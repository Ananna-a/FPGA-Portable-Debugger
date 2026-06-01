# ② 函数发生器 (DDS)

## 源码清单

| 端 | 路径 | 说明 |
|----|------|------|
| FPGA | `src/dds/DDS_Module_Dual.v` | 双通道 DDS 核心 |
| FPGA | `src/dds/DDS_Param_Controller.v` | CDC 参数解析 |
| FPGA | `src/dds/arb_wave_ram_simple.v` | 任意波形 RAM |
| FPGA | `src/dds/sin_rom_a8d8.v` 等 | 5 个波形 ROM (256×8) |
| 上位机 | `src/APP/dds/dds_gui_dual.py` | DDS 双通道控制界面 |
| 上位机 | `src/APP/dds/awg_editor.py` | 任意波形编辑器 |

## FPGA 核心算法

```
32位相位累加器:
  freq_word = (freq_hz << 32) / 50_000_000
  phase_acc += freq_word    (每个时钟)
  rom_addr = phase_acc[31:24]  → ROM 查找 → DAC 输出

DAC 映射: DAC 0→+4.4V, DAC 128→0V, DAC 255→-4.4V
精度: ±0.03 Hz (32位累加器), 8位 DAC
频率范围: 1Hz - 50MHz
```

## 上位机

- 频率字由上位机精确计算 (大端4字节)，避免 FPGA 近似计算
- 单参数发送策略: 只发变化参数，防相位跳变
- AWG 编辑器: 手绘(三次样条)+数学表达式+CSV，scipy 惰性导入

## CDC 命令 (0x10-0x1F)

| 命令 | 功能 | Payload |
|------|------|---------|
| 0x10/0x11 | 波形类型 | [type] 0=正弦,1=方波,2=三角,3=锯齿,4=反锯,5=脉冲,10=任意 |
| 0x12/0x13 | 频率 | [freq_word 4B BE] |
| 0x14/0x15 | 相位 | [phase] 0-255→0-360° |
| 0x16/0x17 | 幅度 | [amp] 0-255 |
| 0x18 | 使能 | [mask] bit0=A,bit1=B |
| 0x1C/0x1D | 占空比 | [duty 2B BE] |
| 0x1E/0x1F | 任意波形 | [256B data] |
