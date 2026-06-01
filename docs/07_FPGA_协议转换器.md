# FPGA — 协议转换器

协议转换器包含 **6 个子功能模块**，共享 `debugger_top.v` 中的命令路由和 UART 应答系统，但在硬件上独立运行。

---

## 一、序列发生器

**源文件**:  
- `src/protocol/sequence/sequence_param_controller.v` — 参数解析  
- `src/protocol/sequence/sequence_playback_parallel.v` — 并行模式  
- `src/protocol/sequence/sequence_playback_serial_v3.v` — 串行模式

### 架构

序列发生器与逻辑分析仪共享 `logic_analyzer_top` 实例 (`src/debugger_top.v:2052-2063`)，同一模块输出同时作为序列输出 (`SEQ_OUT[7:0]`) 和逻辑分析仪控制信号。

### 两种模式

| 模式 | 命令 | 行为 |
|------|:--:|------|
| **并行** | 0x30 (旧) | 8 通道同时输出 1 字节 × N 长度，共享频率 |
| **串行** | 0x31 (旧) / 0x40-0x43 (新) | 每通道独立 256 位序列缓存，独立频率控制 |

### 旧协议 (0x30-0x34)

```
0x30: 并行模式配置
  Payload: [频率 BE32][长度 1B][数据 N]
  频率: 全局共享，所有通道相同步进速率

0x31: 串行模式配置
  Payload: [通道掩码 1B][频率 BE32][长度 1B][数据 N]

0x32: 频率控制 (全局, 并行+串行)
0x33: 启动输出 (开始步进播放)
0x34: 停止输出
```

### 新协议 (0x40-0x43) — 逐通道独立

```
0x40: 配置通道参数
  Payload: [通道ID 1B][频率 BE32 4B][长度 1B]
  每个通道独立频率和长度

0x41: 写入序列数据
  Payload: [通道ID 1B][地址 1B][数据 1B]
  逐字节写入选中的通道缓存

0x42: 使能控制
  Payload: [掩码 1B]
  bit0-7 对应 CH0-7 的使能/禁用

0x43: 全局复位 — 清除所有通道缓存和配置
```

> **新协议优势**: 8 通道可以同时以不同频率输出不同序列模式，不再受全局频率限制。

### 输出引脚

`SEQ_OUT[7:0]` → 8 个 GPIO 引脚 (CST 约束)，每个 bit 对应一个通道的输出状态。

---

## 二、PWM 控制器

**源文件**:  
- `src/protocol/pwm/pwm_generator.v` (114 行) — 单通道 PWM 核心  
- `src/protocol/pwm/pwm_param_controller.v` — 8 通道参数控制

### 核心算法: DDS 相位累加器 + 阈值比较

PWM 复用 DDS 的相位累加器原理，通过比较相位值与占空比阈值生成 PWM 波形:

```
频率控制字计算:
  freq_word = (f_hz × 2^32) / 50_000_000

占空比阈值计算 (16位精度):
  duty_threshold = duty_cycle << 16  // 左移 16 位 → 32 位

PWM 生成:
  phase_acc += freq_word             // 每个 50MHz 时钟累加
  compare_result = (phase_acc < duty_threshold)
  pwm_out = registered compare_result  // 三级流水线去毛刺
```

### 三级流水线消毛刺

```
Stage 1 (寄存输入):
  phase_acc_r <= phase_acc
  duty_threshold_r <= duty_threshold

Stage 2 (比较操作):
  compare_result <= (phase_acc_r < duty_threshold_r)

Stage 3 (输出驱动):
  pwm_out <= compare_result
```

> **设计原因**: 组合逻辑的比较器输出可能产生纳秒级毛刺，三级流水线确保输出稳定无毛刺。
> **源码**: `src/protocol/pwm/pwm_generator.v:66-100` (相位累加器 + 瀑布线)

### 规格

| 参数 | 值 |
|------|-----|
| 通道数 | 8 路独立 |
| 频率范围 | 1 Hz – 1 MHz |
| 占空比精度 | 16 位 (65536 级, 0.0015%) |
| 系统时钟 | 50 MHz |
| 输出引脚 | `PWM_OUT[7:0]` (CST 约束到 8 个 GPIO) |

### CDC 命令

