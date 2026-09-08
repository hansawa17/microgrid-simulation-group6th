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

#define ESP_STREAM_SIZE  1024u
#define ESP_DATA_SIZE    1024u

/* USART1 中断接收单字节缓冲 */
static uint8_t esp_rx_byte;

/* 原始接收流（未解析） */
static uint8_t  esp_stream[ESP_STREAM_SIZE];
static volatile uint16_t esp_stream_len = 0u;

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
    if (esp_stream_len < ESP_STREAM_SIZE)
    {
        esp_stream[esp_stream_len++] = esp_rx_byte;
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
static void esp_consume(uint16_t n)
{
    if (n == 0u) return;
    if (n >= esp_stream_len) { esp_stream_len = 0u; return; }
    memmove(esp_stream, esp_stream + n, esp_stream_len - n);
    esp_stream_len -= n;
}

static void esp_append_data(const uint8_t *p, uint16_t n)
{
    if (esp_data_len + n > ESP_DATA_SIZE)
    {
        esp_data_len = 0u;                      /* 放不下则丢弃旧数据 */
        if (n > ESP_DATA_SIZE) n = ESP_DATA_SIZE;
    }
    memcpy(esp_data + esp_data_len, p, n);
    esp_data_len += n;
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

    while (esp_stream_len > 0u)
    {
        /* 1) +IPD,<id>,<len>:<data> */
        if (esp_stream_len >= 5u && memcmp(esp_stream, "+IPD,", 5) == 0)
        {
            uint16_t colon = 5u;
            while (colon < esp_stream_len && esp_stream[colon] != ':') colon++;
            if (colon >= esp_stream_len) break;                  /* 等更多字节 */

            uint16_t last_comma = 5u;
            for (uint16_t k = 5u; k < colon; k++)
                if (esp_stream[k] == ',') last_comma = k;

            uint16_t len = 0u;
            for (uint16_t k = last_comma + 1u; k < colon; k++)
                len = len * 10u + (uint16_t)(esp_stream[k] - '0');

            if (esp_stream_len < colon + 1u + len) break;         /* 数据未到齐 */

            esp_append_data(esp_stream + colon + 1u, len);
            esp_consume(colon + 1u + len);
            continue;
        }

        /* 2) 普通行（以 \n 结尾） */
        uint16_t nl = 0u;
        while (nl < esp_stream_len && esp_stream[nl] != '\n') nl++;
        if (nl >= esp_stream_len) break;                          /* 等更多字节 */

        ev |= esp_handle_line((const char *)esp_stream, nl);
        esp_consume(nl + 1u);

        /* 3) CIPSEND 的裸 '>' 提示符（紧随行后，无换行） */
        if (esp_stream_len > 0u && esp_stream[0] == '>')
        {
            ev |= ESP_EVT_PROMPT;
            esp_consume(1u);
        }
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

int Esp8266_IsWifiConnected(void) { return (int)esp_wifi_connected; }
int Esp8266_IsTcpConnected(void)  { return (int)esp_tcp_connected; }
