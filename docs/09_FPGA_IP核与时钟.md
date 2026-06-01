# FPGA — IP 核与时钟系统

**目录**: `src/ip/` — 全部 9 个 Gowin IP 核

> 所有 IP 由 Gowin IP Generator 生成，手动修改后需重新生成。`temp/` 子目录为 IP 生成器中间文件。

---

## 一、时钟生成 — PLL × 2

### gowin_pll (系统主时钟)

```
输入: clk (50MHz 外部晶振)
输出:
  clkout0: 125 MHz → DDS_Module_Dual (DDS 核心)
                   → eth_udp_tx_gmii (GMII TX 引擎)
                   → gmii_to_rgmii (RGMII 转换)
  clkout1:  50 MHz → ADC 芯片 (adc_clk_180, 180° 相位偏移)
  pll_lock         → sys_rst = ~reset_n | ~pll_lock

实例化:
  Gowin_PLL u_pll_dds_clk(
      .clkin(clk),           // 50MHz
      .clkout0(clk125m),     // 125MHz
      .clkout1(adc_clk_50m), // 50MHz @ 180°相位
      .lock(pll_lock)
  );
  // src/debugger_top.v:113-120
```

**180° 相位偏移的作用**: ADC 芯片的数据在时钟上升沿后稳定，FPGA 在时钟下降沿（180度）采样，确保采样窗口居中于数据稳定期。

### ddr_pll (DDR3 专用)

```
输入: clk (50MHz)
输出:
  clkout2: 400 MHz → DDR3 IP 核参考时钟
  pll_lock_ddr → DDR3 初始化条件

使能控制:
  enclk2 = pll_stop  ← DDR3 IP 核反馈信号 (动态门控)
  当 DDR3 IP 停止时, pll_stop=0 → 关闭 400MHz 输出 (省电)
```

> **源码**: `src/debugger_top.v:127-138` (ddr_pll 例化)

---

## 二、DDR3 存储子系统

### 组件链

```
ddr3_ctrl_2port (双端口控制器)
  ├── 端口 0 (写):  128位 × Burst 128 → DDR3 写入
  ├── 端口 1 (读):  128位 × Burst 128 → DDR3 读出
  └── 仲裁: 写优先 (write priority)
        │
ddr3_memory_interface (物理层接口)
  ├── AXI 命令队列
  ├── DDR3 PHY: 初始化 + 校准 + 时序控制
  └── 对外接口: O_ddr_addr[13:0], O_ddr_ba[2:0],
                 O_ddr_* 控制信号, IO_ddr_dq[15:0],
                 IO_ddr_dqs[1:0], IO_ddr_dqs_n[1:0]
```

### DDR3 规格

| 参数 | 值 |
|------|-----|
| 总容量 | 256 MB |
| 总线位宽 | 16 位 (IO_ddr_dq[15:0]) |
| 参考时钟 | 400 MHz (来自 ddr_pll) |
| 内部速率 | DDR3-800 (400MHz × 2) |
| Burst 长度 | 128 (每个读/写命令传输 128 拍) |
| 地址范围 | 0 – 268435455 (28 位) |
| IP 核盘 | ddr3_ctrl_2port + ddr3_memory_interface |

### 跨时钟域 FIFO 对

```
写入通道 (50MHz → DDR3 时钟域):
  adc_data_16bit (50MHz) → wr_data_fifo (异步FIFO) → DDR3 写端口

读出通道 (DDR3 时钟域 → 125MHz):
  DDR3 读端口 → rd_data_fifo (异步FIFO) → adc_eth_tx_controller (125MHz)
```

> **源码**: `src/ip/wr_data_fifo/`, `src/ip/rd_data_fifo/`

### 初始化序列

```
上电 → ddr_pll 锁定 (pll_lock_ddr=1)
  → DDR3 IP 初始化 (ZQ校准 + 模式寄存器配置)
  → ddr3_init_done = 1
  → LED[1] 亮 → 系统可以开始使用 DDR3
```

---

## 三、FIFO 缓冲器 × 5

### fifo_in — CDC 接收异步 FIFO

```
用途: FX2 48MHz → 系统 50MHz 跨时钟域
参数: 8位宽, ~512 深度
写入: fx2_ifclk 时钟域, WrEn=rx_valid
读出: clk (50MHz) 时钟域, RdEn=~rx_empty
输出: Q=rx_fifo_out, Empty=rx_empty, Full=rx_full
```

> **源码**: `src/ip/fifo_in/fifo_in.v`, `src/debugger_top.v:635-645` (例化)

### fifo_top — 未实例化（已废弃）

原用于 FPGA → FX2 的 USB 上行通道，现已改用 CH340 UART 替代 USB 上行。

### fifo_sc_hs — 未实例化

原预留同步高速 FIFO。

### wr_data_fifo — DDR3 写数据缓冲

