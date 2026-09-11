/**
  ******************************************************************************
  * @file    esp8266.h
  * @brief   ESP8266-01S（AT 固件）驱动 —— USART1 收发、AT 命令、TCP 数据
  ******************************************************************************
  */
#ifndef __ESP8266_H
#define __ESP8266_H

#include <stdint.h>

/* 事件位（Esp8266_Poll 返回） */
#define ESP_EVT_OK              (1u<<0)
#define ESP_EVT_ERROR           (1u<<1)
#define ESP_EVT_FAIL            (1u<<2)
#define ESP_EVT_WIFI_CONNECTED  (1u<<3)
#define ESP_EVT_WIFI_GOT_IP     (1u<<4)
#define ESP_EVT_WIFI_DISCONN    (1u<<5)
#define ESP_EVT_CONNECT         (1u<<6)
#define ESP_EVT_ALREADY_CONN    (1u<<7)
#define ESP_EVT_CLOSED          (1u<<8)
#define ESP_EVT_SEND_OK         (1u<<9)
#define ESP_EVT_PROMPT          (1u<<10)   /* CIPSEND 的 '>' 提示 */
#define ESP_EVT_READY           (1u<<11)   /* 上电 'ready' */
#define ESP_EVT_BUSY            (1u<<12)   /* busy ... */
#define ESP_EVT_RX_OVERFLOW     (1u<<13)   /* USART/TCP 接收缓冲溢出 */

/* USART1 中断接收（由 HAL_UART_RxCpltCallback 分发调用） */
#define ESP_EVT_UART_ERROR      (1u<<14)
#define ESP_EVT_STA_IP          (1u<<15)

void     Esp8266_StartRx(void);
void     Esp8266_OnRxCplt(void);
void     Esp8266_RecoverRx(void);

/* 发送 AT 命令（自动追加 \r\n） */
void     Esp8266_SendCmd(const char *cmd);

/* 发送原始字节（用于 CIPSEND 后的数据） */
void     Esp8266_SendRaw(const uint8_t *data, uint16_t len);

/* 主循环周期性调用：解析接收流，返回并清空事件位 */
uint32_t Esp8266_Poll(void);

/* 读取累积的 TCP 数据（+IPD 载荷），消费式；返回读到的字节数 */
int      Esp8266_ReadData(uint8_t *dst, uint16_t max_len);

/* 丢弃尚未被上层消费的 TCP 数据；协议错误或重连时调用 */
void     Esp8266_DiscardData(void);

/* 连接状态 */
int      Esp8266_IsWifiConnected(void);
int      Esp8266_IsTcpConnected(void);

#endif /* __ESP8266_H */
