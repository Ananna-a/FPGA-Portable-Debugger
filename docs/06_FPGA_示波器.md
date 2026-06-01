# FPGA — 示波器 (Scope)

**核心源文件**:  
- `src/scope/ADC/adc_capture_stream.v` (480 行) — ADC 采集流控  
- `src/scope/ADC/adc_dual_channel_interleaver.v` — 双通道交织  
- `src/scope/ADC/frequency_counter.v` — 硬件过零频率计  
- `src/scope/ADC/frequency_tx_controller.v` — 频率数据上报  
- `src/scope/ADC/zero_cross_comparator.v` — 过零检测  
- `src/scope/ethernet/trigger_detector.v` — 触发检测器  
- `src/scope/ethernet/adc_eth_tx_controller.v` — DDR3→UDP 分包  
- `src/scope/ethernet/eth_udp_tx_wrapper.v` — UDP 封装  
- `src/scope/ethernet/eth_udp_gmii/eth_udp_tx_gmii.v` — GMII TX 引擎  
- `src/scope/ethernet/gmii_rgmii_gmii/gmii_to_rgmii.v` — GMII→RGMII

---

## 整体数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│ 模拟前端 (50MSPS 8位ADC×2)                                           │
│                                                                      │
│  ADC_CH1 ──→ adc_capture_stream #1 ──→┐                              │
│  ADC_CH2 ──→ adc_capture_stream #2 ──→┤                              │
│                                        ▼                              │
│                          adc_dual_channel_interleaver                │
│                          交织: {CH2[15:8], CH1[7:0]}                  │
│                                        │                              │
│                   ┌────────────────────┤                              │
│                   ▼                    ▼                              │
│            trigger_detector    wr_data_fifo (异步FIFO)               │
│            (边沿/电平检测)      50MHz → DDR3时钟域                     │
│                   │                    │                              │
│                   │                    ▼                              │
│                   │            ddr3_ctrl_2port 写端口                 │
│                   │            128位宽 × Burst 128                   │
│                   │                    │                              │
│                   │              DDR3 SDRAM 256MB                     │
│                   │                    │                              │
│                   │            ddr3_ctrl_2port 读端口                 │
│                   │                    │                              │
│                   │            rd_data_fifo (异步FIFO)               │
│                   │            DDR3时钟域 → 125MHz                    │
│                   │                    │                              │
│                   └────────────────────┤                              │
│                                        ▼                              │
│                          adc_eth_tx_controller                       │
│                          分包 1024B/帧, 帧头 5A AA                    │
│                                        │                              │
│                                        ▼                              │
│                          eth_udp_tx_wrapper → eth_udp_tx_gmii        │
│                          GMII → gmii_to_rgmii → RGMII               │
│                                        │                              │
│                                        ▼                              │
│                          以太网 PHY → PC (UDP :6102)                  │
└─────────────────────────────────────────────────────────────────────┘
```

> 从 ADC 芯片到上位机 pyqtgraph 显示的完整路径经过了 **6 个时钟域 + 2 个外存层次**。

---

## 第一级: ADC 采集与采样率控制

### adc_capture_stream 模块

每个 ADC 通道一个实例，支持 Stream 和 Buffer 两种模式。

**状态机**:

```
STATE_IDLE → (capture_start 脉冲) → STATE_CAPTURING/STATE_WAIT_TRIGGER
                                        │
                          Stream 模式:  永久循环，直到 stop 脉冲
                          Buffer 模式:  capture_length 达限 → STATE_DONE
```

**采样率控制公式**:

```
实际采样率 = 50MHz / RESAMPLE_RATIO / div_set

当 RESAMPLE_RATIO = 1:
  div_set = 1  → 50 MSPS   (最高, ~25MHz 信号带宽)
  div_set = 2  → 25 MSPS
  div_set = 5  → 10 MSPS
  div_set = 100 → 500 kSPS
```

`div_set` 由上位机通过 0x26 命令动态配置。

> **源码**: `src/scope/ADC/adc_capture_stream.v:60-100` (参数定义+两级分频)

**触发系统**（Stream 和 Buffer 模式共用）:

```
触发配置 (0x22):
  trigger_en:     0=自动(连续), 1=等待触发
  trigger_channel: 0=CH1, 1=CH2
  trigger_edge:   0=上升沿, 1=下降沿
  trigger_level:  8位 ADC 值 (0-255), 阈值比较

