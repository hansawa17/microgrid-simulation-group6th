/**
  ******************************************************************************
  * @file    wifi_client.c
  * @brief   Wi-Fi/TCP 客户端实现（连 A 电网模拟器）
  *
  * 协议：UTF-8 JSON Lines；STM32(source=C)按
  * state_request -> state -> wind_action -> ack 的单未决事务运行。
  ******************************************************************************
  */
#include "wifi_client.h"
#include "esp8266.h"
#include "wifi_config.h"
#include "main.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WF_FRAME_SIZE             1024u
#define WF_RX_LINE_SIZE           1024u
#define WF_AT_TIMEOUT_MS          3000u
#define WF_JOIN_TIMEOUT_MS       15000u
#define WF_CONNECT_TIMEOUT_MS    10000u
#define WF_CLOSE_TIMEOUT_MS       2000u
#define WF_PROMPT_TIMEOUT_MS      1000u
#define WF_SEND_OK_TIMEOUT_MS     2000u
#define WF_RESPONSE_TIMEOUT_MS    3000u

/* ------------------------------------------------------------------ */
/*  A 服务端地址/端口（运行期可配，默认值来自 wifi_config.h）           */
/* ------------------------------------------------------------------ */
#define WF_HOST_LEN                48u
static char     g_server_ip[WF_HOST_LEN] = WIFI_SERVER_IP;
static uint16_t g_server_port            = WIFI_SERVER_PORT;
static volatile uint8_t g_reconnect_requested = 0u;

typedef enum {
    WF_AT_SYNC,
    WF_ECHO_OFF,
    WF_SET_MODE,
    WF_JOIN_AP,
    WF_CONNECT_TCP,
    WF_CLOSE_TCP,
    WF_ONLINE
} wf_state_t;

typedef enum {
    WF_TX_IDLE,
    WF_TX_WAIT_PROMPT,
    WF_TX_WAIT_SEND_OK
} wf_tx_state_t;

typedef enum {
    WF_MSG_NONE,
    WF_MSG_STATE_REQUEST,
    WF_MSG_WIND_ACTION,
    WF_MSG_PARAMETER_UPDATE
} wf_message_kind_t;

typedef enum {
    WF_APP_IDLE,
    WF_APP_WAIT_STATE,
    WF_APP_WAIT_ACK
} wf_app_state_t;

typedef struct {
    char     session_id[64];
    uint32_t seq;
    uint32_t step;
    float    sim_time_s;
    float    wind_speed_mps;
    float    wind_available_kw;
    float    wind_operating_limit_kw;
    float    wind_target_kw;
    float    wind_actual_kw;
    uint8_t  wind_running;
    uint8_t  fault;
    uint8_t  got_state;
    int32_t  last_wind_action_seq;   /* 最近一次被 A accepted 的 wind_action.seq（-1=无） */
} WfState_t;

static WfState_t wf;
static wf_state_t wf_state = WF_AT_SYNC;
static uint32_t wf_state_tick = 0u;

static wf_tx_state_t wf_tx_state = WF_TX_IDLE;
static wf_message_kind_t wf_tx_kind = WF_MSG_NONE;
static char wf_tx_frame[WF_FRAME_SIZE];
static uint16_t wf_tx_len = 0u;
static uint32_t wf_tx_seq = 0u;
static uint32_t wf_tx_tick = 0u;

static wf_app_state_t wf_app_state = WF_APP_IDLE;
static uint32_t wf_response_tick = 0u;

/* wind_action 在收到匹配 ACK 前保留；断线且 A 会话未变时原 seq 重发。 */
static char wf_pending_frame[WF_FRAME_SIZE];
static char wf_pending_session[64];
static uint16_t wf_pending_len = 0u;
static uint32_t wf_pending_seq = 0u;
static uint8_t wf_pending_valid = 0u;
static uint8_t wf_resend_pending = 0u;

static char wf_rx_line[WF_RX_LINE_SIZE];
static uint16_t wf_rx_len = 0u;
static uint8_t wf_rx_discarding = 0u;