```
用途: 50MHz ADC 数据 → DDR3 时钟域
参数: 128位宽, ~256 深度 (支持 Burst 128)
```

### rd_data_fifo — DDR3 读数据缓冲

```
用途: DDR3 时钟域 → 125MHz 以太网域
参数: 16位宽, ~512 深度
```

---

## 四、时钟域全景图

```
    外部 50MHz 晶振
           │
    ┌──────┼──────────────────────┐
    │      │                      │
    ▼      ▼                      ▼
 gowin_pll  ddr_pll            clk (直通)
    │         │                  │
    │         └── 400MHz ──→ DDR3 PHY
    │
    ├── 125MHz ──→ DDS_Module_Dual
    │           ──→ gmii_to_rgmii
    │           ──→ eth_udp_tx_gmii
    │           ──→ arb_wave_ram (读时钟)
    │
    └──  50MHz (180°) ──→ ADC 芯片 (adc_clk_out_a/b)


    fx2_ifclk (48MHz, 外供) ──→ FX2_CDC_Core (隔离时钟域)
         │
         └──→ fifo_in ──→ 50MHz 域 (跨时钟域)


    系统域 50MHz (clk):
      ├── debugger_top 主控制
      ├── cdc_cmd_parser, uart_* (通信链)
      ├── DDS_Param_Controller (DDS 参数)
      ├── adc_capture_stream (ADC 采集)
      ├── logic_analyzer_capture (LA 采集)
      ├── PWM, CAN, I2C, SPI, DS18B20 (外设)
      └── arb_wave_ram (写时钟)
```

### 关键跨时钟域汇总

| 路径 | 源域 | 目的域 | 方案 | 亚稳态防护 |
|------|:--:|:--:|------|:--:|
| FX2→系统 | 48MHz | 50MHz | `fifo_in` 异步FIFO | ✅ Gowin IP 内置 |
| 系统→DDS | 50MHz | 125MHz | `arb_wave_ram` 双时钟RAM | ✅ FPGA 分布式RAM |
| ADC→DDR3 | 50MHz | 400MHz | `wr_data_fifo` 异步FIFO | ✅ Gowin IP 内置 |
| DDR3→以太网 | 400MHz | 125MHz | `rd_data_fifo` 异步FIFO | ✅ Gowin IP 内置 |
| ADC状态 | 50MHz | 125MHz | 2 级同步寄存器 | ✅ `debugger_top.v:296-307` |
| CH340→系统 | ~115kbps | 50MHz | `uart_byte_rx` 16倍过采样 | ✅ 同步器+过采样 |
| ADC数据 | 50MHz | 125MHz | 2 级同步寄存器 | ⚠️ 可能采样到中间值 |

> **ADC 数据跨时钟域警告** (`src/debugger_top.v:309-351`): 8 位并行 ADC 数据在 50MHz 域变化，直接用 2 级同步器采样到 125MHz 域可能捕获到未稳定的中间值。但这仅用于 Bode 分析（统计平均），对示波器主通道影响较小。

---

## 五、IP 核清单

| IP | 类型 | 用途 | 状态 |
|----|------|------|:--:|
| `gowin_pll` | PLL | 系统主时钟 (125MHz + 50MHz) | ✅ |
| `ddr_pll` | PLL | DDR3 参考 (400MHz) | ✅ |
| `ddr3_ctrl_2port` | DDR3 Controller | 双端口 DDR3 访问 | ✅ |
| `ddr3_memory_interface` | DDR3 PHY | 物理层时序 | ✅ |
| `fifo_in` | Async FIFO | CDC 接收跨域 (48→50) | ✅ |
| `fifo_sc_hs` | Sync FIFO | 高速同步缓冲 | ❌ 未例化 |
| `fifo_top` | Sync FIFO | USB 上行缓冲 | ❌ 已废弃 |
| `wr_data_fifo` | Async FIFO | DDR3 写缓冲 | ✅ |
| `rd_data_fifo` | Async FIFO | DDR3 读缓冲 | ✅ |

> PLL/FIFO/DDR3 修改需用 Gowin IP Generator 重新生成，不可直接编辑 `.v` 文件。

---

## 六、全局复位策略

```
sys_rst = ~reset_n | ~pll_lock
  = 外部按键复位 OR 主 PLL 未锁定

有效电平: 高复位 (posedge sys_rst)
  → 大部分 always 块使用 posedge sys_rst 作为异步复位
  → 部分模块使用 negedge sys_rst 通过 .Rst_n(~sys_rst) 转换

复位顺序:
  1. 外部 reset_n=0 → sys_rst=1 → 全系统复位
  2. PLL 锁定 → pll_lock=1 → sys_rst=0 → 系统解除复位
  3. DDR3 PLL 独立 → pll_lock_ddr 控制 DDR3 IP 初始化
```

> **源码**: `src/debugger_top.v:141-142`
