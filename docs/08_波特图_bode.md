# ⑤ 波特图 (Bode Plotter)

## 源码清单

| 端 | 路径 | 说明 |
|----|------|------|
| 上位机 | `src/APP/bode/bode_plotter_tab.py` | 波特图 Tab |

## 状态

- **FPGA**：Bode_Analyzer 模块未实现，已从 `.gprj` 移除引用。`debugger_top.v` 中 bode 输出全部接 0
- **上位机**：框架已完成，依赖示波器模块先完成

## CDC 命令 (0xB0-0xB4, 预留)

| 命令 | 功能 |
|------|------|
| 0xB0 | 配置扫频参数 |
| 0xB1 | 开始扫频 |
| 0xB2 | 停止扫频 |
| 0xB3 | 查询状态 |
| 0xB4 | 数据上报 |