```
0x50: PWM 配置
  Payload: [通道ID 1B][频率 BE32 4B][占空比 2B]
  占空比: 0-65535 (16位), 对应 0%-100%

0x51: PWM 使能
  Payload: [掩码 1B]
  bit0-7 对应 CH0-7

0x52: PWM 停止
  Payload: 无
  所有通道停止，相位累加器清零
```

---

## 三、CAN 总线控制器

**源文件**:  
- `src/protocol/devices/can/can_controller.v` (439 行) — CAN 顶层  
- `src/protocol/devices/can/rtl/can_top.v` — CAN IP 核 (fpga-can-main 移植)  
- `src/protocol/devices/can/rtl/can_level_bit.v` — 位级时序  
- `src/protocol/devices/can/rtl/can_level_packet.v` — 帧级封装  
- `src/protocol/devices/can/can_eth_udp_wrapper.v` — CAN UDP 封装 (已禁用)

### 硬件接口

```
CAN_TX → SIT1042AQT/3 → CAN_H / CAN_L (差分总线)
CAN_RX ← SIT1042AQT/3 ←
```

收发器: SIT1042AQT/3，兼容 SJA1000，最大 1 Mbps。  
引脚: CAN_TX=G2, CAN_RX=H2 (CST 约束)。

### 协议支持

- **CAN 2.0A** — 标准帧 (11 位 ID)
- **CAN 2.0B** — 扩展帧 (29 位 ID)

### 波特率配置

基于 50MHz 系统时钟的可选波特率:

| 索引 | 波特率 | Division | 说明 |
|:--:|------|------|------|
| 0 | 1 Mbps | 50 | 固定默认 (当前) |
| 1 | 500 kbps | 100 | |
| 2 | 100 kbps | 500 | |
| 3 | 10 kbps | 5000 | |
| 4 | 5 kbps | 10000 | |

> 命令 0xC0 用于运行时选择波特率，`can_controller.v:88-100` 内注释注明当前固定为索引 3 (1MHz)。

### 数据流 (发送 + 接收)

```
上位机发送 CAN 帧 (0xC1):
  CDC → cdc_cmd_parser → can_controller
    → can_top (CAN IP核) → SIT1042 → CAN BUS

CAN BUS 接收到帧:
  SIT1042 → can_top → can_controller
    → can_rx_report_valid + can_rx_report_data
    → uart_tx_mux (CH_CAN_RX, 优先级 3)
    → uart_byte_tx → CH340 → 上位机

上位机 SerialManager:
  首字节 0x00 → 标准帧 → can_data_received Signal
  首字节 0x01 → 扩展帧 → can_data_received Signal
```

### 接收上报格式 (CH340)

```
标准帧 (首字节 0x00):
  [0x00][ID_H 1B][ID_L|DLC 1B][data0..7 0-8B]

扩展帧 (首字节 0x01):
  [0x01][ID3 1B][ID2 1B][ID1 1B][ID0|DLC 1B][data0..7 0-8B]

DLC 编码: 低 4 位为数据长度 (0-8)
```

### 过滤器

```
0xC2: 设置滤波器
  Payload: [filter_id 4B]
  当前: 全接收模式 (filter_id = 0x000, mask = 0x000)
```

> **源码**: `src/protocol/devices/can/can_controller.v:35-38` (默认参数: `RX_ID_SHORT_FILTER = 0`)

---

## 四、I2C / OLED

**源文件**:  
- `src/protocol/devices/i2c_spi/i2c_generic_controller.v` — 通用 I2C 主机  
- `src/protocol/devices/i2c_spi/i2c_control.v` — I2C 底层状态机  
- `src/protocol/devices/i2c_spi/i2c_bit_shift.v` — 位级操作  
- `src/protocol/devices/i2c_spi/oled_controller.v` — OLED 控制器  
- `src/protocol/devices/i2c_spi/oled_init.v / Oled_Clear.v / Oled_On.v / Oled_Show_control.v` — OLED 操作子模块  
- `src/protocol/devices/i2c_spi/font_data.v` — 内置 6×8 ASCII 字库

### 架构

```
I2C 总线 (i2c_sda, i2c_scl) 由两个控制器共享:

  通用 I2C 主机        OLED 控制器
  (0x70 命令)          (0x73-0x76 命令)
       │                    │
       └──── i2c_scl ──────┘
       └──── i2c_sda ──────┘ (OD输出, 可线与)

切换逻辑: i2c_generic_cmd_req 标志控制
  src/debugger_top.v:2110-2111
```

