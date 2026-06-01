# AGENTS.md

## 项目概述

多功能协议调试器 — 高云（Gowin）FPGA 项目，芯片 GW5AT-138B（GW5AT-LV138PG484AC1/I0），基于 ACX720 开发板 + FX2 USB + DDR3 + ADC + DAC。

**两大独立代码库：FPGA（Verilog）和上位机（Python）**，各自独立构建和运行。

## 目录结构

```
├── acm2108_ddr3_CDC.gprj    # 高云云源软件工程文件（FPGA开发入口，78个V文件注册）
├── acm2108_ddr3_CDC.gprj.user # Gowin EDA 自动生成（用户流程状态，建议 gitignore）
├── impl/                     # Gowin EDA 综合/布局布线输出
├── src/
│   ├── debugger_top.v        # FPGA唯一顶层（~4200行）
│   ├── acm2108_ddr3_CDC.cst  # 引脚约束（441行）
│   │
│   ├── ip/                   # Gowin IP 核（全部 9 个）
│   │   ├── gowin_pll/ / ddr_pll/
│   │   ├── ddr3_ctrl_2port/ / ddr3_memory_interface/
│   │   └── fifo_in/ / fifo_sc_hs/ / fifo_top/ / rd_data_fifo/ / wr_data_fifo/
│   │
│   ├── comm/                 # USB CDC + UART 通信协议栈
│   │   ├── FX2_CDC_Core.v / cdc_cmd_parser.v
│   │   └── uart_byte_rx.v / uart_byte_tx.v / uart_response_tx.v / uart_tx_mux.v
│   │
│   ├── common/               # 公共工具模块
│   │   ├── bin_to_bcd.v / hex8.v / hex8_ext.v / hc595_driver.v / pll_init.v
│   │   ├── bt_uart_bridge.v / cmd_ctrl_simple.v
│   │
│   ├── scope/                # ① 示波器 — ADC采集 + DDR3 + 以太网UDP
│   │   ├── ADC/              (adc_capture_stream, interleaver, frequency_counter等)
│   │   └── ethernet/         (adc_eth_tx_controller, eth_udp_gmii, trigger_detector等)
│   │
│   ├── dds/                  # ② 函数发生器 — DDS双通道 + 任意波形
│   │   └── (原 ACM2108: DDS_Module_Dual, DDS_Param_Controller, ROM×5)
│   │
│   ├── protocol/             # ③ 协议转换器 — 3个子模块
│   │   ├── sequence/         (序列发生器)
│   │   ├── pwm/              (PWM控制器)
│   │   └── devices/          (设备中心: can/ + i2c_spi/)
│   │       ├── can/          (原 CAN_Controller: can_controller, can_top等)
│   │       └── i2c_spi/      (原 protocol_converter: I2C/SPI/OLED/DS18B20)
│   │
│   ├── logic_analyzer/       # ④ 逻辑分析仪 — 采样/触发/数字信号分析
│   │   └── (原 LogicAnalyzer: logic_analyzer_capture, digital_signal_*)
│   │
│   ├── APP/                  # ⑤ 上位机 Python 应用
│   │   ├── main_app.py       # 入口：5个Tab（示波器/发生器/协议转换/逻辑分析/波特图）
│   │   ├── core/             # 串口管理 + 协议 + UDP接收
│   │   ├── scope/            # ① 示波器Tab
│   │   ├── dds/              # ② 函数发生器Tab + AWG编辑器
│   │   ├── protocol/         # ③ 协议转换器Tab: 序列+PWM+设备中心(CAN/I2C/SPI/DS18B20)
│   │   ├── logic_analyzer/   # ④ 逻辑分析仪Tab
│   │   ├── bode/             # ⑤ 波特图Tab
│   │   └── utils/            # RingBuffer / PulseView导出
│
└── docs/
    ├── 00_文档索引.md / 01_项目总览.md / 02_通信协议.md
    ├── fpga/                 # FPGA端7个模块文档
    └── host/                 # 上位机5个模块文档
```

## 开发命令

