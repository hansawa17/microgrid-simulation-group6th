# -*- coding: utf-8 -*-
"""
本地仿真数据源 —— 无单片机硬件时用于演示完整上位机闭环

逻辑与 MCU 固件（wind_turbine.c）一致，但字段名已对齐 A/B 仓库 state.payload 命名：
    wind_speed_mps / wind_available_kw / wind_operating_limit_kw / wind_target_kw /
    wind_actual_kw / wind_running / pitch_target_deg / control_mode

说明：
  - wind_speed_mps / wind_target_kw 在联调前由本地随机游走生成（联调后由 A 的 state 提供）。
  - wind_available_kw   —— 资源可用功率（三次方曲线，与 A 一致）。
  - wind_operating_limit_kw —— 运行许可且无保护时 = wind_available_kw，否则 0（不扣桨距/目标）。
  - wind_actual_kw      —— 实际输出；联调前本地估算（min(目标, 稳态上限)），联调后由 A 计算。

核心控制计算抽为纯函数 compute_control()，与固件 wind_turbine.c 对齐，便于单元测试。
"""

import random #生成随机数，随机风速/随机目标功率，randomwalk（变化幅度控制）

from PyQt6.QtCore import QThread #划分主线程和模拟线程
from PyQt6.QtCore import pyqtSignal #simulator给GUI发送信息的数据通道

import config


def compute_control(wind_speed_mps, wind_target_kw, params, run_enable=True):
    """风电子站核心控制计算（与固件 wind_turbine.c 一致，纯函数便于单测）。

    返回 dict：
        wind_available_kw / wind_running / pitch_target_deg /
        wind_operating_limit_kw / wind_actual_kw
    """
    # 转换成浮点数，输入的参数未必是天然的python数字
    cut_in = float(params["cut_in_speed_mps"])
    rated = float(params["rated_speed_mps"])
    cut_out = float(params["cut_out_speed_mps"])
    rated_power = float(params["wind_rated_power_kw"])
    feather = float(params["pitch_feather_deg"])
    mode = int(params["control_mode"])

    w = wind_speed_mps

    # 1) 资源可用功率（三次方曲线，与 A/固件一致）
    if w < cut_in:
        wind_available_kw = 0.0
    elif w < rated:
        fraction = (w - cut_in) / (rated - cut_in)
        wind_available_kw = rated_power * fraction ** 3
    elif w < cut_out:
        wind_available_kw = rated_power
    else:
        wind_available_kw = 0.0

    # 2) 启停（启停许可 + 风速区间）
    wind_running = bool(run_enable) and cut_in <= w <= cut_out

    # 3) 桨距目标（0-90°：停机/无可用功率→顺桨 90°，开环→0°，闭环按目标限功率）
    if not wind_running or wind_available_kw <= 0.0:
        pitch_target_deg = feather                     # 停机/无可用功率：顺桨
    elif mode == 0:                                     # 开环：最大功率捕获
        pitch_target_deg = 0.0
    else:                                               # 闭环：按目标限功率
        if wind_target_kw >= wind_available_kw:
            pitch_target_deg = 0.0
        else:
            pitch_target_deg = feather * (1.0 - wind_target_kw / wind_available_kw)

    # 4) 稳态运行上限 wind_operating_limit_kw（不扣桨距/目标，避免 B 限功率自锁）
    wind_operating_limit_kw = wind_available_kw if wind_running else 0.0

    # 5) 实际功率 wind_actual_kw（联调前本地估算，联调后交还 A；暂不含爬坡）
    if not wind_running:
        wind_actual_kw = 0.0
    elif mode == 0:  # 开环：最大功率捕获
        wind_actual_kw = wind_available_kw
    else:  # 闭环：min(目标, 稳态上限)
        wind_actual_kw = min(wind_target_kw, wind_operating_limit_kw)

    return {
        "wind_available_kw": wind_available_kw,
        "wind_running": wind_running,
        "pitch_target_deg": pitch_target_deg,
        "wind_operating_limit_kw": wind_operating_limit_kw,
        "wind_actual_kw": wind_actual_kw,
    }


