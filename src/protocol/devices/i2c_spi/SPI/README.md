# SPI模块说明文档

## 文件结构

```
src/protocol_converter/SPI/
├── spi_master_core.v      # SPI主机核心（底层驱动）
└── spi_controller.v       # SPI控制器（命令处理）
```

## 引脚定义

| 信号       | 引脚 | 说明           |
| ---------- | ---- | -------------- |
| `spi_sclk` | B1   | SPI时钟        |
| `spi_cs`   | B2   | 片选（低有效） |
| `spi_mosi` | M17  | 主出从入       |
| `spi_miso` | A1   | 主入从出       |

## 功能说明

### 1. SPI配置 (0x80)
配置SPI通信参数

**Payload格式：**
```
[freq_khz_L][freq_khz_H][cpol][cpha][msb_first]
```

**参数说明：**
- `freq_khz`: SPI频率（KHz），16位小端序
- `cpol`: 时钟极性（0=空闲低，1=空闲高）
- `cpha`: 时钟相位（0=第一边沿采样，1=第二边沿采样）
- `msb_first`: 位序（1=MSB先发，0=LSB先发）

**示例（1MHz, Mode2）：**
```
55 AA 80 05 00 E8 03 01 00 01 [CS]
         ↑  ↑  ↑     ↑  ↑  ↑
         |  |  |     |  |  └─ msb_first=1
         |  |  |     |  └──── cpha=0
         |  |  |     └─────── cpol=1
         |  |  └───────────── freq=1000KHz (0x03E8)
         |  └──────────────── payload长度=5
         └─────────────────── 命令码
```

### 2. SPI通用传输 (0x81)
发送N字节，同时接收N字节

**Payload格式：**
```
[byte_count][tx_data0][tx_data1]...[tx_dataN]
```

**示例（发送 9F 03 00 00 00）：**
```
55 AA 81 05 00 05 9F 03 00 00 00 [CS]
         ↑  ↑  ↑  └──────────────── 数据
         |  |  └──────────────────── byte_count=5
         |  └─────────────────────── payload长度
         └────────────────────────── 命令码
```

**应答（接收数据）：**
```
AA 55 [LEN_L][LEN_H] 81 00 [rx_data0][rx_data1]...[rx_dataN] [CS]
```

### 3. Flash读ID (0x82)
快捷命令：读取Flash JEDEC ID

**Payload：** 无

**示例：**
```
55 AA 82 00 00 [CS]
```

**应答（3字节ID）：**
```
AA 55 03 00 82 00 [厂商ID][设备ID_H][设备ID_L] [CS]

例如：EF 40 18 = Winbond W25Q128
```

### 4. Flash读取 (0x83)
从指定地址读取数据

**Payload格式：**
```
[addr_H][addr_M][addr_L][byte_count]
```

**示例（地址0x000000，读16字节）：**
```
55 AA 83 04 00 00 00 00 10 [CS]
         ↑  ↑  └────────── 地址+字节数
         |  └───────────── payload长度=4
         └──────────────── 命令码
```

**应答（返回数据）：**
```
AA 55 [LEN_L][LEN_H] 83 00 [data0][data1]...[dataN] [CS]
```

### 5. Flash写入 (0x84)
向指定地址写入数据

**Payload格式：**
```
[addr_H][addr_M][addr_L][data0][data1]...[dataN]
```

**示例（地址0x000000，写入 AA 55）：**
```
55 AA 84 05 00 00 00 00 AA 55 [CS]
         ↑  ↑  └──────────── 地址+数据
         |  └───────────────── payload长度=5
         └──────────────────── 命令码
```

## SPI模式说明

| Mode | CPOL | CPHA | 说明                       |
| ---- | ---- | ---- | -------------------------- |
| 0    | 0    | 0    | 空闲低，第一边沿采样       |
| 1    | 0    | 1    | 空闲低，第二边沿采样       |
| 2    | 1    | 0    | 空闲高，第一边沿采样 ⭐默认 |
| 3    | 1    | 1    | 空闲高，第二边沿采样       |

**W25Q128 Flash支持：** Mode 0 和 Mode 3

## W25Q128 Flash命令

| 命令   | 代码 | 说明                       |
| ------ | ---- | -------------------------- |
| 读ID   | 0x9F | 返回厂商ID+设备ID（3字节） |
| 读数据 | 0x03 | 后跟24位地址               |
| 写使能 | 0x06 | 写入前必须先使能           |
| 页编程 | 0x02 | 后跟24位地址+数据          |

## 典型使用流程

### 上位机示例（Python）

```python
# 1. 配置SPI（1MHz, Mode2）
payload = struct.pack("<HBBB", 1000, 1, 0, 1)
serial_manager.send_command(0x80, payload)

# 2. 读取Flash ID
serial_manager.send_command(0x82, b"")
# 应答：EF 40 18 (Winbond W25Q128)

# 3. 读取地址0x000000的16字节
payload = struct.pack(">I", 0x000000)[1:] + struct.pack("B", 16)
serial_manager.send_command(0x83, payload)

# 4. 写入数据到地址0x000000
payload = struct.pack(">I", 0x000000)[1:] + bytes([0xAA, 0x55])
serial_manager.send_command(0x84, payload)

# 5. 通用SPI传输（发送9F，接收3字节）
payload = struct.pack("B", 1) + bytes([0x9F])
serial_manager.send_command(0x81, payload)
```

## 集成到顶层模块

在 `debugger_top.v` 中添加：

```verilog
// SPI接口
output wire spi_cs,
output wire spi_sclk,
output wire spi_mosi,
input wire spi_miso,

// 实例化SPI控制器
spi_controller u_spi_controller (
    .clk               (clk),
    .rst               (~sys_rst),
    .cmd_valid         (spi_cmd_req),
    .cmd_code          (spi_cmd_code_reg),
    .cmd_payload       (cmd_payload),
    .cmd_payload_valid (cmd_payload_valid),
    .payload_counter   (payload_counter),
    .cmd_done          (spi_cmd_done),
    .response_data     (spi_response_data),
    .response_len      (spi_response_len),
    .spi_cs            (spi_cs),
    .spi_sclk          (spi_sclk),
    .spi_mosi          (spi_mosi),
    .spi_miso          (spi_miso)
);
```

## 测试建议

1. **基本配置测试**：
   - 配置1MHz, Mode2
   - 检查应答成功

2. **Flash ID测试**：
   - 读取Flash ID
   - 确认返回 EF 40 18

3. **Flash读取测试**：
   - 读取地址0x000000
   - 检查数据正确性

4. **Flash写入测试**：
   - 写入测试数据
   - 读回验证

5. **通用SPI测试**：
   - 发送自定义命令
   - 检查收发数据

## 注意事项

1. **Flash写入限制**：
   - 每次最多256字节（页大小）
   - 写入前需要擦除（本模块未实现擦除）
   - 写入地址必须页对齐

2. **CS控制**：
   - 自动超时拉高（48个时钟周期）
   - 连续传输时CS保持低电平

3. **频率限制**：
   - 最小分频系数：1（25MHz）
   - 推荐频率：1-8MHz

---

**作者**: AI Assistant  
**日期**: 2025-11-01  
**版本**: V1.0
