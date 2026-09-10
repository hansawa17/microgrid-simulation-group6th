/**
  ******************************************************************************
  * @file    wind_turbine.c
  * @brief   风力发电机控制器 —— 控制计算与串口协议实现
  ******************************************************************************
  * 控制规则（与 A/B 冻结参数一致，见 docs/parameter-ownership.md）：
  *   1) 可用功率 power_available 由风速按三次方曲线得到：
  *        [0, cut_in)              -> 0（停机）
  *        [cut_in, rated)          -> wind_rated_power_kw * fraction^3
  *        [rated, cut_out)         -> wind_rated_power_kw 恒定
  *        [cut_out, +inf)          -> 0（停机）
  *   2) 启停状态 status：
  *        run_enable 且 cut_in <= wind < cut_out -> 运行(1)，否则停止(0)
  *   3) 桨距角 deg（0-90° 顺桨）：停机/无可用功率 -> 90°；开环 -> 0°；
  *      闭环 -> pitch_feather_deg * (1 - power_set / power_available)，pitch_feather_deg=90。
  *   4) 稳态运行上限 power_operating_limit：运行 -> power_available；停机/保护 -> 0。
  *   5) 实际功率 power_actual：联调后由 A 计算；本地兜底 min(available, set)。
  *
  * 联调时：风速 / B 目标经 Wi-Fi 来自 A；可用功率/稳态上限/桨距由 C 计算后随
  * wind_action 上报 A；实际功率回读 A。
  ******************************************************************************
  */
#include "wind_turbine.h"
#include "esp8266.h"
#include "wifi_client.h"
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
    float    cut_in_speed_mps;
    float    rated_speed_mps;
    float    cut_out_speed_mps;
    float    wind_rated_power_kw;
    float    pitch_feather_deg;
    float    c_control_s;  /* 控制周期 s */
    float    c_timeout_s;    /* 通信超时 s */
    uint32_t parameter_revision;  /* 参数版本：每次 $PARAM2 原子应用后递增 */

    /* 计算输出 */
    float    power_available;        /* 可用功率 kW */
    float    power_operating_limit;  /* 稳态运行上限 kW（联调后由 A 计算） */
    float    power_actual;           /* 实际功率 kW */
    float    deg;                    /* 桨距角   °  */
    uint8_t  status;                 /* 启停 0停止/1运行 */

    /* 运行控制 */
    uint8_t  run_enable;      /* 允许运行（STOP 置 0） */
    uint32_t cycle;           /* 控制周期计数 */
} WT_State_t;

static WT_State_t wt;

/* 上次上报的 A 同步状态（用于 $SYNC 变化检测，首帧必发） */
static uint32_t wt_last_sync_status = 0xFFFFFFFFu;

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

/* 上电横幅：串口助手按 115200 8N1 打开对应 COM 口后复位单片机，应能看到本行。
 * 若能看到横幅但收不到 $WIND，说明发送通路正常、问题在周期任务；若横幅也看不到，
 * 说明是接线（TX/RX/GND）、COM 口或波特率不对。 */
static void wt_send_banner(void)
{
    wt_send("$BOOT,wind_turbine,USART2,115200,8N1\r\n");
}

static void wt_send_ack(const char *type, uint8_t ok)
{
    char buf[32];
    sprintf(buf, "$ACK,%s,%u\r\n", type, (unsigned)ok);
    wt_send(buf);
}

static void wt_send_telemetry(void)
{
    char buf[192];
    char f1[16], f2[16], f3[16], f4[16], f5[16], f6[16];
    int32_t last_seq = WifiClient_GetLastWindActionSeq();

    wt_ftoa2(f1, wt.wind_speed);
    wt_ftoa2(f2, wt.power_available);
    wt_ftoa2(f3, wt.power_operating_limit);
    wt_ftoa2(f4, wt.power_set);
    wt_ftoa2(f5, wt.power_actual);
    wt_ftoa2(f6, wt.deg);

    /* $WIND2 在旧 $WIND 基础上追加 last_wind_action_seq（-1 表示尚无被 A accepted 的 C 动作） */
    sprintf(buf, "$WIND2,%lu,%s,%s,%s,%s,%s,%u,%s,%u,%u,%ld\r\n",
            (unsigned long)wt.cycle,
            f1, f2, f3, f4, f5,
            (unsigned)wt.status,
            f6,
            (unsigned)wt.control_mode,
            (unsigned)(WifiClient_IsOnline() ? 1u : 0u),
            (long)last_seq);
    wt_send(buf);
}