/* parameter_update（C 白名单物理参数）队列与 A 同步状态；在单未决事务安全空档发送 */
static char wf_param_update_payload[256];
static uint8_t wf_param_update_queued = 0u;
static uint32_t wf_psync_status = WF_PSYNC_NONE;
static uint32_t wf_psync_seq = 0u;
static char wf_psync_reason[32];

/* 当前等待 ACK 的消息类型（区分 wind_action / parameter_update） */
static wf_message_kind_t wf_ack_kind = WF_MSG_NONE;

static void ftoa2(char *dst, float v)
{
    int ip, fp;
    if (v < 0.0f) { *dst++ = '-'; v = -v; }
    ip = (int)v;
    fp = (int)((v - (float)ip) * 100.0f + 0.5f);
    if (fp >= 100) { fp -= 100; ip += 1; }
    sprintf(dst, "%d.%02d", ip, fp);
}

static const char *json_find(const char *s, const char *key)
{
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\":", key);
    const char *p = strstr(s, pat);
    return p ? (p + strlen(pat)) : NULL;
}

static int json_get_u32(const char *s, const char *key, uint32_t *out)
{
    const char *p = json_find(s, key);
    char *end = NULL;
    unsigned long value;
    if (!p || *p < '0' || *p > '9') return -1;
    value = strtoul(p, &end, 10);
    if (end == p || value > 0xFFFFFFFFul) return -1;
    *out = (uint32_t)value;
    return 0;
}

static int json_get_float(const char *s, const char *key, float *out)
{
    const char *p = json_find(s, key);
    char *end = NULL;
    float value;
    if (!p) return -1;
    value = strtof(p, &end);
    if (end == p || !isfinite(value) || value < 0.0f) return -1;
    *out = value;
    return 0;
}

static int json_get_bool(const char *s, const char *key, uint8_t *out)
{
    const char *p = json_find(s, key);
    if (!p) return -1;
    if (strncmp(p, "true", 4) == 0) { *out = 1u; return 0; }
    if (strncmp(p, "false", 5) == 0) { *out = 0u; return 0; }
    return -1;
}

static int json_get_string(const char *s, const char *key, char *out, size_t n)
{
    const char *p = json_find(s, key);
    size_t i = 0u;
    if (!p || *p != '"' || n == 0u) return -1;
    p++;
    while (*p && *p != '"')
    {
        if (i + 1u >= n) return -1;
        out[i++] = *p++;
    }
    if (*p != '"') return -1;
    out[i] = '\0';
    return (i > 0u) ? 0 : -1;
}

static void wf_reset_transport(void)
{
    wf_tx_state = WF_TX_IDLE;
    wf_tx_kind = WF_MSG_NONE;
    wf_tx_len = 0u;
    wf_app_state = WF_APP_IDLE;
    wf_response_tick = 0u;
    wf.got_state = 0u;
    wf_rx_len = 0u;
    wf_rx_discarding = 0u;
    wf_resend_pending = 0u;
    wf_ack_kind = WF_MSG_NONE;
    /* 已发送但未收到 ACK 的 parameter_update 因断线/超时而 ACK 未知 */
    if (wf_psync_status == WF_PSYNC_SENT)
    {
        wf_psync_status = WF_PSYNC_UNKNOWN;
        strncpy(wf_psync_reason, "ack_unknown", sizeof(wf_psync_reason) - 1u);
        wf_psync_reason[sizeof(wf_psync_reason) - 1u] = '\0';
    }
    Esp8266_DiscardData();
}

