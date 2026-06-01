# FPGA 顶层架构 — debugger_top.v

**源文件**: `src/debugger_top.v` (4157 行)  
**功能**: 唯一 FPGA 顶层模块，实例化所有子模块，实现命令路由、电源管理、I/O 连接。

---

## 端口总览

`debugger_top` 对外暴露 12 类物理接口，约 80 根 I/O 信号：

| 接口类别 | 端口信号 | 方向 | 说明 |
|---------|---------|:--:|------|
| 时钟复位 | `clk`(50MHz), `reset_n` | I | 外部 50MHz 晶振 + 按键复位 |
| FX2 USB | `fx2_fdata[7:0]`, `fx2_flagb/c`, `fx2_ifclk`, `fx2_faddr[1:0]`, `fx2_sloe/slwr/slrd/pkt_end/slcs` | I/O | 双向 SlaveFIFO, 48MHz 时钟 |
| CH340 UART | `uart_tx`, `uart_rx` | O, I | 应答+数据+蓝牙透传 |
| 蓝牙 UART | `bt_tx`, `bt_rx` | O, I | HC-06 模块直连 |
| 调试 | `led[7:0]`, `SW`, `sh_cp/st_cp/ds` | O, I, O | 状态指示 + 数码管 |
| DAC | `DA0_Data[7:0]`, `DA1_Data[7:0]`, `DA0_Clk`, `DA1_Clk` | O | 双通道 8 位 DAC |
| 序列输出 | `SEQ_OUT[7:0]` | O | 8 通道序列发生器 |
| PWM 输出 | `PWM_OUT[7:0]` | O | 8 通道 PWM |
| 逻辑输入 | `LOGIC_IN[7:0]` | I | 逻辑分析仪 8 通道数字输入 |
| I2C | `i2c_sda`, `i2c_scl` | I/O, O | OLED SSD1306 控制 |
| SPI | `spi_cs`, `spi_sclk`, `spi_mosi`, `spi_miso` | O, O, O, I | W25Q128 Flash |
| DS18B20 | `ds18b20_dq` | I/O | 1-Wire 温度传感器 |
| CAN | `can_tx`, `can_rx` | O, I | SIT1042 收发器 |
| ADC | `adc_data_a/b[7:0]`, `adc_clk_out_a/b` | I, O | 双通道并行 ADC |
| 以太网 | `rgmii_tx_clk`, `rgmii_txd[3:0]`, `rgmii_txen`, `eth_rst_n` | O | RGMII 仅 TX |
| DDR3 | `O_ddr_addr[13:0]`, `O_ddr_ba[2:0]`, `O_ddr_*`, `IO_ddr_dq[15:0]`, `IO_ddr_dqs[1:0]` 等 | O/I/O | 256MB DDR3 SDRAM |

> **源码**: `src/debugger_top.v:8-100` — 完整端口声明

---

## 内部信号体系

### 系统全局信号

```
clk (50MHz) ──────────── 系统主时钟域 (几乎所有逻辑)
  ├── gowin_pll → clk125m (125MHz) ── DDS + 以太网 GMII
  │            → adc_clk_50m (50MHz, 180°) ── ADC 芯片
  └── ddr_pll  → loc_clk400m (400MHz) ── DDR3 参考

sys_rst = ~reset_n | ~pll_lock
  → 全局复位: 外部按键 OR PLL未锁定 → 所有模块复位
  → 有效电平: 高复位 (posedge sys_rst)
fx2_ifclk (48MHz) ──── FX2_CDC_Core 时钟域 (隔离)
```

### 通信链路信号

```
CDC接收链 (48MHz → 50MHz):
  FX2_CDC_Core.data_valid + fifo_data_in (48MHz)
    → fifo_in (异步FIFO, Gowin IP)
    → rx_fifo_out + ~rx_empty (50MHz)
    → cdc_cmd_parser.rx_data + rx_valid

cdc_cmd_parser 输出:
  cmd_code[7:0]      命令码 (S_CMD 状态锁存)
  cmd_length[15:0]   Payload 长度
  cmd_payload[7:0]   Payload 逐字节
  cmd_payload_valid  每字节有效脉冲
  cmd_done           整帧解析完成脉冲
  cmd_error          校验错误脉冲
  cmd_valid_pulse    命令码有效脉冲 (S_CMD 状态)

UART上行链 (50MHz → CH340 115200bps):
  子模块数据 → uart_tx_mux (7通道仲裁)
    → uart_byte_tx (波特率分频 + 8N1 帧)
    → uart_tx → CH340 → PC
```

---

## 命令路由机制

### 三层命令处理架构