### FPGA 端（Windows）

FPGA 开发需安装 **高云云源软件（Gowin EDA）**，打开 `acm2108_ddr3_CDC.gprj` 工程即可进行综合、布局布线、生成比特流。

- 综合工具：GowinSyn（非 Synplify Pro）
- 顶层模块：`debugger_top`
- 引脚约束：`src/acm2108_ddr3_CDC.cst`
- 新增 FPGA 源文件需在 `.gprj` 中注册路径

### 上位机端（Python）

```powershell
pip install PySide6 numpy pyqtgraph pyserial
cd src\APP
python main_app.py
```

必须在 `src/APP/` 目录下运行（模块导入使用相对路径 `from core.serial_manager import ...`）。

## 功能模块状态

| 模块 | FPGA 端 | 上位机端 |
|------|---------|----------|
| DDS 函数发生器 | ✅ `src/dds/` | ✅ |
| 序列发生器 | ✅ `src/protocol/sequence/` | ✅ |
| PWM 控制器 | ✅ `src/protocol/pwm/` | ✅ |
| 示波器 | 🚧 `src/scope/` | ✅ |
| 逻辑分析仪 | 🚧 `src/logic_analyzer/` | 🚧 |
| CAN 总线 | ✅ `src/protocol/can/` | ✅ |
| I2C/SPI/OLED/1-Wire | ✅ `src/protocol/i2c_spi/` | ✅ |
| 波特图分析 | ❌ (Bode_Analyzer 未实现) | 🚧 |

## 通信协议

### CDC 帧格式
```
55 AA | cmd | len_l | len_h | payload... | cs
cs = ~(cmd + len_l + len_h + Σpayload) & 0xFF
```

### 应答帧格式
```
AA 55 | mod_id | func_id | status | data | cs
```

### 模块 ID

| ModID | 模块 | 说明 |
|-------|------|------|
| 0x01 | 系统 | 复位、数码管、蓝牙使能 |
| 0x10-0x1F | DDS 发生器 | 波形/频率/相位/幅度/任意波形 |
| 0x20-0x2A | 示波器 | 模式/触发/采样率/Buffer/频率测量 |
| 0x30-0x34 | 序列发生器(旧) | 并行/串行模式 |
| 0x40-0x43 | 序列发生器(新) | 逐通道配置 |
| 0x50-0x52 | PWM | 配置/使能/停止 |
| 0x60-0x68 | 逻辑分析/DSA | 采样/触发/数字信号分析 |
| 0x70-0x76 | I2C/OLED | 写入/初始化/显示 |
| 0x80-0x87 | SPI Flash | 配置/读写/擦除 |
| 0x90-0x91 | 蓝牙 UART | 波特率/透传 |
| 0xA0-0xA2 | DS18B20 | 温度读取/监控 |
| 0xB0-0xB4 | Bode 分析 | 配置/扫频（FPGA待实现） |
| 0xC0-0xC4 | CAN | 配置/发送/接收 |

### 双串口架构
- CDC（命令）：上位机 → FPGA（FX2 USB CDC）
- CH340（应答+数据）：FPGA → 上位机（115200 bps）

## FPGA 代码注意事项

- `debugger_top.v` 是唯一顶层，包含所有模块实例化
- 新增 CDC 命令需在 `debugger_top.v` 的 `cmd_valid_flag` case 语句中注册
- `cmd_ctrl_simple.v`（`src/common/`）未实例化，不使用
- PLL/FIFO/DDR3 为 Gowin IP 核，修改需用 IP Generator 重新生成
- 完整命令码表见 `docs/02_通信协议.md`

## 上位机代码注意事项

- 框架：PySide6 + pyqtgraph
- 入口：`main_app.py`，5 个 Tab + 1 个调试日志 Tab
- 串口管理：`core/serial_manager.py` 统一控制 CDC 和 CH340
- 文档体系：`docs/00_文档索引.md`
- 命名：文件 `snake_case.py`，类 `PascalCase`
- 导入顺序：标准库 → 第三方 → 本地模块
