# 8路PWM控制器模块说明

## 功能概述

8路独立PWM输出控制器，参考DDS方波生成和序列发生器架构设计。

## 文件列表

### 上位机部分 (src/APP/)
- `pwm_controller_tab.py` - PWM控制器UI界面
  - 简洁风格，参考序列发生器设计
  - 8路独立配置
  - 实时日志输出

### FPGA部分 (src/LogicAnalyzer/)
- `pwm_param_controller.v` - PWM参数控制器
  - CDC命令解析
  - 参数存储和管理
  - 8通道配置
  
- `pwm_generator.v` - PWM波形生成器
  - 相位累加器原理（参考DDS）
  - 频率和占空比可调
  - 单通道独立模块

## 引脚定义

根据图片提供的引脚映射:

```
PWM0 → L6   (PIN: L6)
PWM1 → N15  (PIN: N15)
PWM2 → R17  (PIN: R17)
PWM3 → P16  (PIN: P16)
PWM4 → N14  (PIN: N14)
PWM5 → N13  (PIN: N13)
PWM6 → R16  (PIN: R16)
PWM7 → P15  (PIN: P15)
```

## 技术参数

- **通道数量**: 8路独立PWM
- **频率范围**: 1Hz - 1MHz
- **占空比范围**: 0-100%
- **占空比精度**: 16位 (65536级, 0.0015%步进)
- **系统时钟**: 50MHz

## 协议命令

### 0x50 - PWM配置
```
Payload: [通道ID][频率H3][频率H2][频率H1][频率L][占空比]
- 通道ID: 0-7
- 频率: 32位大端序 (Hz)
- 占空比: 0-255 (对应0-100%)
```

### 0x51 - PWM使能控制
```
Payload: [使能掩码]
- 使能掩码: 8位，每位对应一个通道
```

### 0x52 - PWM停止
```
Payload: 无
停止所有PWM输出
```

## 设计思路

### 1. 参考DDS方波生成
- 使用相位累加器
- 频率字计算: `freq_word = (freq_hz * 2^32) / clk_freq`
- 占空比通过阈值比较实现

### 2. 参考序列发生器架构
- 命令解析状态机
- 参数缓存和配置
- 多通道独立控制

### 3. 输出到8路GPIO
- 每个PWM生成器输出1位
- 组合成8位并行输出
- 直接连接到GPIO引脚

## 集成说明

### 主模块集成
在顶层模块中实例化:

```verilog
pwm_param_controller pwm_ctrl(
    .clk            (clk_50m),
    .rst_n          (rst_n),
    .cmd            (cmd),
    .payload_data   (payload_data),
    .payload_valid  (payload_valid),
    .cmd_done       (cmd_done),
    .pwm_output     (pwm_gpio),
    .pwm_enable     (pwm_en),
    .status         (pwm_status)
);
```

### 约束文件 (CST)
添加引脚约束:

```
// PWM输出引脚
IO_LOC  "pwm_gpio[0]" L6;
IO_PORT "pwm_gpio[0]" PULL_MODE=NONE DRIVE=8;

IO_LOC  "pwm_gpio[1]" N15;
IO_PORT "pwm_gpio[1]" PULL_MODE=NONE DRIVE=8;

IO_LOC  "pwm_gpio[2]" R17;
IO_PORT "pwm_gpio[2]" PULL_MODE=NONE DRIVE=8;

IO_LOC  "pwm_gpio[3]" P16;
IO_PORT "pwm_gpio[3]" PULL_MODE=NONE DRIVE=8;

IO_LOC  "pwm_gpio[4]" N14;
IO_PORT "pwm_gpio[4]" PULL_MODE=NONE DRIVE=8;

IO_LOC  "pwm_gpio[5]" N13;
IO_PORT "pwm_gpio[5]" PULL_MODE=NONE DRIVE=8;

IO_LOC  "pwm_gpio[6]" R16;
IO_PORT "pwm_gpio[6]" PULL_MODE=NONE DRIVE=8;

IO_LOC  "pwm_gpio[7]" P15;
IO_PORT "pwm_gpio[7]" PULL_MODE=NONE DRIVE=8;
```

## 使用示例

### Python上位机

```python
from pwm_controller_tab import PWMControllerTab

# 创建PWM控制器
pwm_tab = PWMControllerTab(serial_manager)

# 或使用协议直接发送
import struct

# 配置PWM0: 1kHz, 50%
channel = 0
freq = 1000
duty = 127  # 50% = 127/255
payload = struct.pack('>BIB', channel, freq, duty)
serial_manager.send_command(0x50, payload)

# 使能PWM0
serial_manager.send_command(0x51, bytes([0x01]))
```

## 应用场景

1. **电机控制**: PWM调速、H桥驱动
2. **LED调光**: 多路LED亮度控制
3. **舵机控制**: 标准舵机角度控制
4. **开关电源**: Buck/Boost变换器控制
5. **音频PWM**: 简单的PWM音频输出

## 注意事项

1. **驱动能力**: GPIO输出电流有限，需要外接驱动电路
2. **EMI干扰**: 高频PWM可能产生电磁干扰
3. **频率精度**: 受系统时钟精度限制
4. **占空比限制**: 极端占空比(0%或100%)可能导致输出异常

## 后续扩展

- [ ] 添加死区时间控制
- [ ] 支持互补PWM输出
- [ ] 相位同步功能
- [ ] PWM捕获/测量功能
- [ ] 在设备中心实现模式切换
