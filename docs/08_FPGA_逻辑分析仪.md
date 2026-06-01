# FPGA — 逻辑分析仪 (Logic Analyzer)

**核心源文件**:  
- `src/logic_analyzer/logic_analyzer_top.v` — 序列发生器 + LA 顶层  
- `src/logic_analyzer/logic_analyzer_capture.v` (322 行) — 采样引擎  
- `src/logic_analyzer/digital_signal_analyzer.v` — DSA 数字信号分析  
- `src/logic_analyzer/digital_signal_ctrl.v` — DSA 控制接口

---

## 架构

逻辑分析仪与序列发生器共享 `logic_analyzer_top` 实例:

```verilog
// src/debugger_top.v:2052-2063
logic_analyzer_top logic_analyzer_inst(
    .clk            (clk),           // 50MHz
    .clk_sample     (clk125m),       // 125MHz (序列发生器用)
    .rst_n          (~sys_rst),
    .cmd            (cmd_code),
    .payload_data   (cmd_payload),
    .payload_valid  (cmd_payload_valid),
    .cmd_done       (cmd_done),
    .logic_in       (8'h00),         // 逻辑输入→暂未接到 LA 采集
    .logic_out      (seq_out_internal), // ← 输出到 SEQ_OUT[7:0]
    .status         (logic_status)
);
```

> 当前 `logic_in` 接 0 (悬空)，因为 `logic_analyzer_capture` 模块的输入 `LOGIC_IN[7:0]` 来自顶层 IO 端口直接连接，而非通过 `logic_analyzer_top` 传递。

---

## 采样引擎 — logic_analyzer_capture

### 状态机

```
IDLE (0)
  │ capture_en 脉冲 (0x63)
  ▼
WAIT_TRIGGER (1)  ← 触发使能时进入
  │ trigger_en=0 或 trigger_detected
  ▼
CAPTURING (2)
  │ capture_len>0 且 captured_count>=capture_len → DONE
  │ capture_stop 脉冲 (0x64) → IDLE
  ▼
DONE (3) → 自动返回 IDLE, 采集完成
```

> **状态 4 看门狗**: 5 秒超时自动复位保护 (`src/debugger_top.v:1223-1238`)

### 采样率控制

```
实际采样率 = 50MHz / sample_div

sample_div = 1  → 50 MSPS  (最高)
sample_div = 2  → 25 MSPS
sample_div = 5  → 10 MSPS
```

采样分频器仅在 `CAPTURING` 或 `WAIT_TRIGGER` 状态下计数，精确到 `sample_div - 1` 比较复位 (`src/logic_analyzer/logic_analyzer_capture.v:74-99`)。

`sample_div` 固定为 1 时每个时钟都产生采样脉冲。

> `capture_len` 固定为 0 (连续采集): `src/debugger_top.v:825-828` 每个时钟强制 `la_capture_len <= 32'd0`。

### 输入同步链

```
LOGIC_IN[7:0] (外部IO) 
  → 同步器 Stage 1 → Stage 2 → Stage 3 (3级消除亚稳态)
  → logic_in_sampled (采样后的稳定值)
```

> **源码**: `src/logic_analyzer/logic_analyzer_capture.v:106-125`

### 触发系统

```
触发配置 (0x62):
  [byte0]: bit0 = trigger_en (1=使能)
  [byte1]: trigger_mask[7:0] (1=该通道参与触发判断)
  [byte2]: trigger_value[7:0] (目标值)

触发判断 (CAPTURING 状态中每个采样周期):
  (logic_in_sampled & trigger_mask) == (trigger_value & trigger_mask)
  → trigger_detected = 1
```

当前仅支持电平触发。边沿触发在 `trigger_edge` 寄存器中有预留但未实现。

> **源码**: `src/logic_analyzer/logic_analyzer_capture.v:200-230`

### 初始化保护

```
冷启动保护:
  PLL 锁定后等待 100 μs (INIT_WAIT_CYCLES = 5000 @ 50MHz)
  → la_init_done = 1 (初始化完成)
  → la_param_stable = 1 (参数稳定)

参数更新保护:
  0x60/0x61/0x62 命令完成后:
  la_param_stable = 0 → 重新等待 100 μs
```

> **源码**: `src/debugger_top.v:940-962` (初始化 + 参数稳定逻辑)

---

## DSA — 数字信号分析

### 功能

`digital_signal_ctrl` + `digital_signal_analyzer` 提供 8 通道数字信号的自动测量:

- **频率测量**: 基于 50MHz 时钟计数信号上升沿间隔
- **高/低电平周期**: 信号在一个完整周期内的高电平时间和低电平时间

### CDC 命令

| 命令 | 功能 |
|:--:|------|
| 0x66 | DSA 开始测量 — 对 `LOGIC_IN[7:0]` 8 通道开始同步测量 |
| 0x67 | DSA 停止测量 |
| 0x68 | 读取结果 — FPGA 回复 20 字节数据 |

### 0x68 返回的数据格式

```
响应帧: AA 55 01 68 00 00 CS (应答)
  → 然后通过 DSA 状态机发送 13 字节数据帧:
  [channel 1B][freq LE32 4B][high_cycles LE32 4B][low_cycles LE32 4B]

上位机解析:
  frequency (Hz) = 50_000_000 / (high_cycles + low_cycles)  (近似)
  duty_cycle = high_cycles / (high_cycles + low_cycles)
```

> **源码**: `src/debugger_top.v:2452-2550` (DSA 数据发送状态机), `src/logic_analyzer/digital_signal_ctrl.v`

### DSA 发送总闸

```
dsa_global_tx_enable:
  0x66 → 置 1 (允许发送)
  0x67 → 置 0 (强制停止，防止残留数据阻塞 UART)
```

> **源码**: `src/debugger_top.v:254` (dsa_global_tx_enable 声明), `src/debugger_top.v:915` (默认关闭)

---

## 当前状态与待完成

| 功能 | 状态 | 说明 |
|------|:--:|------|
| 采样引擎 + 触发 | ✅ | 状态机、分频器、触发判断均已完成 |
| DDR3 缓存 | 🚧 | 接口预留，未接入 |
| 数据回传 (USB/CH340) | 🚧 | 仅框架 |
| 上位机 PulseView 界面 | 🚧 | 界面框架完成，数据链路待对接 |
| DSA 数字信号测量 | ✅ | 8 通道频率/占空比测量完成 |
| 协议解码 (I2C/SPI/UART) | ❌ | 上位机端待实现 |
| 看门狗超时保护 | ✅ | 5 秒超时自动复位 |

### 未接通的链路

```
LOGIC_IN[7:0] → logic_analyzer_capture.logic_in  ← 已连接
logic_analyzer_capture → fifo → USB/CH340 回传    ← 未实现
logic_analyzer_capture → DDR3 缓存                ← 未实现
```