static void wf_enter(wf_state_t state)
{
    char cmd[160];
    wf_state = state;
    wf_state_tick = HAL_GetTick();
    switch (state)
    {
    case WF_AT_SYNC:
        Esp8266_SendCmd("AT");
        break;
    case WF_ECHO_OFF:
        Esp8266_SendCmd("ATE0");
        break;
    case WF_SET_MODE:
        Esp8266_SendCmd("AT+CWMODE=1");
        break;
    case WF_JOIN_AP:
        snprintf(cmd, sizeof(cmd), "AT+CWJAP=\"%s\",\"%s\"", WIFI_SSID, WIFI_PASSWORD);
        Esp8266_SendCmd(cmd);
        break;
    case WF_CONNECT_TCP:
        snprintf(cmd, sizeof(cmd), "AT+CIPSTART=\"TCP\",\"%s\",%u",
                 g_server_ip, (unsigned)g_server_port);
        Esp8266_SendCmd(cmd);
        break;
    case WF_CLOSE_TCP:
        Esp8266_SendCmd("AT+CIPCLOSE");
        break;
    case WF_ONLINE:
        wf_reset_transport();
        break;
    default:
        break;
    }
}

static void wf_reconnect(void)
{
    wf_reset_transport();
    if (Esp8266_IsWifiConnected()) wf_enter(WF_CLOSE_TCP);
    else wf_enter(WF_JOIN_AP);
}

static int wf_start_frame(const char *frame, uint16_t len,
                          wf_message_kind_t kind, uint32_t seq)
{
    char cmd[32];
    if (wf_state != WF_ONLINE || wf_tx_state != WF_TX_IDLE ||
        len == 0u || len >= WF_FRAME_SIZE) return -1;
    memcpy(wf_tx_frame, frame, len);
    wf_tx_frame[len] = '\0';
    wf_tx_len = len;
    wf_tx_kind = kind;
    wf_tx_seq = seq;
    snprintf(cmd, sizeof(cmd), "AT+CIPSEND=%u", (unsigned)(len + 1u));
    Esp8266_SendCmd(cmd);
    wf_tx_state = WF_TX_WAIT_PROMPT;
    wf_tx_tick = HAL_GetTick();
    return 0;
}

static int wf_build_envelope(char *out, size_t out_size, const char *type,
                             const char *payload_json, uint32_t seq)
{
    char session[80];
    char sim_time[20];
    if (wf.session_id[0] == '\0') strcpy(session, "null");
    else snprintf(session, sizeof(session), "\"%s\"", wf.session_id);
    ftoa2(sim_time, wf.sim_time_s);
    return snprintf(out, out_size,
        "{\"version\":1,\"type\":\"%s\",\"source\":\"C\",\"target\":\"A\","
        "\"session_id\":%s,\"seq\":%lu,\"step\":%lu,\"sim_time_s\":%s,\"payload\":%s}",
        type, session, (unsigned long)seq, (unsigned long)wf.step,
        sim_time, payload_json);
}

static int wf_send_state_request(void)
{
    char frame[WF_FRAME_SIZE];
    int n = wf_build_envelope(frame, sizeof(frame), "state_request",
                              "{\"full\":true}", wf.seq);
    if (n <= 0 || (size_t)n >= sizeof(frame)) return -1;
    return wf_start_frame(frame, (uint16_t)n, WF_MSG_STATE_REQUEST, wf.seq);
}

static int wf_send_parameter_update(void)
{
    char payload[320];
    char frame[WF_FRAME_SIZE];
    int n;
    snprintf(payload, sizeof(payload), "{\"parameters\":%s}", wf_param_update_payload);
    n = wf_build_envelope(frame, sizeof(frame), "parameter_update", payload, wf.seq);
    if (n <= 0 || (size_t)n >= sizeof(frame)) return -1;
    wf_psync_status = WF_PSYNC_SENT;
    wf_psync_seq = wf.seq;
    wf_psync_reason[0] = '\0';
    return wf_start_frame(frame, (uint16_t)n, WF_MSG_PARAMETER_UPDATE, wf.seq);
}

