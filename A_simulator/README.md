[README.md](https://github.com/user-attachments/files/31892248/README.md)
# A 电网模拟器 · 刘雨杭

负责场景加载、设备仿真、TCP 服务端、grid.db 和 Qt 界面。

建议后续拆分 scenario.py、models.py、simulation.py、server.py、repository.py、gui/。这些文件当前尚未实现。
计算进程从本地库读取场景/参数/有效指令，计算实际出力并写回；通信进程解析和发送报文；GUI 查询和参数修改。

首批任务：独立风速/负荷曲线读取 → 单步纯函数仿真测试 → 库读写 → TCP 状态查询与命令接收 → GUI。
先统一物理模型。风机目标不直接作为实际出力，负荷不受调桨直接改变。柴油机补偿和约束见待确认事项。
运行入口、依赖和测试命令由实现者补充。