```
层1: cdc_cmd_parser (纯硬件帧解析)
  输入: USB CDC 字节流
  输出: cmd_code, cmd_payload, cmd_done
  作用: 帧同步、CS校验、Payload提取
  
层2: cmd_valid_flag (组合逻辑白名单)
  输入: cmd_code_latched (S_CMD锁存的命令码)
  输出: cmd_valid_flag (1=合法命令)
  作用: 防止非法命令码导致系统行为异常
  
层3: main always 块 (命令执行)
  触发: cmd_done && cmd_valid_flag
  动作: case(cmd_code_latched) → 参数寄存器更新 → 子模块触发
```

### cmd_valid_flag 白名单 (60+ 命令码)

```verilog
// src/debugger_top.v:1276-1389
always @(*) begin
    case (cmd_code_latched)
        8'h00, 8'h03, 8'h04, 8'h05,              // 系统
        8'h10-8'h1F (DDS 14条),                   // DDS
        8'h20-8'h2A (ADC 11条),                   // 示波器
        8'h30-8'h34 (序列旧协议 5条),              // SEQ
        8'h40-8'h43 (序列新协议 4条),              // SEQ v2
        8'h50-8'h52 (PWM 3条),                    // PWM
        8'h60-8'h68 (LA/DSA 9条),                 // 逻辑分析
        8'h70, 8'h73-8'h76 (I2C/OLED 5条),        // I2C/OLED
        8'h80-8'h87 (SPI 8条),                    // SPI
        8'h90-8'h91 (蓝牙 2条),                    // BT
        8'hA0-8'hA2 (DS18B20 3条),                // 温度
        8'hC0-8'hC4 (CAN 5条),                    // CAN
        8'hB0-8'hB3: cmd_valid_flag = 1'b1;       // Bode
        default: cmd_valid_flag = 1'b0;
    endcase
end
```

---

## 关键实现细节

### 1. Payload 参数锁存模式

每个有 payload 的命令使用"先锁存-后更新"模式，解决 `param_buffer` 在 `cmd_done` 时被清零导致的竞态：

```
cmd_payload_valid 期间:
  实时锁存到独立锁存器 (sample_div_latch, la_sample_div_latch 等)
  
cmd_done && cmd_valid_flag 时:
  将锁存器的值一次性更新到目标寄存器
```

示例 — ADC 采样率分频 (0x26):

```verilog
// src/debugger_top.v:722-747
if (cmd_code_latched == 8'h26 && cmd_payload_valid) begin
    case (payload_counter)
        16'h0: sample_div_latch[7:0]   <= cmd_payload;
        16'h1: sample_div_latch[15:8]  <= cmd_payload;
        16'h2: sample_div_latch[23:16] <= cmd_payload;
        16'h3: sample_div_latch[31:24] <= cmd_payload;
    endcase
end
if (cmd_done && cmd_valid_flag && cmd_code_latched == 8'h26)
    adc_sample_div <= sample_div_latch;  // 一次性更新
```

同样模式用于: 逻辑分析仪 (`0x60/0x61/0x62`, `src/debugger_top.v:749-830`), Bode (`0xB0`, `src/debugger_top.v:832-886`)

### 2. 应答帧生成机制

```
cmd_finish 信号组合 (src/debugger_top.v:1441-1442):

cmd_finish = simple_cmd_finish       // 简单命令 (cmd_done_posedge_d + 非子模块)
           | cmd_error_posedge       // 校验错
           | oled_cmd_done           // OLED 子模块完成
           | i2c_generic_cmd_done    // 通用 I2C 完成
           | spi_cmd_done            // SPI 完成
           | ds18b20_cmd_done        // DS18B20 完成
           | can_response_valid;     // CAN 应答

response_valid = cmd_finish 触发单周期脉冲
  → uart_response_tx 组装 7 字节帧
  → uart_tx_mux 仲裁后输出
```

> 子模块命令 (cmd_code 0x66-0x68, 0x70-0x76, 0x80-0x87, 0xA0-0xA2, 0xC0-0xC4) 不使用 `simple_cmd_finish`，而是子模块自己产生 `_cmd_done` 信号触发应答。

### 3. I2C 总线仲裁

I2C 总线被通用 I2C 主机和 OLED 控制器共享。顶层通过命令锁存机制切换：

```verilog
// src/debugger_top.v:2110-2111
assign i2c_scl = i2c_generic_cmd_req ? i2c_generic_scl : oled_i2c_scl;
assign i2c_sda = i2c_generic_cmd_req ? i2c_generic_sda : oled_i2c_sda;
```

`i2c_generic_cmd_req` 在 `cmd_done && cmd_code==0x70` 时置 1，在 `i2c_generic_cmd_done` 时清 0。

### 4. DDS 通道 A 多路复用（Bode 扫频）

```verilog
// src/debugger_top.v:1995-1999
assign freq_word_a_mux  = bode_dds_enable ? bode_dds_freq_word : freq_word_a;
assign phase_a_mux      = bode_dds_enable ? bode_dds_phase     : phase_a;
assign amplitude_a_mux  = bode_dds_enable ? bode_dds_amplitude : amplitude_a;
assign enable_a_mux     = bode_dds_enable ? 1'b1               : enable_a;
assign wave_type_a_mux  = bode_dds_enable ? 3'd0               : wave_type_a;  // Bode 强制正弦
```

