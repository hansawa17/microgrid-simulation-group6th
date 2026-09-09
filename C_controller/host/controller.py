# -*- coding: utf-8 -*-
"""
业务控制器 —— 串接 界面(gui) / 通信(serial/tcp) / 仿真(simulator) / 数据库(database)

职责：
  - 串口 / TCP / 本地仿真的连接管理
  - 遥测帧解析 -> 数据库入库 -> 界面刷新 -> 实时曲线
  - 参数下发 / 遥控命令（START/STOP/RESET/AUTO/MANUAL）
  - 历史数据查询 / 导出，报警与通信日志刷新
  - 通信超时检测

线程模型：串口、TCP、仿真均在子线程运行，通过 Qt 信号（队列连接）回到主线程
回调，因此本类所有方法均运行在主线程，可安全操作 GUI 与数据库。
"""

import csv
import time

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

import config
import protocol
from database import Database
from serial_comm import SerialWorker, list_serial_ports
from simulator import Simulator


class Controller(QObject):
    def __init__(self, ui):
        super().__init__()
        self.ui = ui

        self.db = Database()
        self.serial = None          # SerialWorker
        self.simulator = Simulator()

        # 状态
        self.current = None         # 最新遥测 dict
        self._last_data_time = 0.0  # 最近一次收到数据（monotonic 秒）
        self._sim_on = False

        # 通信超时检测定时器
        self._timeout_timer = QTimer(self)
        self._timeout_timer.timeout.connect(self._check_timeout)
        self._timeout_timer.start(1000)

        self._bind_ui()
        self._init_state()
        self._refresh_port_list()

        self.db.insert_system_log("INFO", "SYSTEM_START", "上位机启动，数据库已就绪", source="System")

    # ------------------------------------------------------------------ #
    #  初始化
    # ------------------------------------------------------------------ #
    def _init_state(self):
        params = self.db.get_params()
        self.ui.load_params_form(params)
        self.simulator.set_params(params)
        # 用数据库最近数据回填曲线（历史可追溯）
        for row in self.db.latest_telemetry(300):
            self._append_curves_from_row(row)
        self.ui.status_message("系统就绪 · 数据库已连接 · 等待连接 STM32 或启动本地仿真")

    def _bind_ui(self):
        ui = self.ui
        # 连接管理
        ui.connectSerialButton.clicked.connect(self.connect_serial)
        ui.disconnectSerialButton.clicked.connect(self.disconnect_serial)
        ui.refreshPortsButton.clicked.connect(self._refresh_port_list)
        ui.simulateButton.clicked.connect(self.toggle_simulator)
        ui.applyWifiButton.clicked.connect(self.apply_wifi)

        # 参数
        ui.applyParamsButton.clicked.connect(self.apply_params)
        ui.resetParamsButton.clicked.connect(self.reset_params)

        # 远程控制
        ui.startButton.clicked.connect(lambda: self.send_command("START"))
        ui.stopButton.clicked.connect(lambda: self.send_command("STOP"))
        ui.resetButton.clicked.connect(lambda: self.send_command("RESET"))
        ui.autoButton.clicked.connect(lambda: self.send_command("AUTO"))
        ui.manualButton.clicked.connect(lambda: self.send_command("MANUAL"))

        # 历史数据
        ui.queryButton.clicked.connect(self.query_history)
        ui.refreshButton.clicked.connect(self.refresh_history)
        ui.exportButton.clicked.connect(self.export_history)

        # 数据源信号
        self.simulator.telemetry.connect(self._on_telemetry)

    # ------------------------------------------------------------------ #
    #  连接管理
    # ------------------------------------------------------------------ #
    def _refresh_port_list(self):
        ports = list_serial_ports()
        self.ui.serialPortCombo.clear()
        if ports:
            for dev, desc in ports:
                self.ui.serialPortCombo.addItem(f"{dev} — {desc}", dev)
        else:
            self.ui.serialPortCombo.addItem("（未检测到串口）", "")
        self.ui.status_message(f"已刷新串口列表：{len(ports)} 个设备")

    def connect_serial(self):
        port = self.ui.serialPortCombo.currentData()
        if not port:
            QMessageBox.warning(self.ui, "连接失败", "未选择串口设备。")
            return
        self.disconnect_serial(silent=True)
        self.serial = SerialWorker(port)
        self.serial.line_received.connect(self._on_serial_line)
        self.serial.connected.connect(self._on_serial_connected)
        self.serial.error.connect(self._on_serial_error)
        self.serial.start()
        self.ui.status_message(f"正在连接串口 {port} …")

    def disconnect_serial(self, silent=False):
        if self.serial is not None:
            self.serial.stop()
            self.serial.wait(1000)
            self.serial = None
        self._on_serial_connected(False)
        if not silent:
            self.ui.status_message("串口已断开")

    def _on_serial_connected(self, ok):
        self.ui.set_comm_pills(pca_online=None, stm32_online=ok)
        self.ui.connectSerialButton.setEnabled(not ok)
        self.ui.disconnectSerialButton.setEnabled(ok)
        if ok:
            self.ui.status_message("STM32 串口已连接")
            self.db.insert_system_log("INFO", "UART_CONNECT", "STM32 串口连接成功", source="UART")
            self.query_wifi_config()   # 回填当前 A 服务器地址/端口

    def _on_serial_error(self, msg):
        self.ui.status_message(msg)
        self.db.insert_system_log("ERROR", "SERIAL_ERROR", msg, source="UART")

    def toggle_simulator(self):
        if self._sim_on:
            self._sim_on = False
            self.simulator.stop()
            self.simulator.wait(1000)
            self.ui.simulateButton.setText("启动本地仿真")
            self.ui.status_message("本地仿真已停止")
            self.db.insert_system_log("INFO", "SIM_STOP", "本地仿真停止", source="System")
        else:
            self._sim_on = True
            self.simulator = Simulator()
            self.simulator.telemetry.connect(self._on_telemetry)
            self.simulator.start()
            self.ui.simulateButton.setText("停止本地仿真")
            self.ui.status_message("本地仿真运行中（未连接硬件时的演示模式）")
            self.db.insert_system_log("INFO", "SIM_START", "本地仿真启动", source="System")

    # ------------------------------------------------------------------ #
    #  数据接收
    # ------------------------------------------------------------------ #
    def _on_serial_line(self, text):
        self.db.insert_communication_log("UART", "RX", self._msg_type(text), self._msg_cycle(text),
                                         data_length=len(text), result=1)
        self._dispatch_line(text)

    def _dispatch_line(self, text):
        r = protocol.parse_frame(text)
        if r is None:
            return
        kind, payload = r
        if kind == "wind":
            self._on_telemetry(payload)
        elif kind == "ack":
            atype, ok = payload
            if atype == "WIFI":
                self.ui.status_message("Wi-Fi 地址/端口已" + ("下发，MCU 正在重连 A" if ok else "下发失败（校验未通过）"))
            else:
                self.ui.status_message(f"MCU 应答：{atype} {'成功' if ok else '失败'}")
        elif kind == "wifiget":
            ip, port = payload
            self.ui.set_wifi_form(ip, port)
            self.ui.status_message(f"当前 A 服务器：{ip}:{port}")

    @staticmethod
    def _msg_type(text):
        return text.split(",", 1)[0].lstrip("$") if text else "?"

    @staticmethod
    def _msg_cycle(text):
        parts = text.split(",")
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
        return None

    def _on_telemetry(self, data):
        """统一处理遥测数据（来自串口或本地仿真）。"""
        self.current = data
        self._last_data_time = time.monotonic()

        # 入库（遥测 + 控制历史）
        try:
            self.db.insert_telemetry(data, communication_status=1)
        except Exception as e:
            self.db.insert_system_log("ERROR", "DB_WRITE", f"遥测入库失败：{e}", source="System")

        # 刷新界面
        self.ui.update_kpi(data["wind_speed_mps"], data["wind_available_kw"],
                           data["wind_target_kw"], data["wind_actual_kw"])
        self.ui.update_status(status=data["wind_running"], control_mode=data["control_mode"],
                              cycle=data["cycle"])
        self.ui.set_comm_pills(pca_online=bool(data.get("link_status", 0)), stm32_online=True)
        self.ui.append_curves(data)

    # ------------------------------------------------------------------ #
    #  通信超时检测
    # ------------------------------------------------------------------ #
    def _check_timeout(self):
        if self._last_data_time == 0.0:
            return
        timeout = float(self.db.get_params().get("c_timeout_s", 3.0))
        if time.monotonic() - self._last_data_time > timeout:
            # 仅在有数据源运行且超时时告警一次
            if self._sim_on or (self.serial is not None and self.serial.is_open()):
                self.ui.set_comm_pills(pca_online=None, stm32_online=False)
                self.db.insert_system_log("WARNING", "COMM_TIMEOUT", "STM32 通信超时", source="UART")
                self._last_data_time = 0.0  # 避免反复刷日志，等下一次数据恢复

    # ------------------------------------------------------------------ #
    #  参数
    # ------------------------------------------------------------------ #
    def apply_params(self):
        try:
            params = self.ui.read_params_form()
        except ValueError as e:
            QMessageBox.warning(self.ui, "参数错误", str(e))
            return

        # 合法性检查
        if params["rated_speed_mps"] <= params["cut_in_speed_mps"]:
            QMessageBox.warning(self.ui, "参数错误", "额定风速必须大于切入风速。")
            return
        if params["cut_out_speed_mps"] <= params["rated_speed_mps"]:
            QMessageBox.warning(self.ui, "参数错误", "切出风速必须大于额定风速。")
            return

        # 更新数据库（含 remote_adjust 记录）
        try:
            changes = self.db.update_params(params)
        except Exception as e:
            QMessageBox.warning(self.ui, "数据库错误", str(e))
            return

        # 下发到数据源
        self._send_params(params)

        self.simulator.set_params(params)
        self.ui.status_message(f"参数已应用（{len(changes)} 项改动），并已下发")
        self.db.insert_system_log("INFO", "PARAM_APPLY", f"参数下发成功，共 {len(changes)} 项改动", source="GUI")

    def _send_params(self, params):
        frame = protocol.build_param_frame(params)
        if self.serial is not None and self.serial.is_open():
            ok = self.serial.send(frame)
            self.db.insert_communication_log("UART", "TX", "PARAM", data_length=len(frame), result=1 if ok else 0)
        # 仿真模式：simulator.set_params 已在调用处处理

    def reset_params(self):
        self.ui.load_params_form(config.DEFAULT_PARAMS)
        self.ui.status_message("已恢复默认参数（未下发，点击“应用参数”生效）")

    # ------------------------------------------------------------------ #
    #  远程控制
    # ------------------------------------------------------------------ #
    def send_command(self, cmd):
        # 记录遥控命令
        label = config.COMMANDS.get(cmd, cmd)
        self.db.insert_remote_command(cmd, command_value="", source="GUI")

        # 下发
        sent = False
        if self.serial is not None and self.serial.is_open():
            frame = protocol.build_cmd_frame(cmd)
            sent = self.serial.send(frame)
            self.db.insert_communication_log("UART", "TX", cmd, data_length=len(frame), result=1 if sent else 0)
        if self._sim_on:
            self.simulator.command(cmd)
            sent = True

        # 更新本地仿真/界面状态
        if cmd == "AUTO":
            self.ui.update_status(control_mode=1)
        elif cmd == "MANUAL":
            self.ui.update_status(control_mode=0)

        if sent:
            self.ui.status_message(f"命令 {label} 已发送")
            self.db.insert_system_log("INFO", f"CMD_{cmd}", f"下发命令 {label}", source="GUI")
        else:
            self.ui.status_message(f"命令 {label} 未发送：无可用数据源（请连接 STM32 或启动本地仿真）")
            self.db.insert_system_log("WARNING", f"CMD_{cmd}", f"命令 {label} 无数据源未发送", source="GUI")

    # ------------------------------------------------------------------ #
    #  Wi-Fi / A 服务器 配置
    # ------------------------------------------------------------------ #
    def apply_wifi(self):
        try:
            ip, port = self.ui.read_wifi_form()
        except ValueError as e:
            QMessageBox.warning(self.ui, "地址错误", str(e))
            return

        if self.serial is None or not self.serial.is_open():
            self.ui.status_message("未连接 STM32 串口，无法下发 Wi-Fi 设置")
            self.db.insert_system_log("WARNING", "WIFI_APPLY", "无串口数据源，未下发 Wi-Fi 设置", source="GUI")
            return

        frame = protocol.build_wifi_frame(ip, port)
        ok = self.serial.send(frame)
        self.db.insert_communication_log("UART", "TX", "WIFI", data_length=len(frame), result=1 if ok else 0)
        if ok:
            self.ui.status_message(f"已下发 A 服务器 {ip}:{port}，MCU 正在重连…")
            self.db.insert_system_log("INFO", "WIFI_APPLY", f"下发 A 服务器地址 {ip}:{port}", source="GUI")
        else:
            self.ui.status_message("Wi-Fi 设置发送失败")
            self.db.insert_system_log("WARNING", "WIFI_APPLY", "Wi-Fi 设置发送失败", source="GUI")

    def query_wifi_config(self):
        if self.serial is not None and self.serial.is_open():
            frame = protocol.build_wifi_query_frame()
            self.serial.send(frame)
            self.db.insert_communication_log("UART", "TX", "WIFI?", data_length=len(frame), result=1)

    # ------------------------------------------------------------------ #
    #  历史数据
    # ------------------------------------------------------------------ #
    def query_history(self):
        start = self.ui.startTimeEdit.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        end = self.ui.endTimeEdit.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        if start > end:
            QMessageBox.warning(self.ui, "查询错误", "开始时间不能晚于结束时间。")
            return
        dtype = self.ui.dataTypeCombo.currentText()
        if dtype == "运行数据":
            rows = self.db.query_telemetry(start, end)
            self.ui.set_history_rows(rows, kind="telemetry")
        else:
            rows = self.db.query_control_history(start, end)
            self.ui.set_history_rows(rows, kind="control")
        self.ui.status_message(f"历史数据查询完成：{len(rows)} 条")

    def refresh_history(self):
        self.query_history()
        self.refresh_logs()

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self.ui, "导出历史数据", "history.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        headers, rows = self.ui.get_history_table()
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for row in rows:
                    writer.writerow(row)
            self.ui.status_message(f"已导出 {len(rows)} 条记录到 {path}")
        except Exception as e:
            QMessageBox.warning(self.ui, "导出失败", str(e))

    def refresh_logs(self):
        self.ui.set_system_log_rows(self.db.query_system_log())
        self.ui.set_comm_log_rows(self.db.query_communication_log())

    # ------------------------------------------------------------------ #
    #  曲线回填（启动时）
    # ------------------------------------------------------------------ #
    def _append_curves_from_row(self, row):
        # row: (timestamp, cycle, wind_speed_mps, wind_available_kw, wind_target_kw, wind_actual_kw, wind_running, pitch_target_deg, control_mode)
        ts = row[0]
        try:
            t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S.%f"))
        except ValueError:
            try:
                t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                return
        self.ui.append_curves({
            "wind_speed_mps": row[2],
            "wind_available_kw": row[3],
            "wind_target_kw": row[4],
            "wind_actual_kw": row[5],
        }, t=t)

    # ------------------------------------------------------------------ #
    def shutdown(self):
        self.disconnect_serial(silent=True)
        if self._sim_on:
            self.simulator.stop()
            self.simulator.wait(1000)
        self.db.close()
