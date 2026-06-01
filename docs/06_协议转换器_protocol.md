# ③ 协议转换器 (Protocol)

上位机 Tab 包含 3 个子 Tab: 序列发生器、PWM 控制器、设备中心 (CAN+I2C+SPI+DS18B20+OLED+蓝牙)。

## 源码清单

| 端 | 路径 | 说明 |
|----|------|------|
| FPGA | `src/protocol/sequence/` | 序列发生器 (并行/串行) |
| FPGA | `src/protocol/pwm/` | PWM 发生器 + 参数控制 |
| FPGA | `src/protocol/devices/can/` | CAN 控制器 (FPGA-liteCAN 移植) |
| FPGA | `src/protocol/devices/i2c_spi/` | I2C/SPI/OLED/DS18B20 驱动 |
| 上位机 | `src/APP/protocol/protocol_converter_tab.py` | 容器 Tab |
| 上位机 | `src/APP/protocol/logic_analyzer_tab.py` | 序列发生器界面 |
| 上位机 | `src/APP/protocol/pwm_controller_tab.py` | PWM 界面 |
| 上位机 | `src/APP/protocol/device_center_tab.py` | 设备中心容器 |
| 上位机 | `src/APP/protocol/can_panel.py` | CAN 控制面板 |
| 上位机 | `src/APP/protocol/i2c_panel_simple.py` | I2C 面板 |
| 上位机 | `src/APP/protocol/spi_panel_simple.py` | SPI 面板 |
| 上位机 | `src/APP/protocol/ds18b20_panel.py` | DS18B20 面板 |

## 序列发生器

- 并行模式: 8通道字节序列，DDS 频率步进，最多256字节
- 串行模式: 每通道独立256位，通道掩码使能
- CDC: 0x30-0x34 (旧) / 0x40-0x43 (新，逐通道)

## PWM 控制器

- 基于 DDS 相位累加器，三级流水线消毛刺
- 8路独立，频率 1Hz-1MHz，占空比 16位 (65536级)
- CDC: 0x50 (配置) / 0x51 (使能) / 0x52 (停止)

## CAN 总线

- CAN2.0A/B, 标准帧+扩展帧，SIT1042 收发器
- 接收通过 CH340 UART 上报 (优先级3)，UDP 通道已禁用 (RGMII 冲突)
- 波特率固定 1MHz，过滤器全接收
- CDC: 0xC0-0xC4

## I2C / SPI / DS18B20 / OLED

| 协议 | CDC 命令 | 设备 |
|------|---------|------|
| I2C | 0x70-0x76 | OLED SSD1306 (0x3C) |
| SPI | 0x80-0x87 | W25Q128 Flash |
| DS18B20 | 0xA0-0xA2 | 1-Wire 温度传感器 |
| 蓝牙 | 0x90-0x91 | HC-06 UART 透传 |
