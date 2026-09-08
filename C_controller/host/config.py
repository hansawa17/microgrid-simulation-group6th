# -*- coding: utf-8 -*-
"""
全局配置 —— 风电子站 PC-C 上位机

集中管理：默认参数、串口/TCP 连接、数据库路径、协议字段顺序、单位/标签。
所有模块（protocol / database / controller / gui）统一从这里取值，
保证与 MCU 固件（wind_turbine.h/.c）中的默认值一致。
"""

import os

# --------------------------------------------------------------------------- #
#  数据库
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
DATABASE_PATH = os.path.join(DATABASE_DIR, "wind.db")

# --------------------------------------------------------------------------- #
#  串口（PC-C <-> STM32，USART2）—— 与固件一致
# --------------------------------------------------------------------------- #
SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 0.2          # readline 超时（秒）
SERIAL_BYTESIZE = 8
SERIAL_PARITY = "N"
SERIAL_STOPBITS = 1

# --------------------------------------------------------------------------- #
#  TCP（PC-C <-> PC-A 电网模拟器）—— 预留，本阶段暂不接入
# --------------------------------------------------------------------------- #
TCP_HOST_DEFAULT = "127.0.0.1"
TCP_PORT_DEFAULT = 9000
TCP_RECONNECT_INTERVAL = 3.0  # 秒

# --------------------------------------------------------------------------- #
#  默认风机参数（与 wind_turbine.h 的 WT_DEFAULT_* 保持一致）
# --------------------------------------------------------------------------- #
DEFAULT_PARAMS = {
    "cut_in_speed": 3.0,          # 切入风速   m/s
    "rated_speed": 12.0,          # 额定风速   m/s
    "cut_out_speed": 25.0,        # 切出风速   m/s
    "rated_power": 100.0,         # 额定功率   kW（小组统一基准）
    "deg_max": 90.0,              # 最大顺桨角 °（0° 满功率，90° 完全顺桨）
    "control_period": 1.0,        # 控制周期   s
    "communication_timeout": 3.0, # 通信超时   s
    "control_mode": 1,            # 控制模式 0开环/1闭环
}

# 参数下发帧（$PARAM）中的字段顺序（必须与 MCU 解析顺序一致）
PARAM_FIELDS = [
    "cut_in_speed",
    "rated_speed",
    "cut_out_speed",
    "rated_power",
    "deg_max",
    "control_period",
    "communication_timeout",
    "control_mode",
]

# 遥测帧（$WIND）中的字段顺序（线格式与 MCU 发送顺序一致，保持不变）。
# Python 侧字段名已对齐 A/B 仓库 state.payload 命名（见 common/protocol.md）：
#   wind_speed      -> wind_speed_mps
#   power_available -> wind_available_kw
#   power_set       -> wind_target_kw   （这是 B 下发的调度目标，联调前由本地随机生成）
#   power_actual    -> wind_actual_kw   （A 计算；联调前本地计算）
#   status          -> wind_running     （bool）
#   deg             -> pitch_target_deg
WIND_FIELDS = [
    "cycle",
    "wind_speed_mps",
    "wind_available_kw",
    "wind_target_kw",
    "wind_actual_kw",
    "wind_running",
    "pitch_target_deg",
    "control_mode",
]

# 本地 mock 在串口 8 字段基础上额外计算的字段：wind_operating_limit_kw。
# 该字段由 C 根据可用功率与运行许可计算，再经 A 存储/转发给 B；真实 MCU 尚未上送。
WIND_EXTRA_FIELDS = [
    "wind_operating_limit_kw",
]

# 参数标签 / 单位（GUI 表单与历史显示用）
PARAM_LABELS = {
    "cut_in_speed": ("切入风速", "m/s"),
    "rated_speed": ("额定风速", "m/s"),
    "cut_out_speed": ("切出风速", "m/s"),
    "rated_power": ("额定功率", "kW"),
    "deg_max": ("最大桨距角", "°"),
    "control_period": ("控制周期", "s"),
    "communication_timeout": ("通信超时", "s"),
    "control_mode": ("控制模式", ""),
}

# 控制命令（$CMD）
COMMANDS = {
    "START": "启动",
    "STOP": "停止",
    "RESET": "复位",
    "AUTO": "闭环",
    "MANUAL": "开环",
}

# 实时曲线保留点数（约 60 秒 * 若干）
CURVE_WINDOW_SECONDS = 60
CURVE_MAX_POINTS = 600