static int wf_handle_state(const char *line)
{
    char session[64];
    uint32_t step, next_command_seq;
    float sim_time_s, wind_speed, available, operating_limit, target, actual;
    uint8_t running, fault;
    uint8_t session_changed;

    if (strstr(line, "\"type\":\"state\"") == NULL ||
        strstr(line, "\"source\":\"A\"") == NULL ||
        strstr(line, "\"target\":\"C\"") == NULL ||
        json_get_string(line, "session_id", session, sizeof(session)) != 0 ||
        json_get_u32(line, "step", &step) != 0 ||
        json_get_float(line, "sim_time_s", &sim_time_s) != 0 ||
        json_get_float(line, "wind_speed_mps", &wind_speed) != 0 ||
        json_get_float(line, "wind_available_kw", &available) != 0 ||
        json_get_float(line, "wind_operating_limit_kw", &operating_limit) != 0 ||
        json_get_float(line, "wind_target_kw", &target) != 0 ||
        json_get_float(line, "wind_actual_kw", &actual) != 0 ||
        json_get_bool(line, "wind_running", &running) != 0 ||
        json_get_bool(line, "fault", &fault) != 0)
        return -1;

    session_changed = (wf.session_id[0] != '\0' && strcmp(wf.session_id, session) != 0) ? 1u : 0u;
    if (session_changed)
    {
        wf_pending_valid = 0u;
        wf_pending_len = 0u;
        wf.last_wind_action_seq = -1;   /* 新 session 清空动作追踪 */
    }
    strcpy(wf.session_id, session);

    /* A 返回当前会话该 source 的安全序号下界，解决 MCU 重启后 seq 回零。 */
    if (json_get_u32(line, "next_command_seq", &next_command_seq) == 0 &&
        wf.seq < next_command_seq)
        wf.seq = next_command_seq;

    wf.step = step;
    wf.sim_time_s = sim_time_s;
    wf.wind_speed_mps = wind_speed;
    wf.wind_available_kw = available;
    wf.wind_operating_limit_kw = operating_limit;
    wf.wind_target_kw = target;
    wf.wind_actual_kw = actual;
    wf.wind_running = running;
    wf.fault = fault;
    wf_app_state = WF_APP_IDLE;
    wf_response_tick = 0u;

    if (wf_pending_valid && strcmp(wf_pending_session, wf.session_id) == 0)
    {
        wf.got_state = 0u;
        wf_resend_pending = 1u;
    }
    else
    {
        wf.got_state = 1u;
    }
    return 0;
}

static int wf_handle_ack(const char *line)
{
    uint32_t ack_seq;
    uint8_t accepted;
    if (strstr(line, "\"type\":\"ack\"") == NULL ||
        strstr(line, "\"source\":\"A\"") == NULL ||
        strstr(line, "\"target\":\"C\"") == NULL ||
        json_get_u32(line, "ack_seq", &ack_seq) != 0 ||
        json_get_bool(line, "accepted", &accepted) != 0)
        return -1;

    if (wf_app_state != WF_APP_WAIT_ACK)
        return -1;

    if (wf_ack_kind == WF_MSG_WIND_ACTION)
    {
        if (!wf_pending_valid || ack_seq != wf_pending_seq)
            return -1;
    }
    else if (wf_ack_kind == WF_MSG_PARAMETER_UPDATE)
    {
        if (ack_seq != wf_psync_seq)
            return -1;
    }
    else
    {
        return -1;
    }

    if (wf_ack_kind == WF_MSG_WIND_ACTION)
    {
        if (accepted)
            wf.last_wind_action_seq = (int32_t)ack_seq;
        wf_pending_valid = 0u;
        wf_pending_len = 0u;
    }
    else if (wf_ack_kind == WF_MSG_PARAMETER_UPDATE)
    {
        char reason[32];
        if (json_get_string(line, "reason", reason, sizeof(reason)) == 0)
            strncpy(wf_psync_reason, reason, sizeof(wf_psync_reason) - 1u);
        else
            strncpy(wf_psync_reason, "rejected", sizeof(wf_psync_reason) - 1u);
        wf_psync_reason[sizeof(wf_psync_reason) - 1u] = '\0';
        wf_psync_status = accepted ? WF_PSYNC_ACCEPTED : WF_PSYNC_REJECTED;
    }

    wf_ack_kind = WF_MSG_NONE;
    wf_app_state = WF_APP_IDLE;
    wf_response_tick = 0u;
    wf.got_state = 0u;
    return 0;
}