流模式下:
  trigger_en=0 → 自动连续采集 (立即输出)
  trigger_en=1 → 正常触发模式 (等待边沿)

Buffer模式下:
  天然单次, trigger_en 控制是否等待触发
  触发后采集 capture_length 个采样点后自动停止
```

### 双通道交织 — adc_dual_channel_interleaver

将两个 8 位 ADC 通道交织为 16 位数据:

```
输出格式: {CH2_data[7:0], CH1_data[7:0]} (16位)

采样序列:
  CH1_sample0, CH2_sample0, CH1_sample1, CH2_sample1, ...
  ──交织为─────────────────>
  {CH2_0, CH1_0}, {CH2_1, CH1_1}, ...
```

上位机解交织:
```python
CH2_data = raw[0::2]  # 取偶数索引
CH1_data = raw[1::2]  # 取奇数索引
```

> **源码**: `src/scope/ADC/adc_dual_channel_interleaver.v`

---

## 第二级: DDR3 乒乓缓冲与流控

### DDR3 架构

- **控制器**: `ddr3_ctrl_2port` — 双端口 (同时读写)
- **DRAM**: 256 MB, 16 位数据总线
- **写端口位宽**: 128 位 (4 个 16 位采样点 × 2 时钟 = 8 个采样点/写)
- **读端口位宽**: 128 位
- **Burst 长度**: 128
- **时钟**: 400 MHz (DDR3 参考)

### 流控制策略

为应对 DDR3 写满或读空的风险，顶层实现了精确流控:

```
参数 (src/debugger_top.v:514-524):
  DDR3_BUFFER_SIZE       = 256 MB
  ALMOST_FULL_THRESHOLD  = 254 MB (99.2%) — 停止写入
  ALMOST_EMPTY_THRESHOLD = 32 KB (0.012%)  — 停止读取
  START_READ_THRESHOLD   = 64 KB (0.024%)  — 启动读取
  SAFE_READ_THRESHOLD    = 48 KB (0.018%)  — 继续读取

ddr3_data_count = ddr3_write_count - ddr3_read_count (实时差值)

流控规则:
  ddr3_data_count > ALMOST_FULL_THRESHOLD → 暂停 ADC 写入
  ddr3_data_count < ALMOST_EMPTY_THRESHOLD → 暂停 以太网 读取
  ddr3_data_count >= START_READ_THRESHOLD → 启动以太网传输
  ddr3_data_count >= SAFE_READ_THRESHOLD → 可以继续读取
```

> **源码**: `src/debugger_top.v:511-532`

### DDR3 读写流水线

```
写入路径 (50MHz域):
  ADC 16位数据 → wr_data_fifo (跨时钟域) → ddr3_ctrl_2port 写端口

读取路径 (流水线 3 级):
  Stage1: ddr3_rd_fifo_rden + empty 检测
  Stage2: ddr3_rd_data_stage2 锁存
  Stage3: ddr3_rd_data_stage3 → 以太网模块

避免同时钟域数据丢失的三级锁存 (参考 sequence_playback_serial_v3.v 设计)
```

> **源码**: `src/debugger_top.v:537-547` (三级流水线), `src/ip/ddr3_ctrl_2port/`

---

## 第三级: 以太网 UDP 传输

### 以太网模块链

```
┌──────────────────────┐
│ adc_eth_tx_controller │ ← DDR3 读数据 (16位, 125MHz)
│                      │ → UDP 分包: 每包 ~1008B ADC数据
│                      │ → 帧头: 5A AA + SEQ + TS + LEN + MODE
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ eth_udp_tx_wrapper   │ ← UDP/IP 头部封装
│                      │ → MAC: 固定目标 MAC 地址
│                      │ → IP checksum + UDP checksum
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ eth_udp_tx_gmii      │ ← GMII (8位数据 @ 125MHz)
│                      │ → GMII TX: TXD[7:0], TXEN, TXER
│                      │ → CRC32 计算
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ gmii_to_rgmii        │ ← GMII→RGMII 转换
│                      │ → RGMII: TXD[3:0] DDR @ 125MHz
│                      │ → TX_CLK = 125MHz (占空比调整)
└──────────┬───────────┘
           │
    [以太网 PHY 芯片]
           │
    上位机 UDP:6102
