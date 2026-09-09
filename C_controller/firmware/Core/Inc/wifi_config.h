/**
  ******************************************************************************
  * @file    wifi_config.h
  * @brief   Wi-Fi / A 服务端 配置（SSID/密码为编译期常量；IP/端口为默认值，
 *          运行期可由 PC-C 上位机经 USART2 的 $WIFI 指令覆盖）
  ******************************************************************************
  * 说明：
  *   - WIFI_SSID / WIFI_PASSWORD 仅固件内用于连接热点，不参与运行期修改。
  *   - WIFI_SERVER_IP / WIFI_SERVER_PORT 是上电默认值；联调时可直接经上位机界面
  *     下发新地址/端口（wifi_client.c 的 WifiClient_SetServer），无需重新编译烧录。
  *   - A 电网模拟器是 TCP 服务端，STM32/Wi-Fi 作为客户端主动连接。
  ******************************************************************************
  */
#ifndef __WIFI_CONFIG_H
#define __WIFI_CONFIG_H

/* ------------------ Wi-Fi 热点（手机热点 / 路由器） ------------------ */
#define WIFI_SSID          "YOUR_WIFI_SSID"
#define WIFI_PASSWORD      "YOUR_WIFI_PASSWORD"   /* 占位符：真实密码仅本地烧录用，勿提交 */

/* ------------------ A 电网模拟器 TCP 服务端（运行期可改，见 $WIFI 指令） --- */
#define WIFI_SERVER_IP     "192.168.1.100"   /* 上电默认值；可经上位机 $WIFI 覆盖 */
#define WIFI_SERVER_PORT   5000              /* 上电默认值；可经上位机 $WIFI 覆盖 */

#endif /* __WIFI_CONFIG_H */
