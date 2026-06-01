# 上位机程序目录结构说明

---

## 目录组织

```
src/APP/
├── main_app.py              # 主程序入口
├── core/                    # 核心模块
│   ├── serial_manager.py    # 串口管理器(CDC+CH340)
│   ├── serial_protocol.py   # 串口协议定义
│   └── ethernet_receiver.py # 以太网UDP接收器 (✨新增)
├── tabs/                    # 功能标签页
│   ├── oscilloscope_tab_v2.py      # 示波器(重构版) (✨新增)
│   ├── dds_gui_dual.py             # 函数发生器
│   ├── protocol_converter_tab.py   # 协议转换器
│   ├── logic_analyzer_pulseview_tab.py  # 逻辑分析仪
│   ├── bode_plotter_tab.py         # 波特图
│   └── panels/                     # 子面板组件
│       ├── sequence_generator_tab.py   # 序列发生器
│       ├── pwm_controller_tab.py       # PWM控制器
│       └── device_center_tab.py        # 设备中心
├── utils/                   # 工具模块
│   ├── awg_editor.py        # 任意波形编辑器
│   └── pulseview_exporter.py # PulseView数据导出
├── deprecated/              # 已废弃文件(归档)
│   └── oscilloscope_tab_old.py  # 旧版示波器(2022行,已弃用)
└── picture/                 # 图片资源(图标/Logo)
```

---

## 核心模块说明

### `core/serial_manager.py`
**功能:** 统一管理CDC发送串口和CH340接收串口  
**信号:**
- `connected(str, str)` - 连接成功(tx_port, rx_port)
- `data_received(bytes)` - 接收到数据
- `adc_data_received(list)` - ADC数据专用信号
- `frequency_data_received(bytes)` - 频率数据
- `log_message(str)` - 日志信息

**主要方法:**
- `connect(tx_port, rx_port, baud_rate)` - 连接串口
- `disconnect()` - 断开连接
- `send_command(cmd, payload, description)` - 发送命令

### `core/ethernet_receiver.py` (✨新增)
**功能:** 接收FPGA通过UDP发送的ADC采样数据  
**协议:** 自定义帧格式(0x5A 0xAA帧头 + 序号 + 时间戳 + 数据)  
**信号:**
- `data_received(bytes)` - 原始UDP数据包
- `adc_data_received(list)` - 解析后的ADC数据
- `packet_lost(int)` - 丢包通知
- `log_message(str)` - 日志信息

**主要方法:**
- `start()` - 启动接收线程
- `stop()` - 停止接收线程
- `get_statistics()` - 获取统计信息(总包数/丢包数/丢包率)

---

## 功能标签页说明

### `tabs/oscilloscope_tab_v2.py` (✨重构版)
**功能:** 双通道示波器  
**特点:**
- 清晰的流模式/Buffer模式切换(Radio Button)
- 触发配置面板在流模式下自动禁用
- 支持USB CDC和以太网UDP数据源选择
- 自适应采样参数计算
- FFT频谱分析

**重构改进:**
- 代码量从2022行减少到约400行
- UI布局清晰,分组明确
- 模式切换逻辑简化
- 状态提示清晰

**旧版问题:**
- 模式命名混乱("Buffer连续"、"Buffer单次"、"Stream触发")
- 触发配置在流模式下仍可操作但不生效
- 状态机复杂,边界条件不清
- 代码冗长,难以维护

### `tabs/dds_gui_dual.py`
**功能:** 双通道DDS函数发生器  
**支持波形:** 正弦/方波/三角/锯齿/任意波形  
**特点:** 相位控制、占空比调节、任意波形编辑器

### `tabs/logic_analyzer_pulseview_tab.py`
**功能:** 8通道逻辑分析仪(模仿Saleae Logic)  
**特点:** 协议解码(I2C/SPI/UART)、触发配置、数据导出

### `tabs/protocol_converter_tab.py`
**功能:** 协议转换器(组合界面)  
**包含:** 序列发生器 + PWM控制器 + 设备中心

