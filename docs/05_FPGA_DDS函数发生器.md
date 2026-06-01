# FPGA — DDS 函数发生器

**核心源文件**:  
- `src/dds/DDS_Module_Dual.v` (344 行) — 双通道 DDS 核心  
- `src/dds/DDS_Param_Controller.v` (366 行) — CDC 参数解析控制器  
- `src/dds/arb_wave_ram_simple.v` — 任意波形双端口 RAM  
- `src/dds/sin_rom_a8d8.v` 等 5 个 — 波形查找表 ROM

---

## 核心算法：32 位相位累加器

DDS（直接数字频率合成）通过相位累加器实现精确频率控制，无需模拟振荡器。

```
原理:
  freq_word = (f_target_hz × 2^32) / f_clk
  phase_acc[n+1] = phase_acc[n] + freq_word    (每个时钟周期累加)
  rom_addr = phase_acc[31:24]                   (取高 8 位查表)
  DAC_output = ROM[rom_addr]                    (查表输出波形值)
```

**频率精度**:  
- 32 位累加器，时钟 125 MHz
- 频率分辨率 = 125MHz / 2^32 ≈ **0.029 Hz**
- 实际可设定范围: 1 Hz – 50 MHz（受限于 ROM 一个周期至少 2.5 个采样点）

**相位控制**:  
- 9 位相位输入 (0-359 度)
- 转换为 32 位偏移: `phase_offset = degree × 11930465`（`2^32/360 ≈ 11930465`）
- 累加到相位累加器: `phase_total = phase_acc + phase_offset`

## 幅度控制

```
幅度缩放 = (wave_raw × amplitude × 2) / 256
即 wave_scaled = (wave_raw × amplitude) >> 7
```

- amplitude: 0-255 (8 位 DAC 值)
- 输出 = `wave_raw × amplitude / 256`，实现 256 级线性调幅

> **源码**: `src/dds/DDS_Module_Dual.v:82-102` (相位偏移计算), `src/dds/DDS_Module_Dual.v:107-146` (相位累加+ROM地址)

## 波形类型与数据流水线

### 流水线结构（双通道对称）

```
125MHz时钟 ─┬─ 通道A ─┬─ Phase_Accumulator (32位)
            │         ├─ Phase_Offset (32位, 角度转换)
            │         ├─ ROM_Address = Phase[31:24] (8位)
            │         ├─ Wave_ROM (5个实例 + 1个任意波形RAM)
            │         └─ Amplitude_Scale → DAC0_Data
            │
            └─ 通道B ─ 完全相同的并行流水线 → DAC1_Data
```

### 7 种波形生成

| 索引 | 波形 | 实现方式 | ROM |
|:--:|------|---------|:--:|
| 0 | 正弦波 | ROM 查找表 `sin_rom_a8d8` | 256×8 |
| 1 | 方波 | ROM 查找表 `square_wave_rom_a8d8`, 配合占空比 | 256×8 |
| 2 | 三角波 | ROM 查找表 `triangular_rom_a8d8` | 256×8 |
| 3 | 锯齿波 | ROM 查找表 `sawtooth_rom_a8d8` | 256×8 |
| 4 | 反锯齿波 | ROM 查找表 `inv_sawtooth_rom_a8d8` | 256×8 |
| 5 | 脉冲波 | 实时比较: `phase_acc < duty_threshold` → `0xFF` 否则 `0x00` | 无 |
| 6 | 任意波形 | 双端口 RAM `arb_wave_ram_simple`, 256 字节 | RAM |

### 波形选择 MUX

```verilog
// src/dds/DDS_Module_Dual.v:151-186 (伪代码)
case (wave_type)
    3'd0: wave_raw <= wave_sin;           // 正弦 ROM
    3'd1: wave_raw <= wave_square;        // 方波 ROM
    3'd2: wave_raw <= wave_triangle;      // 三角 ROM
    3'd3: wave_raw <= wave_sawtooth;      // 锯齿 ROM
    3'd4: wave_raw <= wave_inv_sawtooth;  // 反锯齿 ROM
    3'd5: wave_raw <= wave_pulse;         // 脉冲(实时比较生成)
    3'd6: wave_raw <= arb_rd_data;        // 任意波形 RAM
    default: wave_raw <= 8'd0;
endcase
```