/* ------------------------------------------------------------------ */
/*  参数查询应答：$PARAMGET,<request_id>,<revision>,<8 字段>            */
/* ------------------------------------------------------------------ */
static void wt_send_paramget(uint32_t request_id)
{
    char buf[192];
    char f1[16], f2[16], f3[16], f4[16], f5[16], f6[16], f7[16];

    wt_ftoa2(f1, wt.cut_in_speed_mps);
    wt_ftoa2(f2, wt.rated_speed_mps);
    wt_ftoa2(f3, wt.cut_out_speed_mps);
    wt_ftoa2(f4, wt.wind_rated_power_kw);
    wt_ftoa2(f5, wt.pitch_feather_deg);
    wt_ftoa2(f6, wt.c_control_s);
    wt_ftoa2(f7, wt.c_timeout_s);

    sprintf(buf, "$PARAMGET,%lu,%lu,%s,%s,%s,%s,%s,%s,%s,%u\r\n",
            (unsigned long)request_id,
            (unsigned long)wt.parameter_revision,
            f1, f2, f3, f4, f5, f6, f7,
            (unsigned)wt.control_mode);
    wt_send(buf);
}

/* ------------------------------------------------------------------ */
/*  A 副本同步状态上报：$SYNC,<a_sync_status>,<a_sync_seq>,<reason>     */
/* ------------------------------------------------------------------ */
static void wt_send_sync(uint32_t status, int32_t seq, const char *reason)
{
    char buf[128];
    if (reason == NULL) reason = "";
    sprintf(buf, "$SYNC,%lu,%ld,%s\r\n", (unsigned long)status, (long)seq, reason);
    wt_send(buf);
}

/* ------------------------------------------------------------------ */
/*  排队向 A 同步 C 物理参数白名单（control_mode 不发送）               */
/* ------------------------------------------------------------------ */
static void wt_queue_parameter_update(void)
{
    char params[256];
    char f1[16], f2[16], f3[16], f4[16], f5[16], f6[16], f7[16];

    wt_ftoa2(f1, wt.cut_in_speed_mps);
    wt_ftoa2(f2, wt.rated_speed_mps);
    wt_ftoa2(f3, wt.cut_out_speed_mps);
    wt_ftoa2(f4, wt.wind_rated_power_kw);
    wt_ftoa2(f5, wt.pitch_feather_deg);
    wt_ftoa2(f6, wt.c_control_s);
    wt_ftoa2(f7, wt.c_timeout_s);

    snprintf(params, sizeof(params),
             "{\"wind_rated_power_kw\":%s,\"cut_in_speed_mps\":%s,"
             "\"rated_speed_mps\":%s,\"cut_out_speed_mps\":%s,"
             "\"pitch_feather_deg\":%s,\"c_control_s\":%s,\"c_timeout_s\":%s}",
             f4, f1, f2, f3, f5, f6, f7);
    WifiClient_QueueParameterUpdate(params);
}

/* ------------------------------------------------------------------ */
/*  控制计算                                                           */
/* ------------------------------------------------------------------ */
static float wt_power_available(void)
{
    float w = wt.wind_speed;
    if (w < wt.cut_in_speed_mps)
    {
        return 0.0f;
    }
    else if (w < wt.rated_speed_mps)
    {
        /* 切入~额定：按归一化风速的三次方增加（与 A 一致） */
        float fraction = (w - wt.cut_in_speed_mps) / (wt.rated_speed_mps - wt.cut_in_speed_mps);
        return wt.wind_rated_power_kw * fraction * fraction * fraction;
    }
    else if (w < wt.cut_out_speed_mps)
    {
        return wt.wind_rated_power_kw;              /* 额定~切出：恒为额定功率 */
    }
    else
    {
        return 0.0f;                        /* 超过切出：停机 */
    }
}

/* 计算启停状态 + 桨距目标（C 的职责；输入 wind_speed/power_set/power_available 已就绪） */
static void wt_compute_status_pitch(void)
{
    if (wt.run_enable &&
        wt.wind_speed >= wt.cut_in_speed_mps &&
        wt.wind_speed <= wt.cut_out_speed_mps)
    {
        wt.status = WT_STATUS_RUN;
    }
    else
    {
        wt.status = WT_STATUS_STOP;
    }

    if (wt.status == WT_STATUS_STOP || wt.power_available <= 0.0f)
    {
        wt.deg = wt.pitch_feather_deg;                 /* 停机/无可用功率：顺桨 */
    }
    else if (wt.control_mode == WT_MODE_OPEN_LOOP)
    {
        wt.deg = 0.0f;                       /* 开环：最大功率捕获 */
    }
    else /* 闭环 */
    {
        if (wt.power_set >= wt.power_available)
        {
            wt.deg = 0.0f;                   /* 无法满足设定，满发 */
        }
        else
        {
            wt.deg = wt.pitch_feather_deg * (1.0f - wt.power_set / wt.power_available);
        }
    }
}

