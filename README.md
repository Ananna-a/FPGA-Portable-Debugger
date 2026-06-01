# 多功能便携调试器 — 嵌入式 FPGA 比赛项目

基于高云 GW5AT-138B (ACX720 开发板) 的多功能协议调试器，集成 5 大仪器功能于一体，通过 USB CDC 命令控制 + 以太网 UDP 高速数据传输，实现 FPGA 与上位机协同工作。

## 硬件平台

| 资源 | 型号 / 规格 |
|------|------------|
| FPGA 芯片 | GW5AT-138B (GW5AT-LV138PG484AC1/I0) |
| 开发板 | ACX720 |
| 系统时钟 | 50 MHz 外部晶振 |
| USB 接口 | FX2 USB 2.0 (CDC) + CH340 UART |
| 存储器 | DDR3 高速缓存 |
| 模拟前端 | 双通道 8 位 ADC + 双通道 8 位 DAC |
| 其他外设 | 以太网 RGMII、CAN SIT1042、I2C OLED、SPI Flash、DS18B20 |

## 功能模块

| 模块 | FPGA 状态 | 上位机状态 | 说明 |
|------|:--:|:---:|------|
| 📊 **示波器** | 🚧 | ✅ | 双通道 ADC → DDR3 → UDP，Stream/Buffer 模式，触发 + FFT |
| 📡 **DDS 函数发生器** | ✅ | ✅ | 双通道 1Hz-50MHz，5 种波形 + 任意波形，相位/幅度独立控制 |
| 🔧 **协议转换器** | ✅ | ✅ | 序列发生器 + 8路 PWM + 设备中心 (CAN/I2C/SPI/DS18B20/OLED) |
| 🔬 **逻辑分析仪** | 🚧 | 🚧 | 8 通道数字信号采集 + 触发，仿 Saleae Logic 界面，支持 PulseView 导出 |
| 📈 **波特图** | ❌ | 🚧 | 频率响应分析 (FPGA 端 Bode_Analyzer 待实现) |

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│  上位机 (Python / PySide6)                               │
│  main_app.py → 5 Tab + SerialManager                     │
│    示波器 │ DDS │ 协议转换 │ 逻辑分析 │ 波特图            │
└──────┬──────────┬──────────┬────────────────────────────┘
       │ CDC COM  │ CH340    │ UDP :6102
       ▼ (命令)   ▲ (应答)   ▲ (ADC 波形)
┌─────────────────────────────────────────────────────────┐
│  FPGA GW5AT-138B (debugger_top.v, ~4157 行)              │
│  FX2_CDC_Core → cdc_cmd_parser → 60+ 命令码路由          │
│    ├── DDS_Module_Dual (① 函数发生器)                    │
│    ├── ADC → DDR3 → eth (② 示波器)                       │
│    ├── CAN / I2C / SPI / PWM / SEQ (③ 协议转换器)         │
│    └── logic_analyzer_capture (④ 逻辑分析仪)               │
└─────────────────────────────────────────────────────────┘
```

## 通信协议

| 通道 | 方向 | 物理接口 | 速率 | 用途 |
|------|------|---------|------|------|
| CDC | PC → FPGA | FX2 USB | USB 2.0 FS | 命令下发 |
| CH340 | FPGA → PC | CH340 UART | 115200 bps | 应答 + 数据上报 |
| UDP | FPGA → PC | RGMII 以太网 | 100 Mbps | 示波器波形 |

- **CDC 命令帧**: `55 AA | cmd | len_l | len_h | payload | cs`
- **应答帧**: `AA 55 | mod_id | func_id | status | data | cs`
- 50+ 命令码覆盖所有模块 (详见 `docs/03_通信协议.md`)

## 目录结构

```
├── acm2108_ddr3_CDC.gprj     # 高云云源软件工程入口
├── src/
│   ├── debugger_top.v         # FPGA 唯一顶层 (~4157 行)
│   ├── acm2108_ddr3_CDC.cst   # 引脚约束 (441 行)
│   ├── ip/                    # Gowin IP 核 (PLL / DDR3 / FIFO × 9)
│   ├── comm/                  # USB CDC + UART 通信协议栈
│   ├── common/                # 公共模块 (数码管 / 蓝牙桥 / HEX)
│   ├── scope/                 # ① 示波器 (ADC 采集 + 以太网 UDP)
│   ├── dds/                   # ② DDS 函数发生器 (双通道 + 任意波形)
│   ├── protocol/              # ③ 协议转换器
│   │   ├── sequence/          #    序列发生器
│   │   ├── pwm/               #    PWM 控制器
│   │   └── devices/           #    设备中心 (can/ + i2c_spi/)
│   ├── logic_analyzer/        # ④ 逻辑分析仪
│   └── APP/                   # ⑤ 上位机 Python 应用
│       ├── main_app.py        #    入口：5 Tab 主窗口
│       ├── core/              #    串口管理 + 协议 + UDP 接收
│       ├── scope/             #    ① 示波器 Tab
│       ├── dds/               #    ② DDS 发生器 Tab
│       ├── protocol/          #    ③ 协议转换器 Tab
│       ├── logic_analyzer/    #    ④ 逻辑分析仪 Tab
│       ├── bode/              #    ⑤ 波特图 Tab
│       └── utils/             #    环形缓冲 / PulseView 导出
└── docs/                      # 项目文档 (18 篇)
```

## 快速开始

### FPGA 端 (Windows)

1. 安装 **高云云源软件 (Gowin EDA)**
2. 打开 `acm2108_ddr3_CDC.gprj` 工程
3. 综合 → 布局布线 → 生成比特流 → 下载到 FPGA

### 上位机端 (Python)

```powershell
# 安装依赖
pip install PySide6 numpy pyqtgraph pyserial

