/**
  ******************************************************************************
  * @file    wifi_client.h
  * @brief   Wi-Fi/TCP 客户端 —— 连 A 电网模拟器（JSON Lines，source=C）
  ******************************************************************************
  * 拓扑：A 是 TCP 服务端，STM32/Wi-Fi 是 TCP 客户端（source=C）。
  * 本层负责：连接状态机、JSON 报文组帧/解析、state_request / wind_action、ack。
  ******************************************************************************
  */
#ifndef __WIFI_CLIENT_H
#define __WIFI_CLIENT_H

#include <stdint.h>

void WifiClient_Init(void);       /* USART1 启动后调用 */
void WifiClient_Task(void);       /* 主循环周期性调用（非阻塞） */
int  WifiClient_IsOnline(void);   /* 是否已连上 A（TCP 已建立） */
int  WifiClient_HasState(void);   /* 是否已收到 A 的 state（可读取其字段） */

/* 上报风机动作（C 的启停许可 + 桨距目标 + 可用功率 + 稳态上限），仅在线时有效 */
void WifiClient_SendWindAction(uint8_t wind_enable, float pitch_target_deg,
                               float wind_available_kw, float wind_operating_limit_kw);

/* A 下发的 state 快照（供 wind_turbine 控制使用） */
float WifiClient_GetWindSpeedMps(void);
float WifiClient_GetWindAvailableKw(void);
float WifiClient_GetWindOperatingLimitKw(void);
float WifiClient_GetWindTargetKw(void);
float WifiClient_GetWindActualKw(void);

/* 运行期修改 A 服务端地址/端口（PC-C 上位机经 USART2 的 $WIFI 下发），并触发重连 */
void        WifiClient_SetServer(const char *ip, uint16_t port);
const char *WifiClient_GetServerIp(void);
uint16_t    WifiClient_GetServerPort(void);

#endif /* __WIFI_CLIENT_H */