/* 稳态运行上限：运行许可且无保护时 = 可用功率，否则 0（不扣桨距/目标，避免 B 限功率自锁） */
static void wt_compute_operating_limit(void)
{
    if (wt.status == WT_STATUS_RUN)
    {
        wt.power_operating_limit = wt.power_available;
    }
    else
    {
        wt.power_operating_limit = 0.0f;
    }
}

/* ------------------------------------------------------------------ */
/*  复位默认值（不打断串口接收）                                       */
/* ------------------------------------------------------------------ */
static void wt_reset_defaults(void)
{
    wt.wind_speed      = 0.0f;
    wt.power_set       = 0.0f;
    wt.control_mode    = WT_MODE_CLOSED_LOOP;
    wt.cut_in_speed_mps    = WT_DEFAULT_CUT_IN_SPEED_MPS;
    wt.rated_speed_mps     = WT_DEFAULT_RATED_SPEED_MPS;
    wt.cut_out_speed_mps   = WT_DEFAULT_CUT_OUT_SPEED_MPS;
    wt.wind_rated_power_kw     = WT_DEFAULT_WIND_RATED_POWER_KW;
    wt.pitch_feather_deg         = WT_DEFAULT_PITCH_FEATHER_DEG;
    wt.c_control_s  = WT_DEFAULT_C_CONTROL_S;
    wt.c_timeout_s    = WT_DEFAULT_C_TIMEOUT_S;
    wt.run_enable      = 1u;
    wt.cycle           = 0u;
    wt.parameter_revision   = 0u;
    wt.power_available       = 0.0f;
    wt.power_operating_limit = 0.0f;
    wt.power_actual          = 0.0f;
    wt.deg                   = 0.0f;
    wt.status                = WT_STATUS_STOP;
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
        s = strtok(NULL, ","); if (s != NULL) wt.cut_in_speed_mps    = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.rated_speed_mps     = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.cut_out_speed_mps   = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.wind_rated_power_kw     = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.pitch_feather_deg         = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.c_control_s  = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.c_timeout_s    = (float)atof(s);
        s = strtok(NULL, ","); if (s != NULL) wt.control_mode    = (uint8_t)atoi(s);

        /* 参数合法性保护：防止分母为 0 */
        if (wt.rated_speed_mps <= wt.cut_in_speed_mps) wt.rated_speed_mps = wt.cut_in_speed_mps + 1.0f;

        wt_send_ack("PARAM", 1u);
    }
    else if (strcmp(p, "$PARAM2") == 0)
    {
        /* 原子应用：完整解析并校验全部字段后一次性生效；失败保持旧参数并返回当前有效值 */
        char *s;
        uint32_t request_id;
        float v_cut_in, v_rated, v_cut_out, v_power, v_feather, v_control, v_timeout;
        int v_mode;

        s = strtok(NULL, ","); if (s == NULL) { wt_send_ack("PARAM2", 0u); return; }
        request_id = (uint32_t)strtoul(s, NULL, 10);

        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_cut_in  = (float)atof(s);
        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_rated   = (float)atof(s);
        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_cut_out = (float)atof(s);
        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_power   = (float)atof(s);
        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_feather = (float)atof(s);
        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_control = (float)atof(s);
        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_timeout = (float)atof(s);
        s = strtok(NULL, ","); if (s == NULL) { wt_send_paramget(request_id); return; } v_mode    = (int)atoi(s);

        if (v_cut_in < 0.0f || v_rated <= v_cut_in || v_cut_out <= v_rated ||
            v_power <= 0.0f || v_feather < 0.0f || v_feather > 90.0f ||
            v_control <= 0.0f || v_timeout <= 0.0f ||
            (v_mode != WT_MODE_OPEN_LOOP && v_mode != WT_MODE_CLOSED_LOOP))
        {
            wt_send_ack("PARAM2", 0u);
            wt_send_paramget(request_id);   /* 失败保持旧参数并返回当前有效值 */
            return;
        }

        wt.cut_in_speed_mps    = v_cut_in;
        wt.rated_speed_mps     = v_rated;
        wt.cut_out_speed_mps   = v_cut_out;
        wt.wind_rated_power_kw = v_power;
        wt.pitch_feather_deg   = v_feather;
        wt.c_control_s         = v_control;
        wt.c_timeout_s         = v_timeout;
        wt.control_mode        = (uint8_t)v_mode;
        wt.parameter_revision++;

        wt_send_ack("PARAM2", 1u);
        wt_send_paramget(request_id);

        /* MCU 确认生效后，在 TCP 单未决事务安全空档排队同步 C 白名单参数到 A */
        wt_queue_parameter_update();
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
    else if (strcmp(p, "$WIFI") == 0)
    {
        /* 上位机下发 A 服务端地址/端口：$WIFI,<ip>,<port>，改后自动重连 */
        char *ip = strtok(NULL, ",");
        char *port_s = strtok(NULL, ",");
        if (ip != NULL && port_s != NULL && ip[0] != '\0')
        {
            char *end = NULL;
            unsigned long port = strtoul(port_s, &end, 10);
            if (end != port_s && port >= 1ul && port <= 65535ul)
            {
                WifiClient_SetServer(ip, (uint16_t)port);
                wt_send_ack("WIFI", 1u);
            }
            else
            {
                wt_send_ack("WIFI", 0u);
            }
        }
        else
        {
            wt_send_ack("WIFI", 0u);
        }
    }
    else if (strcmp(p, "$WIFI?") == 0)
    {
        /* 查询当前 A 服务端地址/端口：应答 $WIFIGET,<ip>,<port> */
        char buf[96];
        sprintf(buf, "$WIFIGET,%s,%u\r\n",
                WifiClient_GetServerIp(), (unsigned)WifiClient_GetServerPort());
        wt_send(buf);
    }
    else if (strcmp(p, "$PARAM?") == 0)
    {
        /* 旧查询（兼容保留）：应答 $PARAMGET,<request_id=0>,<revision>,<8 字段> */
        wt_send_paramget(0u);
    }
    else if (strcmp(p, "$PARAMGET?") == 0)
    {
        /* 查询当前风机参数：应答 $PARAMGET,<request_id>,<revision>,<8 字段> */
        char *s = strtok(NULL, ",");
        uint32_t request_id = (s != NULL) ? (uint32_t)strtoul(s, NULL, 10) : 0u;
        wt_send_paramget(request_id);
    }
}