class Simulator(QThread):
    """本地仿真线程：按控制周期生成一帧遥测数据。"""

    telemetry = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.params = dict(config.DEFAULT_PARAMS)
        self.run_enable = True
        self.wind_speed_mps = 9.0
        self.wind_target_kw = 60.0
        self.cycle = 0
        self._running = False

    # ------------------------------------------------------------------ #
    #  控制接口（与真实 MCU 对齐）
    # ------------------------------------------------------------------ #
    def set_params(self, params):
        """运行期更新控制参数（切入/额定/切出风速、额定功率、最大顺桨角、
        控制周期、通信超时、控制模式），下一控制周期即生效。

        风速->功率的三次方曲线框架保持不变，仅参数取值变化；非法/越界取值
        按固件 wind_turbine.c 的规则钳位，避免分母为 0。
        """
        p = dict(params)
        try:
            p["cut_in_speed_mps"]    = float(p["cut_in_speed_mps"])
            p["rated_speed_mps"]     = float(p["rated_speed_mps"])
            p["cut_out_speed_mps"]   = float(p["cut_out_speed_mps"])
            p["wind_rated_power_kw"] = float(p["wind_rated_power_kw"])
            p["pitch_feather_deg"]   = float(p["pitch_feather_deg"])
            p["c_control_s"]         = float(p["c_control_s"])
            p["c_timeout_s"]         = float(p["c_timeout_s"])
            p["control_mode"]        = int(p["control_mode"])
            if p["rated_speed_mps"] <= p["cut_in_speed_mps"]:
                p["rated_speed_mps"] = p["cut_in_speed_mps"] + 1.0
            if p["cut_out_speed_mps"] <= p["rated_speed_mps"]:
                p["cut_out_speed_mps"] = p["rated_speed_mps"] + 1.0
        except (KeyError, ValueError, TypeError):
            return  # 参数不完整/非法时不覆盖，保持当前取值
        self.params = p

    def command(self, cmd):
        if cmd == "START":
            self.run_enable = True
        elif cmd == "STOP":
            self.run_enable = False
        elif cmd == "RESET":
            self.params = dict(config.DEFAULT_PARAMS)
            self.run_enable = True
            self.wind_speed_mps = 9.0
            self.wind_target_kw = 60.0
            self.cycle = 0
        elif cmd == "AUTO":
            self.params["control_mode"] = 1
        elif cmd == "MANUAL":
            self.params["control_mode"] = 0

    # ------------------------------------------------------------------ #
    def run(self):
        self._running = True
        while self._running:
            self._step()
            period = max(0.05, min(10.0, float(self.params.get("c_control_s", 1.0))))
            self.msleep(int(period * 1000))

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------ #
    #  控制计算（与 MCU 一致，字段名对齐仓库）
    # ------------------------------------------------------------------ #
    def _step(self):
        # 输入模拟：随机游走（联调前本地生成，联调后由 A 的 state 提供）
        self.wind_speed_mps += random.uniform(-1.5, 1.5)
        self.wind_speed_mps = max(0.0, min(30.0, self.wind_speed_mps))
        self.wind_target_kw += random.uniform(-20.0, 20.0)
        self.wind_target_kw = max(0.0, min(self.params["wind_rated_power_kw"], self.wind_target_kw))

        r = compute_control(self.wind_speed_mps, self.wind_target_kw, self.params, self.run_enable)
        mode = int(self.params["control_mode"])

        data = {
            "cycle": self.cycle,
            "wind_speed_mps": round(self.wind_speed_mps, 2),
            "wind_available_kw": round(r["wind_available_kw"], 2),
            "wind_operating_limit_kw": round(r["wind_operating_limit_kw"], 2),
            "wind_target_kw": round(self.wind_target_kw, 2),
            "wind_actual_kw": round(r["wind_actual_kw"], 2),
            "wind_running": r["wind_running"],
            "pitch_target_deg": round(r["pitch_target_deg"], 2),
            "control_mode": mode,
            "link_status": 0,   # 本地仿真无 A 连接，链路状态恒为离线
        }
        self.cycle += 1
        self.telemetry.emit(data)
