/**
  ******************************************************************************
  * @file    wifi_client.c
  * @brief   Wi-Fi/TCP 客户端实现（连 A 电网模拟器）
  *
  * 协议（common/protocol.md）：UTF-8 JSON，一行一帧，以 LF(\n) 结束。
  * 公共 envelope：version/type/source/target/session_id/seq/step/sim_time_s/payload。
  * STM32 作为 source=C，发 state_request / wind_action，收 state / ack。
  *
  * 本阶段：连接成功后先发 state_request(full=true)，并周期性发 wind_action；
  * wind_turbine 仍用本地随机输入（后续联调再改用 A 下发的 state）。
  ******************************************************************************
  */
#include "wifi_client.h"
#include "esp8266.h"
#include "wifi_config.h"
#include "main.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* ------------------------------------------------------------------ */
/*  连接状态机                                                         */
/* ------------------------------------------------------------------ */
typedef enum {
    WF_RESET,        /* 发 AT，等 OK/ready */
    WF_JOIN_AP,      /* 发 AT+CWJAP，等 WIFI GOT IP */
    WF_CONNECT_TCP,  /* 发 AT+CIPSTART，等 CONNECT */
    WF_ONLINE        /* 已连接：收 state/ack，周期发 wind_action */
} wf_state_t;

typedef struct {
    char     session_id[64];
    uint32_t seq;            /* 发送方会话内递增 */
    uint32_t step;           /* 引用 A 的状态步号 */
    float    sim_time_s;     /* 引用 A 的仿真时间 */

    /* A 下发的 state 快照（供后续控制使用） */
    float    wind_speed_mps;
    float    wind_available_kw;
    float    wind_operating_limit_kw;
    float    wind_target_kw;
    float    wind_actual_kw;
    uint8_t  wind_running;
    uint8_t  fault;
    uint8_t  got_state;
} WfState_t;

static WfState_t wf;
static wf_state_t wf_state = WF_RESET;
static uint32_t   wf_state_tick = 0u;
static uint32_t   wf_pending_events = 0u;

/* JSON 接收行缓冲（LF 分帧） */
static char     wf_rx_line[1024];
static uint16_t wf_rx_len = 0u;

/* ------------------------------------------------------------------ */
/*  工具：float -> 两位小数字符串（避开 newlib-nano 无 %f）              */
/* ------------------------------------------------------------------ */
static void ftoa2(char *dst, float v)
{
    int ip, fp;
    if (v < 0.0f) { *dst++ = '-'; v = -v; }
    ip = (int)v;
    fp = (int)((v - (float)ip) * 100.0f + 0.5f);
    if (fp >= 100) { fp -= 100; ip += 1; }
    sprintf(dst, "%d.%02d", ip, fp);
}

/* ------------------------------------------------------------------ */
/*  JSON 取字段                                                       */
/* ------------------------------------------------------------------ */
static const char *json_find(const char *s, const char *key)
{
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\":", key);
    const char *p = strstr(s, pat);
    return p ? (p + strlen(pat)) : NULL;
}

static long  json_get_int(const char *s, const char *key)   { const char *p = json_find(s, key); return p ? atol(p) : 0L; }
static float json_get_float(const char *s, const char *key) { const char *p = json_find(s, key); return p ? (float)atof(p) : 0.0f; }

static int json_get_bool(const char *s, const char *key, uint8_t *out)
{
    const char *p = json_find(s, key);
    if (!p) return -1;
    *out = (strncmp(p, "true", 4) == 0) ? 1u : 0u;
    return 0;
}

static int json_get_string(const char *s, const char *key, char *out, size_t n)
{
    const char *p = json_find(s, key);
    if (!p || *p != '"') return -1;
    p++;
    size_t i = 0;
    while (*p && *p != '"' && i + 1 < n) out[i++] = *p++;
    out[i] = '\0';
    return 0;
}

