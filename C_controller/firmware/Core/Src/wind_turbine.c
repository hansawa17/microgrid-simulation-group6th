/**
  ******************************************************************************
  * @file    wind_turbine.c
  * @brief   风力发电机控制器 —— 控制计算与串口协议实现
  ******************************************************************************
  * 控制规则：
 *   1) 可用功率 power_available 由风速按分段三次曲线得到：
  *        [0, cut_in)              -> 0（停机）
 *        [cut_in, rated)          -> 0 ~ rated_power 三次增加
 *        [rated, cut_out)         -> rated_power 恒定
 *        [cut_out, +inf)          -> 0（停机）
  *   2) 启停状态 status：
  *        run_enable 且 cut_in <= wind <= cut_out -> 运行(1)，否则停止(0)
  *   3) 桨距角 deg（闭环）：power_set < power_available 时线性变桨限功率，
  *      deg = deg_max * (1 - power_set / power_available)；
 *      power_set >= power_available 或开环运行时 deg = 0；停机时顺桨至 deg_max。
  *   4) 实际功率 power_actual：
  *        停机 -> 0；开环 -> power_available；闭环 -> min(power_available, power_set)
  *
  * 本阶段：风速 / 有功设定（原本经 Wi-Fi 由 A 电网模拟器下发）由内部随机模拟，
  *        控制模式默认闭环，可由上位机下发切换。
  ******************************************************************************
  */
#include "wind_turbine.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

extern UART_HandleTypeDef huart2;

/* ------------------------------------------------------------------ */
/*  风机状态寄存器                                                     */
/* ------------------------------------------------------------------ */
typedef struct
{
    /* 输入（真实系统来自 A，本阶段内部随机模拟） */
    float    wind_speed;      /* 风速        m/s */
    float    power_set;       /* 有功设定    kW  */
    uint8_t  control_mode;    /* 控制模式 0开环/1闭环 */

    /* 参数（上位机可下发修改） */
    float    cut_in_speed;
    float    rated_speed;
    float    cut_out_speed;
    float    rated_power;
    float    deg_max;
    float    control_period;  /* 控制周期 s */
    float    comm_timeout;    /* 通信超时 s */

    /* 计算输出 */
    float    power_available; /* 可用功率 kW */
    float    power_actual;    /* 实际功率 kW */
    float    deg;             /* 桨距角   °  */
    uint8_t  status;          /* 启停 0停止/1运行 */

    /* 运行控制 */
    uint8_t  run_enable;      /* 允许运行（STOP 置 0） */
    uint32_t cycle;           /* 控制周期计数 */
} WT_State_t;

static WT_State_t wt;

/* ------------------------------------------------------------------ */
/*  简单伪随机数（LCG，不依赖 rand() 库，便于确定性调试）              */
/* ------------------------------------------------------------------ */
static uint32_t lcg_seed = 1u;

static uint32_t wt_rand(void)
{
    lcg_seed = lcg_seed * 1103515245UL + 12345UL;
    return (lcg_seed >> 16) & 0x7FFFu;      /* 0 .. 32767 */
}

static float wt_randf(float lo, float hi)
{
    return lo + (hi - lo) * ((float)wt_rand() / 32767.0f);
}

/* ------------------------------------------------------------------ */
/*  串口接收行缓冲                                                     */
/* ------------------------------------------------------------------ */
static uint8_t  rx_byte;
static char     rx_line[128];
static uint16_t rx_len = 0u;

/* ------------------------------------------------------------------ */
/*  浮点 -> 两位小数字符串（避开 newlib-nano 默认无 %f 的问题）         */
/* ------------------------------------------------------------------ */
static void wt_ftoa2(char *dst, float v)
{
    int ip, fp;
    if (v < 0.0f) { *dst++ = '-'; v = -v; }
    ip = (int)v;
    fp = (int)((v - (float)ip) * 100.0f + 0.5f);
    if (fp >= 100) { fp -= 100; ip += 1; }
    sprintf(dst, "%d.%02d", ip, fp);
}

/* ------------------------------------------------------------------ */
/*  发送                                                               */
/* ------------------------------------------------------------------ */
static void wt_send(const char *s)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)s, (uint16_t)strlen(s), 100);
}

