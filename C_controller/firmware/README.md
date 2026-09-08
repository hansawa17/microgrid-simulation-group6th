# STM32 固件 · 风电子站

STM32CubeIDE 工程，目标芯片 **STM32G431RBT3**（LQFP64）(目前claude code按照RBT3生成，后续会改为RBT6），主频 16 MHz（HSI，未开 PLL）。

- `Core/Src/wind_turbine.c` / `Core/Inc/wind_turbine.h`：风机控制计算 + 串口协议 + USART2 收发。
- `Core/Src/main.c`：主循环按控制周期调用 `WindTurbine_PeriodicTask()`。
- USART1（PA9/PA10）：预留接 Wi-Fi 模块（TCP 客户端暂未实现）。
- USART2（PA2/PA3）：与上位机串口通信，115200 8N1。

> 当前风速 / 有功设定由 MCU 内部随机模拟；联调后由 A 经 Wi-Fi 提供，`wind_actual_kw` 改由 A 计算。
> 不提交 Debug/Release 与二进制输出。
