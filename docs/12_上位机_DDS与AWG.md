# 上位机 — DDS 函数发生器 & AWG 编辑器

**源文件**:  
- `src/APP/dds/dds_gui_dual.py` — DDS 双通道控制界面  
- `src/APP/dds/awg_editor.py` — 任意波形编辑器

---

## DDSController — 双通道控制

### 参数接口

| 参数 | 通道A 命令 | 通道B 命令 | 范围 |
|------|:--:|:--:|------|
| 波形类型 | 0x10 | 0x11 | 0=正弦,1=方波,2=三角,3=锯齿,4=反锯,5=脉冲,6=任意 |
| 频率 | 0x12 | 0x13 | 1 Hz – 50 MHz |
| 相位 | 0x14 | 0x15 | 0-359° (1 字节编码) |
| 幅度 | 0x16 | 0x17 | 0-255 (DAC 值) |
| 占空比 | 0x1C | 0x1D | 0-65535 (16位) |
| 使能 | 0x18 | (bitmask) | bit0=A, bit1=B |
| 批量设置 | 0x19 | 0x1A | 全部参数 |
| 任意波形 | 0x1E | 0x1F | 256 字节 |

### 频率字计算

上位机完成频率字转换，避免 FPGA 端的除法近似误差:

```python
def _calc_freq_word(freq_hz: float) -> int:
    """32位大端频率字"""
    # freq_word = (f_hz × 2^32) / 125_000_000
    freq_word = int((freq_hz * (1 << 32)) / 125_000_000)
    return freq_word

payload = struct.pack('>I', freq_word)  # 大端 4 字节
```

### 单参数发送策略

```
用户调整频率滑块:
  → 仅发送 0x12/0x13 命令 (4 字节频率字)
  → 不发送批量 0x19/0x1A (12 字节)

好处:
  - 减少 CDC 总线负载
  - 避免未变化参数被 "刷新" 引起的瞬时跳变
  - 更快的响应 (4字节 vs 12字节)
```

> **源码**: `src/APP/dds/dds_gui_dual.py` (DDSController 类)

---

## AWG 编辑器 — 任意波形编辑

**源文件**: `src/APP/dds/awg_editor.py`

### 三种编辑模式

| 模式 | 输入方式 | 核心库 |
|------|---------|--------|
| **手绘模式** | 鼠标拖拽绘制曲线 | Qt QPainter |
| **数学表达式** | `sin(2*pi*x) + 0.5*sin(6*pi*x)` | `eval()` + numpy |
| **CSV 导入** | 256 点 CSV 文件 | numpy |

### 曲线插值

手绘模式使用三次样条插值平滑曲线:

```python
# scipy 惰性导入 (非必需, 降级用线性插值)
try:
    from scipy.interpolate import CubicSpline
    cs = CubicSpline(points_x, points_y)
    waveform = cs(np.linspace(0, 1, 256))
except ImportError:
    # 降级: 线性插值
    waveform = np.interp(np.linspace(0, 1, 256), points_x, points_y)
```

### 发送流程

```python
def upload_arb_wave(channel: str, data: list):
    """上载 256 字节任意波形到 FPGA"""
    cmd = 0x1E if channel == 'A' else 0x1F
    payload = bytes(data[:256])  # 确保 256 字节
    serial_manager.send_command(cmd, payload)
    # 然后发送 0x10/0x11 将波形类型切换为 6 (任意波形)
```

> 256 字节数据实时写入 FPGA 的 `arb_wave_ram_simple` RAM，每收到一个 payload 字节立即写 RAM，不等 `cmd_done`。
