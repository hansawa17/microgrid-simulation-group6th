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
#  TCP（PC-C <-> PC-A 电网模拟器）—— 已移除：按拓扑 PC-C 不直连 A，由 STM32/Wi-Fi 承担
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
#  Wi-Fi / A 服务端（STM32 作为 TCP 客户端；地址/端口可在 GUI 修改下发）
# --------------------------------------------------------------------------- #
WIFI_DEFAULT_IP = "192.168.1.100"    # 与固件 wifi_config.h 的 WIFI_SERVER_IP 一致（占位，运行期经 GUI 覆盖）
WIFI_DEFAULT_PORT = 5000             # 与固件 wifi_config.h 的 WIFI_SERVER_PORT 一致（占位，运行期经 GUI 覆盖）

# --------------------------------------------------------------------------- #
#  默认风机参数（与 wind_turbine.h 的 WT_DEFAULT_* 保持一致）
# --------------------------------------------------------------------------- #
DEFAULT_PARAMS = {
    "cut_in_speed_mps": 3.0,      # 切入风速   m/s
    "rated_speed_mps": 12.0,      # 额定风速   m/s
    "cut_out_speed_mps": 25.0,    # 切出风速   m/s
    "wind_rated_power_kw": 100.0, # 额定功率   kW（小组统一基准）
    "pitch_feather_deg": 90.0,    # 最大顺桨角 °
    "c_control_s": 1.0,           # 控制周期   s
    "c_timeout_s": 3.0,           # 通信超时   s
    "control_mode": 1,            # 控制模式 0开环/1闭环
}

# 参数下发帧（$PARAM）中的字段顺序（必须与 MCU 解析顺序一致）。
# 参数名已对齐 A 的 canonical 名（见 docs/parameter-ownership.md）：
#   cut_in_speed  -> cut_in_speed_mps
#   rated_speed   -> rated_speed_mps
#   cut_out_speed -> cut_out_speed_mps
#   rated_power   -> wind_rated_power_kw
#   deg_max       -> pitch_feather_deg
#   control_period -> c_control_s
#   communication_timeout -> c_timeout_s
PARAM_FIELDS = [
    "cut_in_speed_mps",
    "rated_speed_mps",
    "cut_out_speed_mps",
    "wind_rated_power_kw",
    "pitch_feather_deg",
    "c_control_s",
    "c_timeout_s",
    "control_mode",
]

# 遥测帧（$WIND）中的字段顺序（与固件 wind_turbine.c 发送顺序一致，9 字段）。
# Python 侧字段名已对齐 A/B 仓库 state.payload 命名（见 common/protocol.md）：
#   wind_speed            -> wind_speed_mps
#   power_available       -> wind_available_kw
#   power_operating_limit -> wind_operating_limit_kw   （联调后由 A 计算）
#   power_set             -> wind_target_kw            （B 下发的调度目标）
#   power_actual          -> wind_actual_kw            （A 计算）
#   status                -> wind_running              （bool）
#   deg                   -> pitch_target_deg
WIND_FIELDS = [
    "cycle",
    "wind_speed_mps",
    "wind_available_kw",
    "wind_operating_limit_kw",
    "wind_target_kw",
    "wind_actual_kw",
    "wind_running",
    "pitch_target_deg",
    "control_mode",
    "link_status",
]

# 参数标签 / 单位（GUI 表单与历史显示用）
PARAM_LABELS = {
    "cut_in_speed_mps": ("切入风速", "m/s"),
    "rated_speed_mps": ("额定风速", "m/s"),
    "cut_out_speed_mps": ("切出风速", "m/s"),
    "wind_rated_power_kw": ("额定功率", "kW"),
    "pitch_feather_deg": ("最大顺桨角", "°"),
    "c_control_s": ("控制周期", "s"),
    "c_timeout_s": ("通信超时", "s"),
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