/* ------------------------------------------------------------------ */
/*  对外接口实现                                                       */
/* ------------------------------------------------------------------ */
void WindTurbine_Init(void)
{
    wt_reset_defaults();
    wt_send_banner();
}

void WindTurbine_StartRx(void)
{
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

uint32_t WindTurbine_GetPeriodMs(void)
{
    uint32_t ms = (uint32_t)(wt.c_control_s * 1000.0f);
    if (ms < 50u)    ms = 50u;      /* 最小 50ms */
    if (ms > 10000u) ms = 10000u;   /* 最大 10s   */
    return ms;
}

uint32_t WindTurbine_GetTimeoutMs(void)
{
    uint32_t ms = (uint32_t)(wt.c_timeout_s * 1000.0f);
    if (ms < 500u)   ms = 500u;
    if (ms > 30000u) ms = 30000u;
    return ms;
}

void WindTurbine_PeriodicTask(void)
{
    if (!WifiClient_IsOnline())
    {
        /* 未连接 A 时不生成伪造的风速/功率数据。 */
        wt.wind_speed = 0.0f;
        wt.power_set  = 0.0f;
        wt.power_available       = 0.0f;
        wt.power_operating_limit = 0.0f;
        wt.power_actual          = 0.0f;
        wt.deg                   = 0.0f;
        wt.status                = WT_STATUS_STOP;
    }
    else if (WifiClient_HasState())
    {
        wt.wind_speed = WifiClient_GetWindSpeedMps();
        wt.power_set  = WifiClient_GetWindTargetKw();
        wt.power_available = wt_power_available();   /* C 计算可用功率（三次方） */
        wt_compute_status_pitch();                   /* 算启停 + 桨距（0-90°） */
        wt_compute_operating_limit();                /* 算稳态上限 */
        WifiClient_SendWindAction(wt.run_enable, wt.deg,
                                  wt.power_available, wt.power_operating_limit);
        wt.power_actual = WifiClient_GetWindActualKw();
    }
    /* 在线但等待 state/ack 时保持上一状态；Wi-Fi 状态机继续推进事务。 */

    /* 上报 A 副本同步状态变化（$SYNC），状态无变化时不重复发送 */
    {
        uint32_t sync_status = WifiClient_GetParamSyncStatus();
        if (sync_status != wt_last_sync_status)
        {
            wt_last_sync_status = sync_status;
            wt_send_sync(sync_status, WifiClient_GetParamSyncSeq(), WifiClient_GetParamSyncReason());
        }
    }

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
    else if (huart->Instance == USART1)
    {
        Esp8266_OnRxCplt();
    }
}
