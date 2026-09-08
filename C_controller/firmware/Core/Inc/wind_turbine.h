/**
  ******************************************************************************
  * @file    wind_turbine.h
  * @brief   风力发电机控制器 —— 控制计算与串口协议（学生 C / MCU 侧）
  *
  * 说明：
  *  - 本模块负责"风电子站"单片机侧的完整控制闭环：
  *      输入(风速/有功设定/控制模式) -> 计算(可用功率/启停/桨距角) -> 输出(串口上报)
  *  - USART1 (PA9/PA10) 保留给 Wi-Fi 模块（本阶段暂不接入 A 电网模拟器）。
  *  - USART2 (PA2/PA3)  与 PC 上位机通过串口通信（本阶段的数据通路）。
  *
  *  串口帧格式（ASCII，\r\n 结尾，字段以 , 分隔）：
  *     MCU -> PC 遥测:  $WIND,<cycle>,<wind_speed>,<power_available>,<power_set>,<power_actual>,<status>,<deg>,<control_mode>\r\n
  *     PC  -> MCU 参数: $PARAM,<cut_in_speed_mps>,<rated_speed_mps>,<cut_out_speed_mps>,<wind_rated_power_kw>,<pitch_feather_deg>,<c_control_s>,<c_timeout_s>,<control_mode>\r\n
  *     PC  -> MCU 命令: $CMD,START|STOP|RESET|AUTO|MANUAL\r\n
  *     MCU -> PC  应答: $ACK,<PARAM|CMD>,<0|1>\r\n
  ******************************************************************************
  */
#ifndef __WIND_TURBINE_H
#define __WIND_TURBINE_H

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include <stdint.h>

/* ------------------------------------------------------------------ */
/*  默认风机参数（与上位机 wind.db 默认值保持一致）                     */
/* ------------------------------------------------------------------ */
#define WT_DEFAULT_CUT_IN_SPEED_MPS    3.0f    /* 切入风速   m/s  */
#define WT_DEFAULT_RATED_SPEED_MPS     12.0f   /* 额定风速   m/s  */
#define WT_DEFAULT_CUT_OUT_SPEED_MPS   25.0f   /* 切出风速   m/s  */
#define WT_DEFAULT_WIND_RATED_POWER_KW 100.0f  /* 额定功率   kW   */
#define WT_DEFAULT_PITCH_FEATHER_DEG   90.0f   /* 最大顺桨角 °    */
#define WT_DEFAULT_C_CONTROL_S         1.0f    /* 控制周期   s    */
#define WT_DEFAULT_C_TIMEOUT_S         3.0f    /* 通信超时   s    */

/* 控制模式 */
#define WT_MODE_OPEN_LOOP     0u  /* 开环 */
#define WT_MODE_CLOSED_LOOP   1u  /* 闭环 */

/* 风机启停状态 */
#define WT_STATUS_STOP        0u
#define WT_STATUS_RUN         1u

/* ------------------------------------------------------------------ */
/*  对外接口                                                          */
/* ------------------------------------------------------------------ */
void     WindTurbine_Init(void);            /* 初始化状态与默认参数        */
void     WindTurbine_StartRx(void);         /* 启动 USART2 中断接收        */
void     WindTurbine_PeriodicTask(void);    /* 每个控制周期调用一次        */
uint32_t WindTurbine_GetPeriodMs(void);     /* 返回当前控制周期(ms)        */
void     WindTurbine_OnRxByte(uint8_t byte);/* USART2 收到一个字节         */

#ifdef __cplusplus
}
#endif

#endif /* __WIND_TURBINE_H */