static void wt_send_ack(const char *type, uint8_t ok)
{
    char buf[32];
    sprintf(buf, "$ACK,%s,%u\r\n", type, (unsigned)ok);
    wt_send(buf);
}

static void wt_send_telemetry(void)
{
    char buf[160];
    char f1[16], f2[16], f3[16], f4[16], f5[16];

    wt_ftoa2(f1, wt.wind_speed);
    wt_ftoa2(f2, wt.power_available);
    wt_ftoa2(f3, wt.power_set);
    wt_ftoa2(f4, wt.power_actual);
    wt_ftoa2(f5, wt.deg);

    sprintf(buf, "$WIND,%lu,%s,%s,%s,%s,%u,%s,%u\r\n",
            (unsigned long)wt.cycle,
            f1, f2, f3, f4,
            (unsigned)wt.status,
            f5,
            (unsigned)wt.control_mode);
    wt_send(buf);
}

/* ------------------------------------------------------------------ */
/*  控制计算                                                           */
/* ------------------------------------------------------------------ */
static float wt_power_available(void)
{
    float w = wt.wind_speed;
    if (w < wt.cut_in_speed)
    {
        return 0.0f;
    }
    else if (w < wt.rated_speed)
    {
        /* 切入~额定：按归一化风速的三次方增加 */
        float fraction = (w - wt.cut_in_speed) / (wt.rated_speed - wt.cut_in_speed);
        return wt.rated_power * fraction * fraction * fraction;
    }
    else if (w < wt.cut_out_speed)
    {
        return wt.rated_power;              /* 额定~切出：恒为额定功率 */
    }
    else
    {
        return 0.0f;                        /* 超过切出：停机 */
    }
}

static void wt_compute(void)
{
    wt.power_available = wt_power_available();

    /* 启停状态 */
    if (wt.run_enable &&
        wt.wind_speed >= wt.cut_in_speed &&
        wt.wind_speed < wt.cut_out_speed)
    {
        wt.status = WT_STATUS_RUN;
    }
    else
    {
        wt.status = WT_STATUS_STOP;
    }

    /* 桨距角与实际功率 */
    if (wt.status == WT_STATUS_STOP)
    {
        wt.deg = wt.deg_max;
        wt.power_actual = 0.0f;
    }
    else if (wt.control_mode == WT_MODE_OPEN_LOOP)
    {
        wt.deg = 0.0f;                       /* 开环：最大功率捕获 */
        wt.power_actual = wt.power_available;
    }
    else /* 闭环 */
    {
        if (wt.power_set >= wt.power_available)
        {
            wt.deg = 0.0f;                   /* 无法满足设定，满发 */
        }
        else
        {
            wt.deg = wt.deg_max * (1.0f - wt.power_set / wt.power_available);
        }
        wt.power_actual = (wt.power_set < wt.power_available) ? wt.power_set : wt.power_available;
    }
}

/* ------------------------------------------------------------------ */
/*  输入模拟（替代 Wi-Fi 来自 A 的数据）                                */
/* ------------------------------------------------------------------ */
static void wt_simulate_inputs(void)
{
    /* 风速：随机游走，覆盖 [0,30] m/s，可跨越切入/额定/切出全区间 */
    wt.wind_speed += wt_randf(-1.5f, 1.5f);
    if (wt.wind_speed < 0.0f)  wt.wind_speed = 0.0f;
    if (wt.wind_speed > 30.0f) wt.wind_speed = 30.0f;

    /* 有功设定：随机游走，范围 [0, rated_power] kW */
    wt.power_set += wt_randf(-8.0f, 8.0f);
    if (wt.power_set < 0.0f)              wt.power_set = 0.0f;
    if (wt.power_set > wt.rated_power)    wt.power_set = wt.rated_power;
}

