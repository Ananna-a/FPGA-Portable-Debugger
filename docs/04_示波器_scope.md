# ① 示波器 (Scope)

## 源码清单

| 端 | 路径 | 说明 |
|----|------|------|
| FPGA | `src/scope/ADC/adc_capture_stream.v` | ADC 双通道流控，支持 stream/buffer 模式 |
| FPGA | `src/scope/ADC/adc_dual_channel_interleaver.v` | CH1/CH2 交织 (CH2\|CH1 → 16位) |
| FPGA | `src/scope/ADC/frequency_counter.v` | 硬件过零频率计 |
| FPGA | `src/scope/ADC/frequency_tx_controller.v` | 频率数据 CH340 上报 |
| FPGA | `src/scope/ADC/zero_cross_comparator.v` | 过零检测比较器 |
| FPGA | `src/scope/ethernet/trigger_detector.v` | 触发检测（边沿/电平） |
| FPGA | `src/scope/ethernet/adc_eth_tx_controller.v` | DDR3 读出 → UDP 分包 |
| FPGA | `src/scope/ethernet/eth_udp_tx_wrapper.v` | UDP 封装 |
| FPGA | `src/scope/ethernet/eth_udp_gmii/` | GMII UDP TX 引擎 + CRC32 + IP checksum |
| FPGA | `src/scope/ethernet/gmii_rgmii_gmii/` | GMII → RGMII 转换 |
| FPGA | `src/ip/ddr3_ctrl_2port/` | DDR3 双端口控制器 (Gowin IP) |
| 上位机 | `src/APP/scope/oscilloscope_tab.py` | 示波器 Tab (~4700行) |
| 上位机 | `src/APP/core/ethernet_receiver.py` | UDP 接收器 (端口6102) |
| 上位机 | `src/APP/utils/ring_buffer.py` | 线程安全环形缓冲 (500K点) |

## FPGA 数据流

```
ADC_CH1/CH2 (50MHz) → adc_capture_stream → adc_dual_channel_interleaver
                                                            │
    ┌───────────────────────────────────────────────────────┤
    ▼                                                       ▼
trigger_detector                                     DDR3 写端口
(边沿/电平/通道选择)                                  (ddr3_ctrl_2port)
    │                                                       │
    └──→ 触发信号 ──→ DDR3 缓存 ──→ DDR3 读端口 ────────────┘
                                                        │
                                                        ▼
                                              adc_eth_tx_controller
                                              分包 1024B, 帧头 5A AA
                                                        │
                                                        ▼
                                              eth_udp_tx_gmii → RGMII
                                              目标: 192.168.0.3:6102
```

## 上位机数据流

```
UDP:6102 → EthernetReceiver.receive_loop()
    → _parse_packet_fast() 验证 5A AA
    → adc_data_received.emit(raw_data)
        ↓
OscilloscopeTab.on_ethernet_adc_data()
    解析交织: CH2=raw[16::2], CH1=raw[17::2]
    电压映射: (byte/255)*10 - 5 → -5V~+5V
        ↓
    ch1_buffer.append()  ← RingBuffer(500K)
    ch2_buffer.append()
        ↓ QTimer(50ms=20FPS)
    update_display() → curve.setData()  ← pyqtgraph

FFT: Python/NumPy rfft，可选窗函数
频率: FPGA 通过 CH340 每1秒上报8字节
```

## 两种模式

| 模式 | 命令 | 行为 |
|------|------|------|
| Stream | 0x20 mode=0 | 连续采集，UDP 持续发送，上位机 RingBuffer 循环 |
| Buffer | 0x20 mode=1 | 触发后采集指定点数 → DDR3 → UDP 分包，发完自动停 |

## CDC 命令 (0x20-0x2A)

| 命令 | Payload | 说明 |
|------|---------|------|
| 0x20 | [mode] | 0=Stream, 1=Buffer |
| 0x21 | [size 4B LE] | Buffer 大小 |
| 0x22 | [cfg 3B] | 触发配置 |
| 0x23 | — | 启动采集 |
| 0x24 | — | 停止采集 |
| 0x26 | [div 4B LE] | 采样率分频 |
| 0x27 | — | 频率测量 |
| 0x28 | [ch1][ch2] | 通道使能 |