# 运行主程序 (必须在 src/APP/ 目录下)
cd src\APP
python main_app.py
```

可选依赖: `scipy` (AWG 编辑器插值), `Cython + Visual Studio` (UDP 加速接收)

## 开发状态

- ✅ 已完成: DDS 双通道函数发生器、序列发生器 (256 字节 × 8 通道)、PWM 控制器 (8 路)、CAN2.0A/B 总线、I2C OLED 显示、SPI Flash 读写、DS18B20 温度传感器、蓝牙透传
- 🚧 进行中: 示波器 ADC 采集 + UDP 传输 (以太网数据通路已验证，上位机界面完成)、逻辑分析仪 (采样框架完成)
- ❌ 待实现: 波特图 FPGA 端 Bode_Analyzer 模块

## 文档

详见 [`docs/`](docs/) 目录，18 篇技术文档覆盖 FPGA 端 6 模块 + 上位机端 6 模块 + 协议 + 开发参考：

| 编号 | 文件 | 内容 |
|------|------|------|
| 00 | `00_文档导航.md` | 文档导航、阅读路径、源码索引 |
| 01 | `01_项目概述.md` | 项目身份、硬件平台、构建命令、模块状态 |
| 02 | `02_系统架构.md` | FPGA↔上位机总体架构、数据通路全景、时钟域 |
| 03 | `03_通信协议.md` | CDC/UDP/CH340 帧格式、60+ 命令码完整表 |
| 04-09 | FPGA 实现 (6篇) | 顶层架构、DDS、示波器、协议转换器、逻辑分析仪、IP核与时钟 |
| 10-15 | 上位机实现 (6篇) | 架构概述、示波器、DDS/AWG、协议转换器、逻辑分析仪、波特图 |
| 16-17 | 开发参考 (2篇) | 开发指南 + 常见问题 |

## 技术亮点

- **双串口架构**: CDC 命令 + CH340 应答分离，避免半双工冲突
- **DDR3 高速缓存**: 示波器通过 DDR3 乒乓缓冲实现连续采集与触发后停止
- **UDP 高速传输**: FPGA 端 GMII → RGMII 转换，上位机端 C 扩展加速接收
- **DDS 精确频率**: 32 位相位累加器，±0.03 Hz 精度，5 种基础波形 + 任意波形编辑器
- **模块解耦**: FPGA 命令码区间分发 + Python 信号/槽，Tab 间无直接依赖
- **仿 Saleae 逻辑分析仪**: 8 通道时序显示，支持 I2C/SPI/UART 协议解码与 PulseView 导出

## 许可证

本项目由开源社区贡献者开发，保留版权。详细许可信息请参考各子模块。

---

*多功能便携调试器 — 嵌入式 FPGA 比赛项目*
