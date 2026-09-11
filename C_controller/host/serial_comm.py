# -*- coding: utf-8 -*-
"""
串口通信线程 —— PC-C 上位机 <-> STM32 风机控制器（USART2）

基于 pyserial + QThread。后台循环 readline 读取一帧，通过 Qt 信号把
原始行抛给主线程（controller），由 protocol.parse_frame 统一解析。
"""

import threading
import time

import serial
import serial.tools.list_ports
from PyQt6.QtCore import QThread, pyqtSignal

import config


def list_serial_ports():
    """枚举当前可用的串口设备列表，返回 [(设备名, 描述), ...]。"""
    ports = []
    try:
        for p in serial.tools.list_ports.comports():
            ports.append((p.device, p.description or p.device))
    except Exception:
        pass
    return ports


class SerialWorker(QThread):
    """串口后台线程：负责打开/关闭串口、持续读取并向上层抛行。"""

    line_received = pyqtSignal(str)     # 收到一行（不含换行）
    connected = pyqtSignal(bool)        # 串口打开/关闭
    error = pyqtSignal(str)             # 错误信息

    def __init__(self, port, baudrate=config.SERIAL_BAUDRATE, parent=None):
        super().__init__(parent)
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self._running = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  线程生命周期
    # ------------------------------------------------------------------ #
    def run(self):
        self._running = True
        connected = False
        last_open_error = None

        while self._running:
            if self._serial is None:
                try:
                    self._serial = serial.Serial(
                        port=self.port,
                        baudrate=self.baudrate,
                        bytesize=config.SERIAL_BYTESIZE,
                        parity=config.SERIAL_PARITY,
                        stopbits=config.SERIAL_STOPBITS,
                        timeout=config.SERIAL_TIMEOUT,
                    )
                except Exception as e:
                    message = f"串口 {self.port} 暂不可用，正在自动重连：{e}"
                    if message != last_open_error:
                        self.error.emit(message)
                        last_open_error = message
                    if connected:
                        connected = False
                        self.connected.emit(False)
                    time.sleep(config.SERIAL_RECONNECT_DELAY_S)
                    continue
                connected = True
                last_open_error = None
                self.connected.emit(True)

            try:
                line = self._serial.readline()
                if not line:
                    continue
                text = line.decode("ascii", errors="ignore").strip()
                if text:
                    self.line_received.emit(text)
            except serial.SerialException as e:
                if self._running:
                    self.error.emit(f"串口连接中断，正在自动重连：{e}")
            except Exception as e:
                if self._running:
                    self.error.emit(f"串口异常，正在自动重连：{e}")
            else:
                continue

            self._close_port()
            if connected:
                connected = False
                self.connected.emit(False)
            if self._running:
                time.sleep(config.SERIAL_RECONNECT_DELAY_S)

        self._close_port()
        if connected:
            self.connected.emit(False)

    # ------------------------------------------------------------------ #
    #  对外方法
    # ------------------------------------------------------------------ #
    def send(self, data: bytes) -> bool:
        """向串口写入字节，返回是否成功。"""
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                return False
            try:
                self._serial.write(data)
                self._serial.flush()
                return True
            except Exception:
                return False

    def stop(self):
        """请求线程退出并关闭串口。"""
        self._running = False
        self._close_port()

    def _close_port(self):
        with self._lock:
            if self._serial is not None:
                try:
                    if self._serial.is_open:
                        self._serial.close()
                except Exception:
                    pass
                self._serial = None

    def is_open(self) -> bool:
        with self._lock:
            return self._serial is not None and self._serial.is_open