> **源码**: `src/dds/DDS_Module_Dual.v:151-186` (波形选择MUX), `src/dds/DDS_Module_Dual.v:306-340` (幅度缩放+输出)

## 占空比控制（脉冲 + 方波）

16 位占空比精度 (0-65535, 0.0015% 步进)，通过相位阈值比较实现:

```
duty_threshold = duty_cycle << 16  (左移16位 → 32位)
pulse_out = (phase_acc < duty_threshold) ? 0xFF : 0x00
```

16 位值 32768 = 50% = `0x80000000` 阈值。

> **源码**: `src/dds/DDS_Module_Dual.v:99-100`

## 参数控制器

`DDS_Param_Controller` 处理 14 条 DDS 命令 (0x10-0x1F):

### Payload 缓冲机制

```
Payload 接收: payload_valid 时逐字节写入 payload_buffer[N]
  单字节命令: cmd_done 时直接从 payload_buffer[0] 取值
  多字节命令: cmd_done 时从 payload_buffer[0..N] 拼接 32 位值
  256 字节命令: 逐字节实时写入任意波形 RAM (不等 cmd_done)
```

### 关键命令处理

| 命令 | 处理方式 | 特殊逻辑 |
|------|---------|---------|
| 0x10/0x11 | `wave_type <= payload_buffer[0][2:0]` | 校验范围 0-6 |
| 0x12/0x13 | `freq_word <= {payload_buffer[0..3]}` 大端拼接 | 上位机已计算频率字 |
| 0x14/0x15 | `phase <= payload_buffer[0]` | 0-255 → 0-359° |
| 0x16/0x17 | `amplitude <= payload_buffer[0]` | 直接 DAC 值 |
| 0x18 | `enable_a/b <= payload_buffer[0][0/1]` | 独立控制 |
| 0x19/0x1A | 批量设置: type+freq+phase+amp+offset+duty | 一次 12 字节 |
| 0x1E/0x1F | 实时写入 RAM: `arb_wr_data <= payload_data` | 不等 cmd_done |

> **源码**: `src/dds/DDS_Param_Controller.v:74-366` (完整命令处理)

## 任意波形 RAM

`arb_wave_ram_simple` — 双时钟双端口 256×8 RAM:

```
写接口 (50MHz):
  wr_en_a/b, wr_addr[7:0], wr_data[7:0]
  来源: DDS_Param_Controller (0x1E/0x1F 命令)
  
读接口 (125MHz):
  rd_addr_a/b[7:0] → rd_data_a/b[7:0]
  来源: DDS_Module_Dual ROM地址
```

写入 256 字节后，切换波形类型为 6 即可播放任意波形。

> **源码**: `src/dds/arb_wave_ram_simple.v`

## DAC 输出时序

```
DAC 时钟: DA_Clk = 125MHz 直通
DAC 数据: DA0_Data / DA1_Data = 流水线最后一拍的幅度缩放后值

输出电平映射 (源码依据: DDS_Module_Dual.v 幅度缩放逻辑):
  DAC 0   → ~+4.4V
  DAC 128 → ~0V (中点)
  DAC 255 → ~-4.4V
```

## 上位机 `DDSController` 连接

上位机通过 `SerialManager.send_command()` 发送 CDC 命令:

```
用户调整频率滑块 → DDSController._calc_freq_word()
  → freq_word = (f_hz << 32) / 125_000_000
  → struct.pack('>I', freq_word)  # 大端 4 字节
  → serial_manager.send_command(0x12, payload)  # 通道A频率

单参数发送策略: 每次只发送变化的参数，避免未变化的参数触发瞬时跳变
```

> **上位机源码**: `src/APP/dds/dds_gui_dual.py` (DDSController 类)