static int wf_handle_json_line(const char *line)
{
    if (strstr(line, "\"type\":\"state\"") != NULL) return wf_handle_state(line);
    if (strstr(line, "\"type\":\"ack\"") != NULL) return wf_handle_ack(line);
    return -1;
}

static int wf_process_rx(void)
{
    uint8_t data[256];
    int n;
    int protocol_error = 0;
    while ((n = Esp8266_ReadData(data, sizeof(data))) > 0)
    {
        for (int i = 0; i < n; i++)
        {
            char ch = (char)data[i];
            if (wf_rx_discarding)
            {
                if (ch == '\n') wf_rx_discarding = 0u;
                continue;
            }
            if (ch == '\n')
            {
                if (wf_rx_len > 0u)
                {
                    if (wf_rx_line[wf_rx_len - 1u] == '\r') wf_rx_len--;
                    wf_rx_line[wf_rx_len] = '\0';
                    if (wf_handle_json_line(wf_rx_line) != 0) protocol_error = -1;
                }
                wf_rx_len = 0u;
            }
            else if (wf_rx_len < WF_RX_LINE_SIZE - 1u)
            {
                wf_rx_line[wf_rx_len++] = ch;
            }
            else
            {
                wf_rx_len = 0u;
                wf_rx_discarding = 1u;
                protocol_error = -1;
            }
        }
    }
    return protocol_error;
}

static void wf_finish_send(void)
{
    if (wf.seq <= wf_tx_seq) wf.seq = wf_tx_seq + 1u;
    if (wf_tx_kind == WF_MSG_STATE_REQUEST)
    {
        wf_app_state = WF_APP_WAIT_STATE;
        wf_response_tick = HAL_GetTick();
    }
    else if (wf_tx_kind == WF_MSG_WIND_ACTION || wf_tx_kind == WF_MSG_PARAMETER_UPDATE)
    {
        wf_app_state = WF_APP_WAIT_ACK;
        wf_response_tick = HAL_GetTick();
    }
    wf_ack_kind = wf_tx_kind;
    wf_tx_state = WF_TX_IDLE;
    wf_tx_kind = WF_MSG_NONE;
    wf_tx_len = 0u;
}

static int wf_tx_task(uint32_t events)
{
    uint32_t now = HAL_GetTick();
    if (wf_tx_state == WF_TX_WAIT_PROMPT)
    {
        if (events & ESP_EVT_PROMPT)
        {
            Esp8266_SendRaw((const uint8_t *)wf_tx_frame, wf_tx_len);
            Esp8266_SendRaw((const uint8_t *)"\n", 1u);
            wf_tx_state = WF_TX_WAIT_SEND_OK;
            wf_tx_tick = now;
        }
        else if ((events & (ESP_EVT_ERROR | ESP_EVT_FAIL | ESP_EVT_BUSY)) ||
                 now - wf_tx_tick > WF_PROMPT_TIMEOUT_MS)
            return -1;
    }
    else if (wf_tx_state == WF_TX_WAIT_SEND_OK)
    {
        if (events & ESP_EVT_SEND_OK) wf_finish_send();
        else if ((events & (ESP_EVT_ERROR | ESP_EVT_FAIL | ESP_EVT_BUSY)) ||
                 now - wf_tx_tick > WF_SEND_OK_TIMEOUT_MS)
            return -1;
    }
    return 0;
}

void WifiClient_Init(void)
{
    memset(&wf, 0, sizeof(wf));
    wf.last_wind_action_seq = -1;
    wf_pending_valid = 0u;
    wf_pending_len = 0u;
    wf_param_update_queued = 0u;
    wf_psync_status = WF_PSYNC_NONE;
    wf_psync_seq = 0u;
    wf_psync_reason[0] = '\0';
    wf_ack_kind = WF_MSG_NONE;
    wf_reset_transport();
    wf_enter(WF_AT_SYNC);
}