### OLED 命令

| 命令 | 功能 |
|:--:|------|
| 0x73 | SSD1306 初始化序列 (oled_init) |
| 0x74 | 清屏 (oled_clear) |
| 0x75 | 全亮测试 (oled_on) |
| 0x76 | 显示文本: `[x 1B][y 1B][text N]` |

> 设备地址: 0x3C (SSD1306 默认)。显示器 128×64 像素，使用内置 6×8 ASCII 字库 `font_data.v`。

### 通用 I2C

```
0x70: 通用 I2C 主机写入
  Payload: [dev_addr 1B][reg_addr 1B][data N]
  
流程:
  1. START → dev_addr + W
  2. reg_addr (1 字节寄存器地址)
  3. data[0..N-1] (N 字节数据)
  4. STOP

应答数据: uart_response_tx.data ← i2c_generic_response
  (读取/扫描结果回填)
```

---

## 五、SPI Flash (W25Q128)

**源文件**:  
- `src/protocol/devices/i2c_spi/SPI/spi_controller.v` — SPI 顶层控制器  
- `src/protocol/devices/i2c_spi/SPI/spi_master_core.v` — SPI 物理层

### 引脚

| 信号 | 引脚 | 说明 |
|------|------|------|
| `spi_sclk` | B1 | SPI 时钟 |
| `spi_cs` | B2 | 片选 (低有效) |
| `spi_mosi` | M17 | 主出从入 |
| `spi_miso` | A1 | 主入从出 |

### 命令集

| 命令 | 功能 | Payload |
|:--:|------|---------|
| 0x80 | SPI 配置 | `[mode 1B][speed 1B]` — CPOL/CPHA + 分频系数 |
| 0x81 | SPI 通用传输 | `[len 1B][data N]` |
| 0x82 | 读 JEDEC ID | 无 (返回 3 字节) |
| 0x83 | Flash 读取 | `[addr 3B][len 1B]` |
| 0x84 | Flash 写入 | `[addr 3B][data N]` |
| 0x85 | 扇区擦除 | `[addr 3B]` — 4KB |
| 0x86 | 全片擦除 | 无 — Chip Erase (~40s) |
| 0x87 | 读状态寄存器 | 无 |

### 流式数据回传

Flash 读取命令 (0x82/0x83/0x87) 的响应数据通过 CH340 流式发送:

```
spi_response_valid → SPI数据FIFO (256 字节缓冲)
  → SPI_TX_SEND_DATA 状态机:
    等待应答帧 → 逐字节从FIFO取 → 通过 uart_tx_mux (CH_SPI) 发送
  → SPI_TX_IDLE
```

> **源码**: `src/debugger_top.v:2350-2449` (SPI 数据发送状态机)

---

## 六、DS18B20 温度传感器

**源文件**:  
- `src/protocol/devices/i2c_spi/DS18B20/ds18b20_controller.v` — 1-Wire 主机  
- `src/protocol/devices/i2c_spi/DS18B20/ds18b20_temp_display.v` — BCD 温度转换

### 1-Wire 协议

单总线 (`ds18b20_dq` 双向 OD 输出)，标准 DS18B20 时序:
- 复位脉冲 + 应答检测
- ROM 命令 (Skip ROM = 0xCC)
- 功能命令 (Convert T = 0x44, Read Scratchpad = 0xBE)
- 12 位精度，转换时间 ~750ms

### 命令

| 命令 | 功能 | 说明 |
|:--:|------|------|
| 0xA0 | 单次读取 | 触发一次转换 + 读取温度 |
| 0xA1 | 连续监控 | `[interval 1B]` 定时重复读取 |
| 0xA2 | 停止监控 | 停止周期性读取 |

### 数据上报与显示

- **CH340 上报**: 2 字节温度值 (通过 uart_tx_mux DS18B20 通道)
- **数码管显示**: BCD 转换 → 数码管 (格式: `±XX.X°C`，带小数点)
- 显示优先级: DS18B20 读取中 > ADC 频率 > 默认显示

> **源码**: `src/debugger_top.v:1809-1892` (温度显示逻辑 + BCD转换)
