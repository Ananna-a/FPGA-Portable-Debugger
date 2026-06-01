# AGENTS.md — AI 开发助手配置

> **项目技术文档**: 全部位于 `docs/` 目录（18 篇），请输入 `/docs` 查看 `00_文档导航.md`。

## 项目概述

多功能协议调试器 — 高云（Gowin）FPGA 项目，芯片 GW5AT-138B，基于 ACX720 开发板。**FPGA（Verilog）和上位机（Python）各自独立构建**。

## 开发命令

### FPGA 端（Windows）

高云云源软件（Gowin EDA）打开 `acm2108_ddr3_CDC.gprj` → 综合 → 布局布线 → 生成比特流。
- 综合工具: **GowinSyn**（非 Synplify Pro）
- 顶层模块: `debugger_top`
- 引脚约束: `src/acm2108_ddr3_CDC.cst`

### 上位机端（Python）

```powershell
pip install PySide6 numpy pyqtgraph pyserial
cd src\APP
python main_app.py
```

**必须在 `src/APP/` 目录下运行**。

## FPGA 开发注意事项

- `debugger_top.v` 是唯一顶层（4157 行），所有模块在此实例化
- 新增 CDC 命令: 在 `cmd_valid_flag` case 语句中注册白名单 + main always 块中添加处理
- `cmd_ctrl_simple.v` 未实例化，已废弃
- PLL/FIFO/DDR3 为 Gowin IP 核，修改需 IP Generator 重新生成
- 新增 `.v` 文件需在 `.gprj` 中注册路径
- Payload 接收建议使用独立锁存器模式（参考 `sample_div_latch` 实现）

## 上位机开发注意事项

- 框架: PySide6 + pyqtgraph
- 入口: `main_app.py`，5 Tab + 1 调试日志 Tab
- 串口管理: `core/serial_manager.py` 统一控制 CDC 和 CH340
- 命名: 文件 `snake_case.py`，类 `PascalCase`
- 导入顺序: 标准库 → 第三方 → 本地模块
- Tab 间通过 SerialManager 信号/槽解耦，不直接引用

## 文档体系

所有技术文档在 `docs/`:

| 编号 | 内容 |
|:--:|------|
| 00 | 文档导航 + 阅读路径 + 源码索引 |
| 01 | 项目概述、硬件平台、构建命令 |
| 02 | 系统架构、数据通路全景、时钟域 |
| 03 | 通信协议 **完整命令码表 (60+条)** |
| 04 | FPGA 顶层 debugger_top.v 详解 |
| 05-09 | FPGA 各模块实现 (DDS/Scope/Protocol/LA/时钟IP) |
| 10-15 | 上位机各模块实现 (架构/Scope/DDS/Protocol/LA/Bode) |
| 16-17 | 开发指南 + 常见问题 |