void WifiClient_Task(void)
{
    uint32_t events = Esp8266_Poll();
    uint32_t now = HAL_GetTick();

    /* 上位机刚下发了新的 A 地址/端口：在主循环安全点断开并按新配置重连 */
    if (g_reconnect_requested)
    {
        g_reconnect_requested = 0u;
        wf_reconnect();
        return;
    }

    if (events & ESP_EVT_WIFI_DISCONN)
    {
        wf_reset_transport();
        wf_enter(WF_JOIN_AP);
        return;
    }
    if ((events & ESP_EVT_CLOSED) && wf_state != WF_CLOSE_TCP)
    {
        wf_reset_transport();
        wf_enter(WF_CONNECT_TCP);
        return;
    }

    switch (wf_state)
    {
    case WF_AT_SYNC:
        if (events & (ESP_EVT_OK | ESP_EVT_READY)) wf_enter(WF_ECHO_OFF);
        else if (now - wf_state_tick > WF_AT_TIMEOUT_MS) wf_enter(WF_AT_SYNC);
        break;
    case WF_ECHO_OFF:
        if (events & ESP_EVT_OK) wf_enter(WF_SET_MODE);
        else if ((events & ESP_EVT_ERROR) || now - wf_state_tick > WF_AT_TIMEOUT_MS)
            wf_enter(WF_ECHO_OFF);
        break;
    case WF_SET_MODE:
        if (events & ESP_EVT_OK) wf_enter(WF_JOIN_AP);
        else if ((events & ESP_EVT_ERROR) || now - wf_state_tick > WF_AT_TIMEOUT_MS)
            wf_enter(WF_SET_MODE);
        break;
    case WF_JOIN_AP:
        if (events & ESP_EVT_WIFI_GOT_IP) wf_enter(WF_CONNECT_TCP);
        else if ((events & (ESP_EVT_FAIL | ESP_EVT_ERROR)) ||
                 now - wf_state_tick > WF_JOIN_TIMEOUT_MS)
            wf_enter(WF_JOIN_AP);
        break;
    case WF_CONNECT_TCP:
        if (events & (ESP_EVT_CONNECT | ESP_EVT_ALREADY_CONN)) wf_enter(WF_ONLINE);
        else if ((events & (ESP_EVT_ERROR | ESP_EVT_FAIL)) ||
                 now - wf_state_tick > WF_CONNECT_TIMEOUT_MS)
            wf_enter(WF_CLOSE_TCP);
        break;
    case WF_CLOSE_TCP:
        if ((events & (ESP_EVT_CLOSED | ESP_EVT_OK | ESP_EVT_ERROR)) ||
            now - wf_state_tick > WF_CLOSE_TIMEOUT_MS)
            wf_enter(WF_CONNECT_TCP);
        break;
    case WF_ONLINE:
        if (events & ESP_EVT_RX_OVERFLOW)
        {
            wf_reconnect();
            return;
        }
        if (wf_tx_task(events) != 0)
        {
            wf_reconnect();
            return;
        }
        if (wf_process_rx() != 0)
        {
            wf_reconnect();
            return;
        }
        if (wf_app_state != WF_APP_IDLE &&
            now - wf_response_tick > WF_RESPONSE_TIMEOUT_MS)
        {
            wf_reconnect();
            return;
        }
        if (wf_tx_state == WF_TX_IDLE && wf_app_state == WF_APP_IDLE)
        {
            if (wf_resend_pending)
            {
                if (wf_start_frame(wf_pending_frame, wf_pending_len,
                                   WF_MSG_WIND_ACTION, wf_pending_seq) == 0)
                    wf_resend_pending = 0u;
            }
            else if (wf_param_update_queued)
            {
                /* 在单未决事务安全空档发送 parameter_update，不与 state/wind_action/ACK 并发 */
                if (wf_send_parameter_update() == 0)
                    wf_param_update_queued = 0u;
                else
                    wf_reconnect();
            }
            else if (!wf.got_state)
            {
                if (wf_send_state_request() != 0) wf_reconnect();
            }
        }
        break;
    default:
        wf_enter(WF_AT_SYNC);
        break;
    }
}

