# ④ 逻辑分析仪 (Logic Analyzer)

## 源码清单

| 端 | 路径 | 说明 |
|----|------|------|
| FPGA | `src/logic_analyzer/logic_analyzer_top.v` | 逻辑分析顶层 |
| FPGA | `src/logic_analyzer/logic_analyzer_capture.v` | 采样与触发控制 |
| FPGA | `src/logic_analyzer/digital_signal_analyzer.v` | 数字信号分析 |
| FPGA | `src/logic_analyzer/digital_signal_ctrl.v` | 数字信号控制 |
| 上位机 | `src/APP/logic_analyzer/logic_analyzer_pulseview_tab.py` | 逻辑分析仪 Tab (仿 Saleae Logic) |
| 上位机 | `src/APP/logic_analyzer/digital_signal_panel.py` | 数字信号分析面板 |
| 上位机 | `src/APP/utils/pulseview_exporter.py` | PulseView .sr 导出 |

## FPGA 架构

```
LOGIC_IN[7:0] (数字输入引脚)
        │
        ▼
 logic_analyzer_capture
  采样状态机: IDLE → WAIT_TRIGGER → CAPTURING → DONE
  触发: 任意通道，上升/下降/电平
        │
        ▼
  DDR3 缓存 (预留接口)
```

## 上位机

- 参考 Saleae Logic 软件设计，8通道时序显示
- PulseView .sr 格式导出
- DSA 数字信号测量 (频率/占空比)

## CDC 命令 (0x60-0x68)

| 命令 | 功能 |
|------|------|
| 0x60 | 设置采样率 |
| 0x61 | 设置缓存深度 |
| 0x62 | 设置触发参数 |
| 0x63 | 启动采集 |
| 0x64 | 停止采集 |
| 0x65 | 读取状态 |
| 0x66 | DSA 开始测量 |
| 0x67 | DSA 停止测量 |
| 0x68 | DSA 读取结果 |

## 当前状态

- 采样框架和触发状态机已实现
- DDR3 缓存接口预留
- 数据回传通道待实现
- 协议解码 (I2C/SPI/UART) 待实现