当 Bode 扫频激活时，DDS 通道A 被 Bode 分析仪接管：强制正弦波，频率由扫频算法控制。

### 5. 数码管显示优先级

```
DS18B20读取中 > ADC采集中(频率) > 频率测量完成 > 默认显示 "hifpga"

src/debugger_top.v:1889-1892
hex_display_final = ds18b20_reading_active  → temp_bcd
                  | adc_stream_active       → freq_display_bcd
                  | freq_display_active     → freq_display_bcd
                  | default                 → 32'h00B1_FC6A  // "hifpga"
```

频率显示每 2 秒自动在 CH1/CH2 之间切换（`src/debugger_top.v:1853-1877`）。

---

## 实例化树 (完整)

```
debugger_top
├── Gowin_PLL u_pll_dds_clk                        (gowin_pll)
├── ddr_pll u_ddr_pll                               (ddr_pll)
├── FX2_CDC_Core fx2_cdc                            (comm/)
├── fifo_in rx_fifo                                 (ip/fifo_in)
├── cdc_cmd_parser cmd_parser                       (comm/)
├── uart_tx_mux u_uart_tx_mux                       (comm/)
├── uart_response_tx u_uart_response_tx              (comm/)
├── uart_byte_tx u_uart_byte_tx                     (comm/)
├── bt_uart_bridge u_bt_bridge                      (common/)
├── hc595_driver hc595_drv                          (common/)
├── hex8_ext hex8_inst                              (common/)
├── ds18b20_temp_display temp_conv                  (common/)
├── DDS_Param_Controller dds_param_ctrl             (dds/)
├── arb_wave_ram_simple u_arb_ram                   (dds/)
├── DDS_Module_Dual dds_dual_inst                   (dds/)
├── pwm_param_controller pwm_ctrl_inst              (protocol/pwm/)
├── logic_analyzer_top logic_analyzer_inst           (protocol/sequence/ — 序列发生器)
├── i2c_generic_controller u_i2c_generic_controller (protocol/devices/i2c_spi/)
├── oled_controller u_oled_controller               (protocol/devices/i2c_spi/)
├── spi_controller u_spi_controller                 (protocol/devices/i2c_spi/SPI/)
├── ds18b20_controller u_ds18b20_controller         (protocol/devices/i2c_spi/DS18B20/)
├── can_controller u_can_controller                 (protocol/devices/can/)
├── can_udp_tx u_can_udp_tx                         (protocol/devices/can/)
├── digital_signal_ctrl u_digital_signal_ctrl       (logic_analyzer/)
├── [ADC模块] adc_capture_stream × 2                (scope/ADC/)
├── [ADC模块] adc_dual_channel_interleaver           (scope/ADC/)
├── [ADC模块] frequency_counter                     (scope/ADC/)
├── [ADC模块] frequency_tx_controller               (scope/ADC/)
├── [ADC模块] zero_cross_comparator                 (scope/ADC/)
├── [ADC模块] trigger_detector                      (scope/ethernet/)
├── [ADC模块] adc_eth_tx_controller                 (scope/ethernet/)
├── [ADC模块] eth_udp_tx_wrapper                    (scope/ethernet/)
├── [ADC模块] gmii_to_rgmii                         (scope/ethernet/gmii_rgmii_gmii/)
├── ddr3_ctrl_2port ddr3_ctrl_inst                  (ip/ddr3_ctrl_2port/)
├── ddr3_memory_interface ddr3_memory_inst           (ip/ddr3_memory_interface/)
├── wr_data_fifo                                     (ip/wr_data_fifo)
├── rd_data_fifo                                     (ip/rd_data_fifo)
├── [LA模块] logic_analyzer_capture                  (logic_analyzer/)
├── [LA模块] digital_signal_analyzer                 (logic_analyzer/)
└── [BODE模块] 全部output assign=0 (未例化)

总计: ~30 个子模块实例
```

---

## 调试与状态 LED

8 个 LED 的状态分配（当前版本含 Bode 调试）：

| LED | 信号 | 说明 |
|:---:|------|------|
| 0 | `led_cnt[24]` | 心跳灯，~1.5 Hz |
| 1 | `ddr3_init_done` | DDR3 初始化完成 |
| 2 | `bode_sweep_active` | Bode 扫频进行中 |
| 3 | `bode_iq_valid` | IQ 解调有效脉冲 |
| 4 | `adc_ch1_stream_active` | ADC 采集状态 |
| 5 | `bode_formatter_busy` | Bode 数据格式化忙 |
| 6 | `uart_tx_busy` | UART 发送忙 |
| 7 | `bode_uart_tx_send_active` | Bode UART 发送请求 |

> 注: LED[2-7] 含较多 Bode 调试信号（当前调试阶段），产品化后应调整为模块状态指示。