---

## 工具模块说明

### `utils/awg_editor.py`
**功能:** 任意波形编辑器  
**特点:** 手绘模式、数学表达式生成、CSV导入/导出

### `utils/pulseview_exporter.py`
**功能:** 导出逻辑分析仪数据为PulseView兼容格式(.sr)

---

## deprecated目录说明

此目录存放已废弃或即将删除的旧代码，仅用于对比参考。

**当前文件:**
- `oscilloscope_tab_old.py` - 旧版示波器(2022行)
  - 原因: 模式切换混乱,代码冗长,难以维护
  - 替代: `oscilloscope_tab_v2.py`
  - 状态: 已归档,测试完成后可删除

**清理策略:**
- 新版功能测试完成后,直接删除旧文件
- 如需保留作为历史记录,建议使用Git版本控制

---

## 开发规范

### 文件命名
- 模块文件: `小写_下划线.py`
- 类名: `PascalCase`
- 函数名: `snake_case`

### 导入顺序
1. Python标准库
2. 第三方库(PySide6/numpy/pyqtgraph等)
3. 本地模块(core/tabs/utils)

### 代码风格
- 遵循PEP 8规范
- 函数/类添加docstring说明
- 复杂逻辑添加注释

### 信号/槽机制
- 使用PySide6的Signal/Slot机制实现模块解耦
- 信号命名: `动词_名词` (如 `data_received`, `command_sent`)
- 槽函数命名: `on_信号名` (如 `on_data_received`)

---

## 依赖库

### 必需库
```
PySide6>=6.5.0     # Qt界面框架
numpy>=1.24.0      # 数值计算
pyqtgraph>=0.13.0  # 实时绘图
pyserial>=3.5      # 串口通信
```

### 可选库
```
scipy              # FFT频谱分析(可用numpy替代)
matplotlib         # 高级绘图(已用pyqtgraph替代)
```

---

## 快速开始

### 1. 安装依赖
```powershell
pip install PySide6 numpy pyqtgraph pyserial
```

### 2. 运行主程序
```powershell
cd src\APP
python main_app.py
```

### 3. 连接设备
- CDC串口: 选择FX2 USB CDC设备(如COM15)
- 接收串口: 选择CH340设备(如COM24)
- 波特率: 115200

### 4. 功能测试
- 示波器: 启动ADC采集,查看波形显示
- 函数发生器: 设置波形参数,输出信号
- 逻辑分析仪: 启动采集,查看数字信号

---

## 常见问题

### Q1: 导入错误 `ModuleNotFoundError: No module named 'core'`
**解决:** 确保在`src/APP/`目录下运行程序,或设置PYTHONPATH
```powershell
$env:PYTHONPATH = "d:\Desktop\GW_FPGA\demo\cdc_ch340_logic_FINAL\src\APP"
```

### Q2: 示波器卡死怎么办?
**临时方案:**
1. 降低采样率(div_set=10)
2. 缩短Buffer长度(10k点)
3. 重启上位机程序

**根本解决:** 需配合FPGA端诊断(参考版本规划文档)

### Q3: 如何切换到以太网UDP模式?
**步骤:**
1. 示波器界面选择数据源"以太网UDP"
2. 配置FPGA IP地址和端口(未来版本支持界面配置)
3. 发送0x2B命令切换传输通道

---

## 更新日志

### v2.0 (2025-11-18)
- ✨ 新增 `oscilloscope_tab_v2.py` 重构版示波器
- ✨ 新增 `ethernet_receiver.py` 以太网接收模块
- ♻️ 重构模式切换逻辑(流模式/Buffer模式清晰分离)
- 🗑️ 归档旧版示波器（已移除）

### v1.4 (2025-01-29)
- ✨ 新增逻辑分析仪PulseView兼容界面
- ✨ 新增协议转换器组合界面
- 🐛 修复串口接收线程阻塞问题

---

**文档编写日期:** 2025-11-18  
**维护者:** AI辅助开发团队
