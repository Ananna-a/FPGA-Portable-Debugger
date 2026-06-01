# ADC模块说明

## 当前使用的模块

### 1. adc_capture_stream.v ✅
**功能**: ADC数据流式采集
- 支持Stream模式和Buffer模式
- 边沿触发采集
- 可配置采样深度和分频
- 输出到DDR3或直接输出

**使用位置**: `debugger_top.v` - 双通道ADC采集（CH1/CH2）

### 2. adc_dual_channel_interleaver.v ✅
**功能**: 双通道ADC数据交织
- 将CH1和CH2数据交织成单个16位数据流
- 格式: [CH1_sample0, CH2_sample0, CH1_sample1, CH2_sample1, ...]
- 同步数据有效信号

**使用位置**: `debugger_top.v` - DDR3写入前的数据合并

### 3. frequency_counter.v ✅
**功能**: 硬件频率计
- 基于过零检测的频率测量
- 1秒计数周期
- 精度可达1Hz
- 输出32位频率值

**使用位置**: `debugger_top.v` - FPGA端频率测量

### 4. frequency_tx_controller.v ✅
**功能**: 频率数据通过CDC发送控制器
- 将测得的频率通过CH340发送到PC
- 4字节小端序格式
- 响应上位机0x27命令

**使用位置**: `debugger_top.v` - 频率数据回传

### 5. zero_cross_comparator.v ✅
**功能**: 过零检测比较器
- 检测ADC信号过零点
- 提供边沿信号给频率计
- 可配置阈值

**使用位置**: `debugger_top.v` - 频率测量的前端处理

## 已删除的冗余模块

- ❌ `adc_16bit_to_8bit.v` - 位宽转换（已被 adc_dual_channel_interleaver 替代）
- ❌ `adc_8bit_to_16bit.v` - 位宽转换（未使用）

## 数据流图

```
ADC硬件 (8位 × 2通道)
    ↓
adc_capture_stream × 2 (CH1, CH2)
    ↓
adc_dual_channel_interleaver (16位交织)
    ↓
DDR3存储
    ↓
以太网发送 (经过FIFO到125MHz域)
```

## 频率测量数据流

```
ADC信号
    ↓
zero_cross_comparator (过零检测)
    ↓
frequency_counter (计数1秒)
    ↓
frequency_tx_controller (CDC发送)
    ↓
上位机 (0x27命令响应)
```

## 模块配置

### adc_capture_stream
```verilog
parameter ADC_WIDTH = 8;  // ADC位宽
```

### frequency_counter
```verilog
parameter CLK_FREQ = 50_000_000;  // 50MHz基准时钟
```

## 注意事项

1. **ADC位宽**: 当前支持8位ADC，如需16位需修改参数
2. **触发电平**: zero_cross_comparator的阈值可通过参数配置
3. **采样率**: 由 `adc_div_set` 参数控制，范围1-4294967295
4. **数据格式**: 双通道交织，CH1和CH2交替存储

## 与以太网模块的接口

ADC模块输出的16位交织数据通过以下路径发送：
1. DDR3缓存
2. 异步FIFO（50MHz → 125MHz）
3. adc_eth_tx_controller
4. eth_udp_tx_wrapper
5. 网络发送

详见 `src/ethernet_oscilloscope/adc_eth_tx_controller.v` 和 `src/ethernet_oscilloscope/eth_udp_tx_wrapper.v`
