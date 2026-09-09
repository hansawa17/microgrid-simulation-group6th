/**
  ******************************************************************************
  * @file    esp8266.c
  * @brief   ESP8266-01S（AT 固件）驱动实现
  *
  * 通过 USART1（PA9/PA10，115200 8N1）与 ESP8266 通信：
  *   - 发送 AT 命令（Esp8266_SendCmd）
  *   - 解析返回事件（OK/ERROR/CONNECT/CLOSED/WIFI GOT IP/...）
  *   - 提取 +IPD 载荷（TCP 数据）供上层 wifi_client 读取
  *
  * 解析在 Esp8266_Poll 中完成（主循环调用），中断里只做单字节收存。
  ******************************************************************************
  */
#include "esp8266.h"
#include "main.h"
#include <string.h>

extern UART_HandleTypeDef huart1;

#define ESP_STREAM_SIZE   1024u
#define ESP_DATA_SIZE     1024u
#define ESP_AT_LINE_SIZE   192u
#define ESP_IPD_HEADER_MAX  32u

/* USART1 中断接收单字节缓冲 */
static uint8_t esp_rx_byte;

/* 原始接收流（ISR 写 head，主循环读 tail；避免并发 memmove 丢字节） */
static uint8_t esp_stream[ESP_STREAM_SIZE];
static volatile uint16_t esp_stream_head = 0u;
static volatile uint16_t esp_stream_tail = 0u;
static volatile uint8_t esp_stream_overflow = 0u;

/* 当前 +IPD 载荷可跨多次 Poll/串口中断到达 */
static uint16_t esp_ipd_remaining = 0u;
static uint8_t esp_ipd_drop = 0u;

/* 累积的 TCP 数据（+IPD 载荷），供上层消费 */
static uint8_t  esp_data[ESP_DATA_SIZE];
static volatile uint16_t esp_data_len = 0u;

/* 连接状态 */
static volatile uint8_t esp_wifi_connected = 0u;
static volatile uint8_t esp_tcp_connected  = 0u;

/* ------------------------------------------------------------------ */
/*  RX（中断）                                                         */
/* ------------------------------------------------------------------ */
void Esp8266_StartRx(void)
{
    HAL_UART_Receive_IT(&huart1, &esp_rx_byte, 1);
}

void Esp8266_OnRxCplt(void)
{
    uint16_t next = (uint16_t)((esp_stream_head + 1u) % ESP_STREAM_SIZE);
    if (next != esp_stream_tail)
    {
        esp_stream[esp_stream_head] = esp_rx_byte;
        esp_stream_head = next;
    }
    else
    {
        esp_stream_overflow = 1u;
    }
    HAL_UART_Receive_IT(&huart1, &esp_rx_byte, 1);
}

/* ------------------------------------------------------------------ */
/*  TX                                                                 */
/* ------------------------------------------------------------------ */
static void esp_send(const uint8_t *p, uint16_t n)
{
    HAL_UART_Transmit(&huart1, (uint8_t *)p, n, 1000);
}

void Esp8266_SendCmd(const char *cmd)
{
    esp_send((const uint8_t *)cmd, (uint16_t)strlen(cmd));
    esp_send((const uint8_t *)"\r\n", 2);
}

void Esp8266_SendRaw(const uint8_t *data, uint16_t len)
{
    esp_send(data, len);
}

/* ------------------------------------------------------------------ */
/*  流消费 / 数据累积                                                  */
/* ------------------------------------------------------------------ */
static uint16_t esp_stream_available(void)
{
    uint16_t head = esp_stream_head;
    uint16_t tail = esp_stream_tail;
    return (head >= tail) ? (uint16_t)(head - tail)
                          : (uint16_t)(ESP_STREAM_SIZE - tail + head);
}

static uint8_t esp_stream_peek(uint16_t offset)
{
    return esp_stream[(uint16_t)((esp_stream_tail + offset) % ESP_STREAM_SIZE)];
}

static void esp_consume(uint16_t n)
{
    uint16_t available = esp_stream_available();
    if (n > available) n = available;
    esp_stream_tail = (uint16_t)((esp_stream_tail + n) % ESP_STREAM_SIZE);
}

static int esp_prefix_is(const char *prefix, uint16_t n)
{
    if (esp_stream_available() < n) return 0;
    for (uint16_t i = 0u; i < n; i++)
        if (esp_stream_peek(i) != (uint8_t)prefix[i]) return 0;
    return 1;
}

static int esp_append_stream_data(uint16_t n)
{
    if ((uint32_t)esp_data_len + n > ESP_DATA_SIZE)
    {
        esp_data_len = 0u;
        return -1;
    }
    for (uint16_t i = 0u; i < n; i++)
        esp_data[esp_data_len + i] = esp_stream_peek(i);
    esp_data_len += n;
    return 0;
}

/* ------------------------------------------------------------------ */
/*  行处理                                                             */
/* ------------------------------------------------------------------ */
#define LINE_IS(lit) ((n) == (uint16_t)(sizeof(lit)-1u) && memcmp(s, (lit), (n)) == 0)