```

### GMII → RGMII 转换关键

```
GMII:  8 位数据 @ 125 MHz SDR (单边沿采样)
RGMII: 4 位数据 @ 125 MHz DDR (双边沿采样)

转换逻辑:
  RGMII_TXD[3:0] = {GMII_TXD[7:4]} @ 时钟上升沿
                  | {GMII_TXD[3:0]} @ 时钟下降沿
  RGMII_TX_CTL   = GMII_TXEN ^ GMII_TXER  (异或编码)
```

> 以太网仅使用 **TX（发送）**功能，RX 接收未实现。**CAN UDP 通道已禁用**，原因是缺少 RGMII TX 仲裁器避免与 ADC 数据冲突 (`src/debugger_top.v:2340-2345`)。

---

## 第四级: 频率测量

### 硬件频率计数据流

```
ADC 信号 → zero_cross_comparator (过零检测 + 阈值比较)
         → frequency_counter (1 秒计数周期, 50MHz 基准)
         → frequency_tx_controller (CH340 上报)
         → 上位机 SerialManager.frequency_data_received
```

### 频率测量流程

```
上位机发送 0x27 → FPGA:
  1. 立即应答: AA 55 01 27 00 00 CS
  2. 等待 1 秒: 硬件频率计计数
  3. 通过 CH340 发送 8 字节频率数据:
     [CH1_freq LE32][CH2_freq LE32] (小端 32 位, 单位 Hz)

上位机 SerialManager:
  freq_response_state: IDLE → WAIT_RESPONSE → WAIT_DATA
  接收 8 字节后 → frequency_data_received.emit()
```

> **源码**: `src/scope/ADC/frequency_counter.v`, `src/scope/ADC/frequency_tx_controller.v`

---

## 上位机接收与显示链路

```
UDP :6102 → EthernetReceiver.receive_loop()
  → _parse_packet_fast(): 验证 5A AA → 提取 SEQ/TS/LEN/DATA
  → adc_data_received.emit(raw_data)
      ↓
OscilloscopeTab.on_ethernet_adc_data():
  解交织: CH2 = raw[16::2], CH1 = raw[17::2]
  电压映射: (byte / 255) × 10 - 5 → -5V ~ +5V
      ↓
  ch1_buffer.append(data)  ← RingBuffer(500K 点)
  ch2_buffer.append(data)
      ↓ QTimer(50ms = 20FPS)
  update_display() → curve.setData(data) ← pyqtgraph
  update_fft() → np.fft.rfft(data) → FFT 曲线
```

> **上位机源码**: `src/APP/scope/oscilloscope_tab.py` (~4700 行), `src/APP/core/ethernet_receiver.py` (633 行), `src/APP/utils/ring_buffer.py` (377 行)

---

## 两种工作模式对比

| 特性 | Stream 模式 | Buffer 模式 |
|------|-----------|-----------|
| 命令 | 0x20 mode=0 | 0x20 mode=1 |
| 行为 | 连续采集，UDP 持续发送 | 触发后采集指定点数后自动停止 |
| 触发 | 可选 (0x22 trigger_en) | 可选 (单次) |
| DDR3 | 边写边读流控 | 写满后读取，发完停止 |
| 上位机 | RingBuffer 循环覆盖 | 单次捕获显示 |
| 适用 | 实时波形观察 | 一次性捕获异常事件 |

---

## 关键配置参数汇总

| 参数 | 命令 | 范围 | 默认值 |
|------|:--:|------|:---:|
| 采集模式 | 0x20 | 0=Stream, 1=Buffer | 0 |
| Buffer 大小 | 0x21 | 1 – 2^32 | 10000 |
| 采样率分频 | 0x26 | 1 – 2^32 | 1 (50MSPS) |
| 触发电平 | 0x22 byte2 | 0–255 | 128 (中点) |
| 触发边沿 | 0x22 byte1 | 0=上升, 1=下降 | 0 |
| 通道使能 | 0x28 | CH1/CH2 独立 | 双通道使能 |