void WifiClient_SendWindAction(uint8_t wind_enable, float pitch_target_deg,
                               float wind_available_kw, float wind_operating_limit_kw)
{
    char payload[160];
    char frame[WF_FRAME_SIZE];
    char pitch[20], available[20], operating_limit[20];
    int n;

    if (wf_state != WF_ONLINE || !wf.got_state ||
        wf_tx_state != WF_TX_IDLE || wf_app_state != WF_APP_IDLE ||
        wf_pending_valid) return;

    ftoa2(pitch, pitch_target_deg);
    ftoa2(available, wind_available_kw);
    ftoa2(operating_limit, wind_operating_limit_kw);
    snprintf(payload, sizeof(payload),
             "{\"wind_enable\":%s,\"pitch_target_deg\":%s,"
             "\"wind_available_kw\":%s,\"wind_operating_limit_kw\":%s}",
             wind_enable ? "true" : "false", pitch, available, operating_limit);
    n = wf_build_envelope(frame, sizeof(frame), "wind_action", payload, wf.seq);
    if (n <= 0 || (size_t)n >= sizeof(frame)) return;

    memcpy(wf_pending_frame, frame, (size_t)n + 1u);
    strcpy(wf_pending_session, wf.session_id);
    wf_pending_len = (uint16_t)n;
    wf_pending_seq = wf.seq;
    wf_pending_valid = 1u;
    if (wf_start_frame(frame, (uint16_t)n, WF_MSG_WIND_ACTION, wf.seq) == 0)
        wf.got_state = 0u;
    else
        wf_pending_valid = 0u;
}

void WifiClient_SetServer(const char *ip, uint16_t port)
{
    if (ip == NULL || ip[0] == '\0' || port == 0u) return;
    strncpy(g_server_ip, ip, WF_HOST_LEN - 1u);
    g_server_ip[WF_HOST_LEN - 1u] = '\0';
    g_server_port = port;
    g_reconnect_requested = 1u;   /* 延迟到主循环 WifiClient_Task 执行重连，避免在 UART 中断里发 AT */
}

const char *WifiClient_GetServerIp(void)   { return g_server_ip; }
uint16_t    WifiClient_GetServerPort(void) { return g_server_port; }

void WifiClient_QueueParameterUpdate(const char *parameters_json)
{
    if (parameters_json == NULL || parameters_json[0] == '\0') return;
    if (strlen(parameters_json) >= sizeof(wf_param_update_payload)) return;
    strcpy(wf_param_update_payload, parameters_json);
    wf_param_update_queued = 1u;
    wf_psync_status = WF_PSYNC_QUEUED;
    wf_psync_reason[0] = '\0';
}

int32_t WifiClient_GetLastWindActionSeq(void) { return wf.last_wind_action_seq; }

uint32_t WifiClient_GetParamSyncStatus(void)  { return wf_psync_status; }

int32_t WifiClient_GetParamSyncSeq(void)
{
    if (wf_psync_status == WF_PSYNC_NONE || wf_psync_status == WF_PSYNC_QUEUED)
        return -1;
    return (int32_t)wf_psync_seq;
}

const char *WifiClient_GetParamSyncReason(void) { return wf_psync_reason; }

int WifiClient_IsOnline(void)                    { return (wf_state == WF_ONLINE) ? 1 : 0; }
int WifiClient_HasState(void)                    { return (wf.got_state != 0u) ? 1 : 0; }
float WifiClient_GetWindSpeedMps(void)           { return wf.wind_speed_mps; }
float WifiClient_GetWindAvailableKw(void)        { return wf.wind_available_kw; }
float WifiClient_GetWindOperatingLimitKw(void)   { return wf.wind_operating_limit_kw; }
float WifiClient_GetWindTargetKw(void)           { return wf.wind_target_kw; }
float WifiClient_GetWindActualKw(void)           { return wf.wind_actual_kw; }