/* ------------------------------------------------------------------ */
/*  发送一行 JSON（AT+CIPSEND=<len> 流程）                              */
/* ------------------------------------------------------------------ */
static int wf_send_line(const char *line, uint16_t len)
{
    char cmd[32];
    snprintf(cmd, sizeof(cmd), "AT+CIPSEND=%u", (unsigned)(len + 1u)); /* +1 为 \n */
    Esp8266_SendCmd(cmd);

    uint32_t t0 = HAL_GetTick();
    while (HAL_GetTick() - t0 < 1000u)
    {
        uint32_t ev = Esp8266_Poll();
        wf_pending_events |= ev;                 /* 合并事件，避免丢失 */
        if (ev & ESP_EVT_PROMPT) break;
    }

    Esp8266_SendRaw((const uint8_t *)line, len);
    Esp8266_SendRaw((const uint8_t *)"\n", 1);
    return 0;
}

static int wf_send_envelope(const char *type, const char *payload_json)
{
    char buf[512];
    char sess[80];
    char sim[16];

    if (wf.session_id[0] == '\0') strcpy(sess, "null");
    else snprintf(sess, sizeof(sess), "\"%s\"", wf.session_id);
    ftoa2(sim, wf.sim_time_s);

    int n = snprintf(buf, sizeof(buf),
        "{\"version\":1,\"type\":\"%s\",\"source\":\"C\",\"target\":\"A\","
        "\"session_id\":%s,\"seq\":%lu,\"step\":%lu,\"sim_time_s\":%s,\"payload\":%s}",
        type, sess, (unsigned long)wf.seq, (unsigned long)wf.step, sim, payload_json);
    if (n <= 0 || (size_t)n >= sizeof(buf)) return -1;

    wf.seq++;
    return wf_send_line(buf, (uint16_t)n);
}

static void wf_send_state_request(void)
{
    wf_send_envelope("state_request", "{\"full\":true}");
}

/* ------------------------------------------------------------------ */
/*  解析收到的 JSON 行                                                */
/* ------------------------------------------------------------------ */
static void wf_handle_state(const char *line)
{
    json_get_string(line, "session_id", wf.session_id, sizeof(wf.session_id));
    wf.step        = (uint32_t)json_get_int(line, "step");
    wf.sim_time_s  = json_get_float(line, "sim_time_s");
    wf.wind_speed_mps       = json_get_float(line, "wind_speed_mps");
    wf.wind_available_kw    = json_get_float(line, "wind_available_kw");
    wf.wind_operating_limit_kw = json_get_float(line, "wind_operating_limit_kw");
    wf.wind_target_kw       = json_get_float(line, "wind_target_kw");
    wf.wind_actual_kw       = json_get_float(line, "wind_actual_kw");
    json_get_bool(line, "wind_running", &wf.wind_running);
    json_get_bool(line, "fault", &wf.fault);
    wf.got_state = 1u;
}

static void wf_handle_json_line(const char *line)
{
    if (strstr(line, "\"type\":\"state\"") != NULL)
    {
        wf_handle_state(line);
    }
    /* ack 目前只用于确认，不需要额外处理 */
}

static void wf_process_rx(void)
{
    uint8_t tmp[256];
    int n;
    while ((n = Esp8266_ReadData(tmp, sizeof(tmp))) > 0)
    {
        for (int i = 0; i < n; i++)
        {
            char c = (char)tmp[i];
            if (c == '\n')
            {
                if (wf_rx_len > 0u)
                {
                    wf_rx_line[wf_rx_len] = '\0';
                    wf_handle_json_line(wf_rx_line);
                }
                wf_rx_len = 0u;
            }
            else if (wf_rx_len < sizeof(wf_rx_line) - 1u)
            {
                wf_rx_line[wf_rx_len++] = c;
            }
        }
    }
}

/* ------------------------------------------------------------------ */
/*  进入某个状态（进入时发送对应命令）                                  */
/* ------------------------------------------------------------------ */
static void wf_enter(wf_state_t s)
{
    wf_state = s;
    wf_state_tick = HAL_GetTick();

    switch (s)
    {
    case WF_RESET:
        Esp8266_SendCmd("AT");
        break;
    case WF_JOIN_AP:
        {
            char cmd[128];
            snprintf(cmd, sizeof(cmd), "AT+CWJAP=\"%s\",\"%s\"", WIFI_SSID, WIFI_PASSWORD);
            Esp8266_SendCmd(cmd);
        }
        break;
    case WF_CONNECT_TCP:
        {
            char cmd[64];
            snprintf(cmd, sizeof(cmd), "AT+CIPSTART=\"TCP\",\"%s\",%u", WIFI_SERVER_IP, (unsigned)WIFI_SERVER_PORT);
            Esp8266_SendCmd(cmd);
        }
        break;
    default:
        break;
    }
}

