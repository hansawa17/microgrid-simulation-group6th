# -*- coding: utf-8 -*-
"""
本地仿真数据源 —— 无单片机硬件时用于演示完整上位机闭环

逻辑与 MCU 固件（wind_turbine.c）一致，但字段名已对齐 A/B 仓库 state.payload 命名：
    wind_speed_mps / wind_available_kw / wind_operating_limit_kw / wind_target_kw /
    wind_actual_kw / wind_running / pitch_target_deg / control_mode

说明：
  - wind_speed_mps / wind_target_kw 在联调前由本地随机游走生成（联调后由 A 的 state 提供）。
  - wind_available_kw   —— 资源可用功率（本地线性分段映射；A 采用三次方曲线，联调后以 A 为准）。
  - wind_operating_limit_kw —— 在 wind_available_kw 基础上扣 C 启停许可/保护/当前桨距/设备上限
        得到的稳态运行上限，不考虑 B 目标、B 使能或实际爬坡（与 A/B 约定一致）。
  - wind_actual_kw      —— 实际输出；联调前本地计算（min(目标, 稳态上限)，暂不含爬坡），联调后交还 A。
"""

import random

from PyQt6.QtCore import QThread, pyqtSignal

import config


class Simulator(QThread):
    """本地仿真线程：按控制周期生成一帧遥测数据。"""

    telemetry = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.params = dict(config.DEFAULT_PARAMS)
        self.run_enable = True
        self.wind_speed_mps = 9.0
        self.wind_target_kw = 500.0
        self.cycle = 0
        self._running = False

    # ------------------------------------------------------------------ #
    #  控制接口（与真实 MCU 对齐）
    # ------------------------------------------------------------------ #
    def set_params(self, params):
        self.params = dict(params)

    def command(self, cmd):
        if cmd == "START":
            self.run_enable = True
        elif cmd == "STOP":
            self.run_enable = False
        elif cmd == "RESET":
            self.params = dict(config.DEFAULT_PARAMS)
            self.run_enable = True
            self.wind_speed_mps = 9.0
            self.wind_target_kw = 500.0
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
            period = max(0.05, min(10.0, float(self.params.get("control_period", 1.0))))
            self.msleep(int(period * 1000))

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------ #
    #  控制计算（与 MCU 一致，字段名对齐仓库）
    # ------------------------------------------------------------------ #
    def _wind_available_kw(self):
        """风速 -> 资源可用功率（本地线性分段映射）。"""
        w = self.wind_speed_mps
        cut_in = self.params["cut_in_speed"]
        rated = self.params["rated_speed"]
        cut_out = self.params["cut_out_speed"]
        rated_power = self.params["rated_power"]
        if w < cut_in:
            return 0.0
        if w < rated:
            return rated_power * (w - cut_in) / (rated - cut_in)
        if w <= cut_out:
            return rated_power
        return 0.0

    def _step(self):
        # 输入模拟：随机游走（联调前本地生成，联调后由 A 的 state 提供）
        self.wind_speed_mps += random.uniform(-1.5, 1.5)
        self.wind_speed_mps = max(0.0, min(30.0, self.wind_speed_mps))
        self.wind_target_kw += random.uniform(-80.0, 80.0)
        self.wind_target_kw = max(0.0, min(self.params["rated_power"], self.wind_target_kw))

        cut_in = self.params["cut_in_speed"]
        cut_out = self.params["cut_out_speed"]
        mode = int(self.params["control_mode"])

        # 1) 资源可用功率
        wind_available_kw = self._wind_available_kw()

        # 2) 启停状态（C 的启停许可 + 风速区间）
        wind_running = self.run_enable and cut_in <= self.wind_speed_mps <= cut_out

        # 3) 桨距目标（C 闭环限功率；本地线性变桨模型，deg_max 对应完全顺桨）
        if not wind_running:
            pitch_target_deg = 0.0
        elif mode == 0:  # 开环：最大功率捕获
            pitch_target_deg = 0.0
        else:  # 闭环
            if self.wind_target_kw >= wind_available_kw:
                pitch_target_deg = 0.0
            else:
                pitch_target_deg = self.params["deg_max"] * (1.0 - self.wind_target_kw / wind_available_kw)

        # 4) 稳态运行上限 wind_operating_limit_kw：
        #    扣 C 启停许可 / 保护 / 当前桨距 / 设备上限，不扣 B 目标、B 使能或爬坡。
        if not wind_running:
            wind_operating_limit_kw = 0.0
        else:
            deg_max = self.params["deg_max"]
            pitch_factor = 1.0 - (pitch_target_deg / deg_max) if deg_max > 0 else 1.0
            wind_operating_limit_kw = min(self.params["rated_power"], wind_available_kw) * max(0.0, pitch_factor)

        # 5) 实际功率 wind_actual_kw（联调前本地计算，联调后交还 A；暂不含爬坡）
        if not wind_running:
            wind_actual_kw = 0.0
        elif mode == 0:  # 开环：最大功率捕获
            wind_actual_kw = wind_available_kw
        else:  # 闭环：min(目标, 稳态上限)
            wind_actual_kw = min(self.wind_target_kw, wind_operating_limit_kw)

        data = {
            "cycle": self.cycle,
            "wind_speed_mps": round(self.wind_speed_mps, 2),
            "wind_available_kw": round(wind_available_kw, 2),
            "wind_operating_limit_kw": round(wind_operating_limit_kw, 2),
            "wind_target_kw": round(self.wind_target_kw, 2),
            "wind_actual_kw": round(wind_actual_kw, 2),
            "wind_running": wind_running,
            "pitch_target_deg": round(pitch_target_deg, 2),
            "control_mode": mode,
        }
        self.cycle += 1
        self.telemetry.emit(data)
