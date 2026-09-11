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

/* 上报风机动作（C 的启停许可 + 桨距目标 + 可用功率 + 稳态上限），仅在线且闭环时有效 */
void WifiClient_SendWindAction(uint8_t wind_enable, float pitch_target_deg,
                               float wind_available_kw, float wind_operating_limit_kw);

/* 开环只读消费 A 的 state（不发 wind_action）：清 got_state，
   使 WifiClient_Task 继续发起下一轮 state_request（验收项31）。 */
void WifiClient_ReleaseState(void);

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

/* ------------------------------------------------------------------ */
/*  parameter_update 与动作追踪（本整改新增）                           */
/* ------------------------------------------------------------------ */
/* A 副本同步状态（wind_turbine 据此组装 $SYNC 帧） */
#define WF_PSYNC_NONE     0u  /* 未发送 */
#define WF_PSYNC_QUEUED   1u  /* 已排队，等待安全空档 */
#define WF_PSYNC_SENT     2u  /* 已发送，等待 A 的 ACK */
#define WF_PSYNC_ACCEPTED 3u  /* A accepted */
#define WF_PSYNC_REJECTED 4u  /* A rejected */
#define WF_PSYNC_UNKNOWN  5u  /* 超时/断线，ACK 未知 */

/* 排队向 A 发送 source=C 的 parameter_update（在单未决 TCP 事务安全空档发送）。
 * parameters_json 形如 {"wind_rated_power_kw":100.00,...,"c_timeout_s":3.00}，
 * 只允许 C 白名单物理参数，control_mode 不得包含。 */
void WifiClient_QueueParameterUpdate(const char *parameters_json);

/* 最近一次被 A accepted 的 wind_action.seq（-1 表示当前会话尚无） */
int32_t WifiClient_GetLastWindActionSeq(void);

/* parameter_update 同步状态 / seq / reason（供 wind_turbine 组装 $SYNC） */
uint32_t    WifiClient_GetParamSyncStatus(void);
int32_t     WifiClient_GetParamSyncSeq(void);
const char *WifiClient_GetParamSyncReason(void);

#endif /* __WIFI_CLIENT_H */