static uint32_t esp_handle_line(const char *s, uint16_t n)
{
    while (n > 0u && (s[n-1u] == '\r' || s[n-1u] == '\n')) n--;   /* 去尾部 \r */
    if (n == 0u) return 0u;

    if (LINE_IS("OK"))                       return ESP_EVT_OK;
    if (LINE_IS("ERROR"))                    return ESP_EVT_ERROR;
    if (LINE_IS("FAIL"))                     return ESP_EVT_FAIL;
    if (LINE_IS("WIFI GOT IP"))              { esp_wifi_connected = 1u; return ESP_EVT_WIFI_GOT_IP; }
    if (LINE_IS("WIFI DISCONNECT"))          { esp_wifi_connected = 0u; esp_tcp_connected = 0u; return ESP_EVT_WIFI_DISCONN; }
    if (LINE_IS("WIFI CONNECTED"))           return ESP_EVT_WIFI_CONNECTED;
    if (LINE_IS("ALREADY CONNECTED"))        { esp_tcp_connected = 1u; return ESP_EVT_ALREADY_CONN; }
    if (LINE_IS("CONNECT"))                  { esp_tcp_connected = 1u; return ESP_EVT_CONNECT; }
    if (LINE_IS("CLOSED"))                   { esp_tcp_connected = 0u; return ESP_EVT_CLOSED; }
    if (LINE_IS("SEND OK"))                  return ESP_EVT_SEND_OK;
    if (LINE_IS("ready"))                    return ESP_EVT_READY;
    if (n >= 4u && memcmp(s, "busy", 4) == 0) return ESP_EVT_BUSY;
    return 0u;
}

/* ------------------------------------------------------------------ */
/*  Poll：解析接收流，返回本次解析出的事件位                            */
/* ------------------------------------------------------------------ */
uint32_t Esp8266_Poll(void)
{
    uint32_t ev = 0u;

    if (esp_stream_overflow)
    {
        esp_stream_overflow = 0u;
        esp_stream_tail = esp_stream_head;
        esp_data_len = 0u;
        esp_ipd_remaining = 0u;
        esp_ipd_drop = 0u;
        ev |= ESP_EVT_RX_OVERFLOW;
    }

    while (esp_stream_available() > 0u)
    {
        uint16_t available = esp_stream_available();

        /* 已读到 +IPD 头：载荷可分多批搬运，不要求一次塞进原始缓冲 */
        if (esp_ipd_remaining > 0u)
        {
            uint16_t take = (available < esp_ipd_remaining) ? available : esp_ipd_remaining;
            if (!esp_ipd_drop && esp_append_stream_data(take) != 0)
            {
                esp_ipd_drop = 1u;
                ev |= ESP_EVT_RX_OVERFLOW;
            }
            esp_consume(take);
            esp_ipd_remaining -= take;
            if (esp_ipd_remaining == 0u) esp_ipd_drop = 0u;
            continue;
        }

        /* CIPSEND 的提示符可能没有前导换行，必须先于普通行处理 */
        if (esp_stream_peek(0u) == '>')
        {
            ev |= ESP_EVT_PROMPT;
            esp_consume(1u);
            if (esp_stream_available() > 0u && esp_stream_peek(0u) == ' ')
                esp_consume(1u);
            continue;
        }

        /* +IPD,<len>: 或 +IPD,<id>,<len>: */
        if (available >= 5u && esp_prefix_is("+IPD,", 5u))
        {
            uint16_t colon = 5u;
            while (colon < available && esp_stream_peek(colon) != ':') colon++;
            if (colon >= available)
            {
                if (available > ESP_IPD_HEADER_MAX)
                {
                    ev |= ESP_EVT_RX_OVERFLOW;
                    esp_consume(1u);
                    continue;
                }
                break;
            }

            uint16_t len_start = 5u;
            for (uint16_t k = 5u; k < colon; k++)
                if (esp_stream_peek(k) == ',') len_start = (uint16_t)(k + 1u);

            uint32_t len = 0u;
            uint8_t valid = (len_start < colon) ? 1u : 0u;
            for (uint16_t k = len_start; k < colon; k++)
            {
                uint8_t ch = esp_stream_peek(k);
                if (ch < '0' || ch > '9') { valid = 0u; break; }
                len = len * 10u + (uint32_t)(ch - '0');
                if (len > 65535u) { valid = 0u; break; }
            }
            if (!valid)
            {
                ev |= ESP_EVT_RX_OVERFLOW;
                esp_consume((uint16_t)(colon + 1u));
                continue;
            }

            esp_consume((uint16_t)(colon + 1u));
            esp_ipd_remaining = (uint16_t)len;
            esp_ipd_drop = ((uint32_t)esp_data_len + len > ESP_DATA_SIZE) ? 1u : 0u;
            if (esp_ipd_drop)
            {
                esp_data_len = 0u;
                ev |= ESP_EVT_RX_OVERFLOW;
            }
            continue;
        }

        /* 普通 AT 行（以 \n 结尾） */
        uint16_t nl = 0u;
        while (nl < available && esp_stream_peek(nl) != '\n') nl++;
        if (nl >= available) break;

        if (nl < ESP_AT_LINE_SIZE)
        {
            char line[ESP_AT_LINE_SIZE];
            for (uint16_t i = 0u; i < nl; i++) line[i] = (char)esp_stream_peek(i);
            ev |= esp_handle_line(line, nl);
        }
        else
        {
            ev |= ESP_EVT_RX_OVERFLOW;
        }
        esp_consume(nl + 1u);
    }

    return ev;
}

/* ------------------------------------------------------------------ */
int Esp8266_ReadData(uint8_t *dst, uint16_t max_len)
{
    if (esp_data_len == 0u) return 0;
    uint16_t n = (esp_data_len < max_len) ? esp_data_len : max_len;
    memcpy(dst, esp_data, n);
    memmove(esp_data, esp_data + n, esp_data_len - n);
    esp_data_len -= n;
    return (int)n;
}

void Esp8266_DiscardData(void)
{
    esp_data_len = 0u;
    esp_ipd_remaining = 0u;
    esp_ipd_drop = 0u;
}

int Esp8266_IsWifiConnected(void) { return (int)esp_wifi_connected; }
int Esp8266_IsTcpConnected(void)  { return (int)esp_tcp_connected; }
