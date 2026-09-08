# -*- coding: utf-8 -*-
"""
TCP 客户端线程 —— PC-C 上位机 <-> PC-A 电网模拟器（预留）

真实系统中：A 电网模拟器作为 TCP Server，PC-C 作为 TCP Client，经 Wi-Fi 获取
风速 / 有功设定等实时数据。本阶段该链路暂不接入（MCU 内部随机模拟），
此处保留一个可用的 TCP 客户端框架，供三机联调时启用。
"""

import socket
import threading

from PyQt6.QtCore import QThread, pyqtSignal

import config


class TcpClient(QThread):
    """TCP 客户端线程：连接 A 电网模拟器，持续接收并向上层抛行。"""

    line_received = pyqtSignal(str)
    connected = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(self, host=config.TCP_HOST_DEFAULT, port=config.TCP_PORT_DEFAULT, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self._running = False
        self._sock = None
        self._lock = threading.Lock()

    def run(self):
        self._running = True
        while self._running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
            except OSError as e:
                # 连接失败：等待后重连
                self.connected.emit(False)
                if not self._running:
                    break
                self._sock = None
                self.error.emit(f"TCP 连接失败：{e}")
                self._sleep(config.TCP_RECONNECT_INTERVAL)
                continue

            self._sock = sock
            self.connected.emit(True)
            self._read_loop(sock)
            self._sock = None
            self.connected.emit(False)

    def _read_loop(self, sock):
        sock.settimeout(1.0)
        buf = b""
        while self._running:
            try:
                data = sock.recv(4096)
            except (socket.timeout, ConnectionError, OSError):
                if not self._running:
                    break
                continue
            except Exception:
                break

            if not data:
                break  # 对端关闭
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="ignore").strip()
                if text:
                    self.line_received.emit(text)

        try:
            sock.close()
        except Exception:
            pass

    def send(self, data: bytes) -> bool:
        with self._lock:
            if self._sock is None:
                return False
            try:
                self._sock.sendall(data)
                return True
            except Exception:
                return False

    def stop(self):
        self._running = False
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def _sleep(self, seconds):
        # 分片睡眠，便于及时响应 stop()
        for _ in range(int(seconds * 10)):
            if not self._running:
                return
            self.msleep(100)
