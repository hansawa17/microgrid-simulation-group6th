# -*- coding: utf-8 -*-
"""
实时曲线控件 —— 基于 pyqtgraph

封装一个可复用的实时曲线图：横轴为时间（HH:MM:SS），纵轴为数据。
支持多条曲线（图例），自动滚动最近 N 秒窗口。
"""

import time
from collections import deque

import pyqtgraph as pg
from PyQt6 import QtCore

# 曲线主题配色（与 gui.COLORS 保持一致）
SERIES_COLORS = {
    "wind_speed_mps": "#2f6fd6",      # 风速
    "wind_available_kw": "#1fa15a",   # 可用功率
    "wind_target_kw": "#e19a1a",      # 目标功率
    "wind_actual_kw": "#6b4fd8",      # 实际功率
}

# 全局抗锯齿 + 白色背景
pg.setConfigOptions(antialias=True, background="w", foreground="#33516d")


class CurveWidget(pg.PlotWidget):
    """实时曲线图：多条曲线 + 时间横轴 + 自动滚动窗口。"""

    def __init__(self, title="", ylabel="", max_points=600, window_seconds=60, parent=None):
        super().__init__(parent)
        self._max_points = max_points
        self._window_seconds = window_seconds
        self._curves = {}
        self._data = {}

        self.setLabel("left", ylabel)
        self.setLabel("bottom", "时间")
        self.setTitle(title, color="#16324f", size="12pt")

        # 时间轴
        self._date_axis = pg.DateAxisItem(orientation="bottom")
        self.setAxisItems({"bottom": self._date_axis})

        self.showGrid(x=True, y=True, alpha=0.25)
        self.addLegend(offset=(10, 10))
        self.getPlotItem().setMenuEnabled(False)
        self.getPlotItem().hideButtons()

    # ------------------------------------------------------------------ #
    def add_series(self, name, label=None, color=None):
        """添加一条曲线。返回该曲线句柄。"""
        if name in self._curves:
            return self._curves[name]
        color = color or SERIES_COLORS.get(name, "#888888")
        curve = self.plot(pen=pg.mkPen(color, width=2), name=label or name)
        self._curves[name] = curve
        self._data[name] = deque(maxlen=self._max_points)
        return curve

    def append(self, name, t, value):
        """追加一个点 (t 为 epoch 秒)。未声明的曲线会先自动创建。"""
        if value is None:
            return
        if name not in self._curves:
            self.add_series(name)
        self._data[name].append((t, float(value)))

    def append_multi(self, t, values: dict):
        """一次追加多个曲线的同一时刻数据。"""
        for name, value in values.items():
            self.append(name, t, value)

    def redraw(self):
        """把缓冲数据刷到图上，并滚动时间窗口。"""
        now = time.time()
        for name, buf in self._data.items():
            if not buf:
                continue
            xs = [p[0] for p in buf]
            ys = [p[1] for p in buf]
            self._curves[name].setData(xs, ys)
        # 最近 window_seconds 秒
        if self._window_seconds and self._window_seconds > 0:
            self.setXRange(now - self._window_seconds, now, padding=0)

    def clear(self):
        for name in self._data:
            self._data[name].clear()
            self._curves[name].setData([], [])


def build_wind_curve(parent=None):
    """预置：实时风速曲线（单条 wind_speed_mps）。"""
    w = CurveWidget(title="实时风速曲线", ylabel="风速 / m/s", parent=parent)
    w.add_series("wind_speed_mps", "风速")
    return w


def build_power_curve(parent=None):
    """预置：实时功率曲线（可用/目标/实际三条）。"""
    w = CurveWidget(title="实时功率曲线", ylabel="功率 / kW", parent=parent)
    w.add_series("wind_available_kw", "可用功率")
    w.add_series("wind_target_kw", "目标功率")
    w.add_series("wind_actual_kw", "实际功率")
    return w