/* ------------------------------------------------------------------ */
/*  复位默认值（不打断串口接收）                                       */
/* ------------------------------------------------------------------ */
static void wt_reset_defaults(void)
{
    wt.wind_speed      = 9.0f;
    wt.power_set       = 50.0f;
    wt.control_mode    = WT_MODE_CLOSED_LOOP;
    wt.cut_in_speed    = WT_DEFAULT_CUT_IN_SPEED;
    wt.rated_speed     = WT_DEFAULT_RATED_SPEED;
    wt.cut_out_speed   = WT_DEFAULT_CUT_OUT_SPEED;
    wt.rated_power     = WT_DEFAULT_RATED_POWER;
    wt.deg_max         = WT_DEFAULT_DEG_MAX;
    wt.control_period  = WT_DEFAULT_CONTROL_PERIOD;
    wt.comm_timeout    = WT_DEFAULT_COMM_TIMEOUT;
    wt.run_enable      = 1u;
    wt.cycle           = 0u;
    wt.power_available = 0.0f;
    wt.power_actual    = 0.0f;
    wt.deg             = 0.0f;
    wt.status          = WT_STATUS_STOP;
}

/* ------------------------------------------------------------------ */
/*  帧解析                                                             */
/* ------------------------------------------------------------------ */
static void wt_parse_line(char *line)
{
    char *p;

    if (line[0] != '$') return;

    p = strtok(line, ",");
    if (p == NULL) return;

    if (strcmp(p, "$PARAM") == 0)
    {
        char *s;
        s = strtok(NULL, ","); if (s != NULL) wt.cut_in_speed    = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.rated_speed     = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.cut_out_speed   = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.rated_power     = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.deg_max         = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.control_period  = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.comm_timeout    = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.control_mode    = (uint8_t)atoi(s);

        /* 参数合法性保护：防止分母为 0 */
        if (wt.rated_speed <= wt.cut_in_speed) wt.rated_speed = wt.cut_in_speed + 1.0f;

        wt_send_ack("PARAM", 1u);
    }
    else if (strcmp(p, "$CMD") == 0)
    {
        char *cmd = strtok(NULL, ",");
        if (cmd == NULL) return;

        if      (strcmp(cmd, "START")  == 0) { wt.run_enable = 1u;              wt_send_ack("CMD", 1u); }
        else if (strcmp(cmd, "STOP")   == 0) { wt.run_enable = 0u;              wt_send_ack("CMD", 1u); }
        else if (strcmp(cmd, "RESET")  == 0) { wt_reset_defaults();             wt_send_ack("CMD", 1u); }
        else if (strcmp(cmd, "AUTO")   == 0) { wt.control_mode = WT_MODE_CLOSED_LOOP; wt_send_ack("CMD", 1u); }
        else if (strcmp(cmd, "MANUAL") == 0) { wt.control_mode = WT_MODE_OPEN_LOOP;   wt_send_ack("CMD", 1u); }
        else                                { wt_send_ack("CMD", 0u); }
    }
}

/* ------------------------------------------------------------------ */
/*  对外接口实现                                                       */
/* ------------------------------------------------------------------ */
void WindTurbine_Init(void)
{
    wt_reset_defaults();
    lcg_seed = HAL_GetTick() + 0x5A5A5A5AUL;
    if (lcg_seed == 0u) lcg_seed = 1u;
}

void WindTurbine_StartRx(void)
{
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

uint32_t WindTurbine_GetPeriodMs(void)
{
    uint32_t ms = (uint32_t)(wt.control_period * 1000.0f);
    if (ms < 50u)    ms = 50u;      /* 最小 50ms */
    if (ms > 10000u) ms = 10000u;   /* 最大 10s   */
    return ms;
}

void WindTurbine_PeriodicTask(void)
{
    wt_simulate_inputs();
    wt_compute();
    wt_send_telemetry();
    wt.cycle++;
}

void WindTurbine_OnRxByte(uint8_t byte)
{
    if (byte == '\n')
    {
        if (rx_len > 0u)
        {
            rx_line[rx_len] = '\0';
            if (rx_line[rx_len - 1u] == '\r') rx_line[rx_len - 1u] = '\0';
            wt_parse_line(rx_line);
        }
        rx_len = 0u;
    }
    else if (byte == '\r')
    {
        /* 忽略，等待 '\n' */
    }
    else
    {
        if (rx_len < (uint16_t)(sizeof(rx_line) - 1u))
        {
            rx_line[rx_len++] = (char)byte;
        }
    }
}

/* ------------------------------------------------------------------ */
/*  USART2 接收完成回调（覆盖 HAL 弱函数，收到一字节立即重新装载）      */
/* ------------------------------------------------------------------ */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2)
    {
        WindTurbine_OnRxByte(rx_byte);
        HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
    }
}
