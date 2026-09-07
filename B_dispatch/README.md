[README.md](https://github.com/user-attachments/files/31892277/README.md)
# B EMS 主站 · 李佳霖

负责 operator_io 通信、operator_core 调度、ems.db、Qt 界面；连接 A，不充当本项目 TCP 中心。

建议后续拆分 operator_io.py、operator_core.py、dispatch.py、repository.py、gui/。
通信进程向 A 查询状态并写本地库；核心进程读状态/参数，生成风柴目标；闭环时通信进程发送新指令，开环仅展示结果。

首批任务：样例状态输入 → 约束调度函数 → 上下限/缺口测试 → 开闭环控制 → 联网与 GUI。
不要默认柴发瞬时满足任意缺口；不要修改 A 的实际功率或 grid.db。过期测量不应静默参与新一轮决策。
运行入口、依赖和测试命令由实现者补充。