/* ------------------------------------------------------------------ */
/*  对外接口                                                           */
/* ------------------------------------------------------------------ */
void WifiClient_Init(void)
{
    memset(&wf, 0, sizeof(wf));
    wf_state = WF_RESET;
    wf_state_tick = 0u;
    wf_pending_events = 0u;
    wf_rx_len = 0u;
    wf_enter(WF_RESET);
}

void WifiClient_Task(void)
{
    uint32_t ev = Esp8266_Poll() | wf_pending_events;
    wf_pending_events = 0u;

    /* 掉线处理（任意状态） */
    if (ev & ESP_EVT_WIFI_DISCONN)
    {
        wf_enter(WF_JOIN_AP);
        return;
    }
    if (ev & ESP_EVT_CLOSED)
    {
        wf_enter(WF_CONNECT_TCP);
        return;
    }

    switch (wf_state)
    {
    case WF_RESET:
        if (ev & (ESP_EVT_OK | ESP_EVT_READY))
        {
            Esp8266_SendCmd("ATE0");          /* 关回显 */
            Esp8266_SendCmd("AT+CWMODE=1");   /* STA 模式 */
            wf_enter(WF_JOIN_AP);
        }
        else if (HAL_GetTick() - wf_state_tick > 3000u)
        {
            wf_enter(WF_RESET);               /* 重发 AT */
        }
        break;

    case WF_JOIN_AP:
        if (ev & ESP_EVT_WIFI_GOT_IP)
        {
            wf_enter(WF_CONNECT_TCP);
        }
        else if ((ev & (ESP_EVT_FAIL | ESP_EVT_ERROR)) || (HAL_GetTick() - wf_state_tick > 15000u))
        {
            wf_enter(WF_JOIN_AP);             /* 重试入网 */
        }
        break;

    case WF_CONNECT_TCP:
        if (ev & (ESP_EVT_CONNECT | ESP_EVT_ALREADY_CONN))
        {
            wf.session_id[0] = '\0';
            wf.seq = 0u;
            wf.step = 0u;
            wf.sim_time_s = 0.0f;
            wf.got_state = 0u;
            wf_state = WF_ONLINE;
            wf_state_tick = HAL_GetTick();
            wf_send_state_request();          /* 立即请求全量状态 */
        }
        else if ((ev & (ESP_EVT_ERROR | ESP_EVT_CLOSED)) || (HAL_GetTick() - wf_state_tick > 10000u))
        {
            wf_enter(WF_CONNECT_TCP);         /* 重试连接 */
        }
        break;

    case WF_ONLINE:
        wf_process_rx();                      /* 消费累积的 TCP 数据 */
        break;

    default:
        break;
    }
}

void WifiClient_SendWindAction(uint8_t wind_enable, float pitch_target_deg,
                               float wind_available_kw, float wind_operating_limit_kw)
{
    if (wf_state != WF_ONLINE) return;
    char payload[128];
    char p[16], a[16], o[16];
    ftoa2(p, pitch_target_deg);
    ftoa2(a, wind_available_kw);
    ftoa2(o, wind_operating_limit_kw);
    snprintf(payload, sizeof(payload),
             "{\"wind_enable\":%s,\"pitch_target_deg\":%s,\"wind_available_kw\":%s,\"wind_operating_limit_kw\":%s}",
             wind_enable ? "true" : "false", p, a, o);
    wf_send_envelope("wind_action", payload);
}

int WifiClient_IsOnline(void)
{
    return (wf_state == WF_ONLINE) ? 1 : 0;
}

int   WifiClient_HasState(void)                  { return (wf.got_state != 0u) ? 1 : 0; }
float WifiClient_GetWindSpeedMps(void)           { return wf.wind_speed_mps; }
float WifiClient_GetWindAvailableKw(void)        { return wf.wind_available_kw; }
float WifiClient_GetWindOperatingLimitKw(void)   { return wf.wind_operating_limit_kw; }
float WifiClient_GetWindTargetKw(void)           { return wf.wind_target_kw; }
float WifiClient_GetWindActualKw(void)           { return wf.wind_actual_kw; }
