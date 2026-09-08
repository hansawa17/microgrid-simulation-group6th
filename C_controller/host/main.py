# -*- coding: utf-8 -*-
"""
南极孤立微电网风力发电子站 PC-C 上位机 —— 程序入口

组装：界面(gui.MainWindow) + 业务控制器(controller.Controller)。
Controller 内部负责：串口 / TCP / 本地仿真连接管理、遥测入库、实时刷新、
参数下发、遥控命令、历史查询与导出、通信日志与报警展示。

启动：
    python main.py
"""

import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from gui import MainWindow
from controller import Controller


def main():
    app = QApplication(sys.argv)

    # 工业监控界面建议使用微软雅黑 / 思源黑体等中文字体。
    app.setFont(QFont("Microsoft YaHei", 10))

    window = MainWindow()
    controller = Controller(window)

    # 退出时优雅关闭后台线程与数据库。
    app.aboutToQuit.connect(controller.shutdown)

    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
