#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CDC串口协议层 - 公共模块
提供统一的协议帧生成、解析、命令定义
"""

import struct

# ============================================================================
# 命令定义
# ============================================================================

# DDS 函数发生器命令 (0x10 - 0x1F)
CMD_DDS_WAVE_A = 0x10  # 设置通道A波形
CMD_DDS_WAVE_B = 0x11  # 设置通道B波形
CMD_DDS_FREQ_A = 0x12  # 设置通道A频率
CMD_DDS_FREQ_B = 0x13  # 设置通道B频率
CMD_DDS_PHASE_A = 0x14  # 设置通道A相位
CMD_DDS_PHASE_B = 0x15  # 设置通道B相位
CMD_DDS_AMP_A = 0x16  # 设置通道A幅度
CMD_DDS_AMP_B = 0x17  # 设置通道B幅度
CMD_DDS_ENABLE = 0x18  # 设置通道使能
CMD_DDS_ALL_A = 0x19  # 设置通道A全部参数
CMD_DDS_ALL_B = 0x1A  # 设置通道B全部参数
CMD_DDS_DUTY_A = 0x1C  # 设置通道A占空比
CMD_DDS_DUTY_B = 0x1D  # 设置通道B占空比
CMD_DDS_AWG_A = 0x1E  # 写入通道A任意波形
CMD_DDS_AWG_B = 0x1F  # 写入通道B任意波形

# 序列发生器命令 (0x30 - 0x3F) - 旧协议（并行模式 + 串行共享频率）
CMD_SEQ_PARALLEL_MODE = 0x30  # 并行模式配置 [序列长度][序列数据...]
CMD_SEQ_SERIAL_MODE = 0x31  # 串行模式配置 [通道ID][序列长度][比特序列...]
CMD_SEQ_FREQ_CONTROL = 0x32  # 频率控制（全局） [频率字(4B小端序)]
CMD_SEQ_START = 0x33  # 启动输出
CMD_SEQ_STOP = 0x34  # 停止输出

# 序列发生器命令 (0x40 - 0x43) - 新协议（串行独立频率，32位DDS）
CMD_SEQ_CONFIG_CHANNEL = (
    0x40  # 配置通道参数 [通道ID][频率字31:24][23:16][15:8][7:0][长度]
)
CMD_SEQ_WRITE_DATA = 0x41  # 写入序列数据 [通道ID][地址][数据]
CMD_SEQ_ENABLE_CONTROL = 0x42  # 使能控制 [使能掩码]
CMD_SEQ_RESET_ALL = 0x43  # 全局复位

# PWM控制器命令 (0x50 - 0x5F)
CMD_PWM_CONFIG = 0x50  # PWM配置
CMD_PWM_ENABLE = 0x51  # PWM使能
CMD_PWM_STOP = 0x52  # PWM停止

# 逻辑分析仪命令 (0x60 - 0x68)
CMD_LA_SET_SAMPLE_RATE = 0x60  # 设置采样率（分频系数）
CMD_LA_SET_BUFFER_SIZE = 0x61  # 设置缓冲区大小
CMD_LA_SET_TRIGGER = 0x62  # 设置触发参数
CMD_LA_START = 0x63  # 开始采集
CMD_LA_STOP = 0x64  # 停止采集
CMD_LA_READ_STATUS = 0x65  # 读取状态
CMD_DSA_START = 0x66  # 开始8路数字信号测量
CMD_DSA_STOP = 0x67  # 停止数字信号测量
CMD_DSA_READ = 0x68  # 读取指定通道测量结果

# 电机控制命令 (0x60 - 0x6F) - 已移除，与逻辑分析仪冲突
# 注意：电机控制功能已集成到PWM控制器中，不再使用独立命令码
# CMD_MOTOR_CONFIG = 0x60
# CMD_MOTOR_START = 0x61
# CMD_MOTOR_STOP = 0x62
# CMD_MOTOR_EMERGENCY = 0x63
# CMD_MOTOR_DIRECTION = 0x64

# I2C设备命令 (0x70 - 0x7F)
CMD_I2C_WRITE = 0x70  # 通用I2C写入
# 0x71, 0x72 已废弃（读取/扫描功能移除）
CMD_OLED_INIT = 0x73  # OLED初始化
CMD_OLED_CLEAR = 0x74  # OLED清屏
CMD_OLED_FULL = 0x75  # OLED全亮显示
CMD_OLED_TEXT = 0x76  # OLED显示文本

# SPI设备命令 (0x80 - 0x8F)
CMD_SPI_CONFIG = 0x80  # SPI配置
CMD_SPI_TRANSFER = 0x81  # SPI传输
CMD_SPI_FLASH_ID = 0x82  # Flash读ID
CMD_SPI_FLASH_READ = 0x83  # Flash读取
CMD_SPI_FLASH_WRITE = 0x84  # Flash写入
CMD_SPI_FLASH_ERASE_SECTOR = 0x85  # Flash扇区擦除
CMD_SPI_FLASH_ERASE_CHIP = 0x86  # Flash全片擦除
CMD_SPI_FLASH_STATUS = 0x87  # Flash读状态

# UART设备命令 (0x90 - 0x9F)
CMD_UART_BAUD = 0x90  # 蓝牙波特率设置
CMD_UART_SEND = 0x91  # 发送数据到蓝牙

# DS18B20温度传感器命令 (0xA0 - 0xAF)
CMD_DS18B20_READ = 0xA0  # 单次读取温度
CMD_DS18B20_START_MONITOR = 0xA1  # 开始连续监控
CMD_DS18B20_STOP_MONITOR = 0xA2  # 停止连续监控

# Bode分析仪命令 (0xB0 - 0xBF) - 🔥 V2.1新增：频率响应分析
CMD_BODE_CONFIG = 0xB0  # 配置参数 [start_freq(4B)][end_freq(4B)][points(2B)][settle_time(2B)][sample_time(2B)]
CMD_BODE_START = 0xB1  # 开始扫频测量
CMD_BODE_STOP = 0xB2  # 停止扫频
CMD_BODE_QUERY = 0xB3  # 查询当前状态
CMD_BODE_DATA = 0xB4  # 数据上报（FPGA主动发送，21字节）

# 示波器/ADC命令 (0x20-0x28) ⭐ V5.0扩展硬件级通道控制
CMD_ADC_MODE = 0x20  # 设置ADC模式（buffer/stream）
CMD_ADC_BUFFER_SIZE = 0x21  # 设置Buffer大小
CMD_ADC_TRIGGER = 0x22  # 设置触发参数
CMD_ADC_START = 0x23  # 启动采集
CMD_ADC_STOP = 0x24  # 停止采集
CMD_ADC_STATUS = 0x25  # 读取ADC状态（预留）
CMD_ADC_SAMPLE_RATE = 0x26  # 设置采样率分频系数
CMD_ADC_FREQ_MEASURE = 0x27  # ADC信号频率测量（独立功能，不启动采集）
CMD_ADC_CHANNEL_ENABLE = 0x28  # 设置通道使能（🔥 V5.0新增：硬件级控制）
CMD_ADC_BUFFER_STATUS = 0x2A  # 读取Buffer模式状态（🔥 V8.7新增：状态查询）
# ❌ 注意：0x29, 0x2B-0x2F 未定义，不存在！

# CAN总线命令 (0xC0 - 0xCF) - 🔥 V2.0新增：SIT1042兼容SJA1000协议
CMD_CAN_CONFIG = 0xC0  # CAN配置（波特率）[波特率索引]
CMD_CAN_SEND = 0xC1  # 发送CAN帧 [frame_type][id_bytes][dlc][data...]
CMD_CAN_FILTER = 0xC2  # 设置过滤器 [filter_id(32bit)]
CMD_CAN_STATUS = 0xC3  # 读取状态
CMD_CAN_RX_DATA = 0xC4  # CAN接收数据上报（FPGA主动发送）

# 命令名称映射（用于日志）
CMD_NAMES = {
    # 系统命令 (0x00-0x0F)
    0x00: "系统复位",
    0x01: "查询状态",
    0x02: "设置模式",
    # DDS命令 (0x10-0x1F)
    0x10: "设置通道A波形",
    0x11: "设置通道B波形",
    0x12: "设置通道A频率",
    0x13: "设置通道B频率",
    0x14: "设置通道A相位",
    0x15: "设置通道B相位",
    0x16: "设置通道A幅度",
    0x17: "设置通道B幅度",
    0x18: "设置通道使能",
    0x19: "设置通道A全部",
    0x1A: "设置通道B全部",
    0x1C: "设置通道A占空比",
    0x1D: "设置通道B占空比",
    0x1E: "写入通道A任意波形",
    0x1F: "写入通道B任意波形",
    # 示波器/ADC (0x20-0x27) ⭐
    0x20: "ADC设置模式",
    0x21: "ADC设置Buffer大小",
    0x22: "ADC设置触发参数",
    0x23: "ADC启动采集",
    0x24: "ADC停止采集",
    0x25: "ADC读取状态",
    0x26: "ADC设置采样率",
    0x27: "ADC频率测量",
    0x28: "ADC设置通道使能",  # 🔥 V5.0新增
    0x2A: "ADC读取Buffer状态",  # 🔥 V8.7新增
    # 序列生成器 (0x30-0x34) - 旧协议（并行 + 串行共享频率）
    0x30: "序列并行模式配置",  # 并行模式
    0x31: "序列串行模式配置",  # 串行共享频率
    0x32: "序列频率控制",  # 全局频率
    0x33: "序列启动输出",  # 启动
    0x34: "序列停止输出",  # 停止
    # 序列生成器 (0x40-0x43) - 新协议（串行独立频率32位DDS）
    0x40: "序列配置通道参数",  # 配置通道+独立频率
    0x41: "序列写入数据",  # 写入序列数据
    0x42: "序列使能控制",  # 使能掩码
    0x43: "序列全局复位",  # 全局复位
    # PWM (0x50-0x5F)
    0x50: "PWM配置",
    0x51: "PWM使能",
    0x52: "PWM停止",
    # 逻辑分析仪 (0x60-0x68)
    0x60: "逻辑分析仪设置采样率",
    0x61: "逻辑分析仪设置缓冲区",
    0x62: "逻辑分析仪设置触发",
    0x63: "逻辑分析仪开始采集",
    0x64: "逻辑分析仪停止采集",
    0x65: "逻辑分析仪读取状态",
    0x66: "数字信号测量开始",
    0x67: "数字信号测量停止",
    0x68: "数字信号测量读取结果",
    # 电机控制 (0x60-0x6F) - 已废弃，与逻辑分析仪冲突
    # 0x60: "电机配置",
    # 0x61: "启动电机",
    # 0x62: "停止电机",
    # 0x63: "电机急停",
    # 0x64: "电机方向控制",
    # I2C (0x70-0x7F)
    0x70: "I2C写入",  # 通用I2C写入
    0x73: "OLED初始化",
    0x74: "OLED清屏",
    0x75: "OLED显示文本",
    0x76: "OLED显示图像",
    # SPI (0x80-0x8F)
    0x80: "SPI配置",
    0x81: "SPI传输",
    0x82: "SPI读取Flash ID",
    0x83: "SPI读取Flash",
    0x84: "SPI写入Flash",
    0x85: "SPI擦除扇区",
    0x86: "SPI擦除芯片",
    0x87: "SPI读取状态",
    # UART/蓝牙 (0x90-0x9F)
    0x90: "UART波特率配置",
    0x91: "UART发送数据",
    0x92: "蓝牙AT命令",
    # DS18B20 (0xA0-0xAF)
    0xA0: "DS18B20单次读取",
    0xA1: "DS18B20开始监控",
    0xA2: "DS18B20停止监控",
    # Bode分析仪 (0xB0-0xBF) - 🔥 V2.1新增
    0xB0: "Bode配置参数",
    0xB1: "Bode开始扫频",
    0xB2: "Bode停止扫频",
    0xB3: "Bode查询状态",
    0xB4: "Bode数据上报",
    # CAN总线 (0xC0-0xCF) - 🔥 V2.0新增
    0xC0: "CAN配置波特率",
    0xC1: "CAN发送帧",
    0xC2: "CAN设置过滤器",
    0xC3: "CAN读取状态",
    0xC4: "CAN接收数据上报",
}


# ============================================================================
# 协议帧处理函数
# ============================================================================


def calc_checksum(data):
    """
    计算校验和（简单累加和取低8位）

    Args:
        data: 字节序列

    Returns:
        校验和 (0-255)
    """
    return sum(data) & 0xFF


def generate_command(cmd, payload=b""):
    """
    生成CDC命令帧

    帧格式: 55 AA [CMD] [LEN_L] [LEN_H] [PAYLOAD] [CS]

    Args:
        cmd: 命令字节 (0x00-0xFF)
        payload: 有效载荷数据 (bytes)

    Returns:
        完整的命令帧 (bytearray)
    """
    frame = bytearray([0x55, 0xAA, cmd])
    frame.extend(struct.pack("<H", len(payload)))  # 小端序长度
    frame.extend(payload)
    cs = calc_checksum(frame[2:])  # 从CMD开始计算校验和
    frame.append(cs)
    return frame


def parse_response(data):
    """
    解析应答帧

    应答格式: AA 55 [MOD_ID] [FUNC_ID] [STATUS] [RSVD] [CS]

    Args:
        data: 接收到的字节序列

    Returns:
        dict 或 None: 解析结果
            {
                'mod_id': 模块ID,
                'func_id': 功能ID,
                'status': 状态码 (0=成功, 1=校验错误, 2=无效命令, 3=参数错误),
                'checksum_ok': 校验是否正确
            }
    """
    if len(data) < 7:
        return None

    if data[0] != 0xAA or data[1] != 0x55:
        return None

    return {
        "mod_id": data[2],
        "func_id": data[3],
        "status": data[4],
        "checksum_ok": (data[6] == calc_checksum(data[2:6])),
    }


def get_status_string(status_code):
    """
    获取状态码描述

    Args:
        status_code: 状态码 (0-255)

    Returns:
        状态描述字符串
    """
    status_map = {
        0x00: "成功",
        0x01: "校验错误",
        0x02: "无效命令",
        0x03: "参数错误",
    }
    return status_map.get(status_code, f"未知(0x{status_code:02X})")


def get_command_name(cmd):
    """
    获取命令名称

    Args:
        cmd: 命令字节

    Returns:
        命令名称字符串
    """
    return CMD_NAMES.get(cmd, f"命令0x{cmd:02X}")


# ============================================================================
# DDS专用函数
# ============================================================================


def calc_freq_word(freq_hz, fclk=125000000):
    """
    计算DDS频率控制字

    公式: freq_word = (目标频率 / 系统时钟) * 2^32

    Args:
        freq_hz: 目标频率 (Hz)
        fclk: 系统时钟频率 (Hz, 默认125MHz)

    Returns:
        32位频率控制字 (0 - 4294967295)
    """
    freq_word = int((freq_hz * 4294967296) / fclk)
    return freq_word


def build_dds_all_params_payload(params):
    """
    构建DDS全参数设置的payload（16位占空比升级版）

    Args:
        params: 参数字典
            {
                'wave_type': 波形类型 (0-6),
                'freq_hz': 频率 (Hz),
                'phase_deg': 相位 (0-359度),
                'amplitude': 幅度 (0-255),
                'duty_cycle': 占空比 (0-100%，支持3位小数精度)
            }

    Returns:
        payload字节序列 (bytes)
    """
    freq_word = calc_freq_word(params["freq_hz"])

    # 16位占空比转换：0-100% 映射到 0-65535
    # 参考PWM模块实现：duty_word = duty_pct * 655.35
    duty_pct = params.get("duty_cycle", 50.0)
    duty_word = int(duty_pct * 655.35)  # 0-100% -> 0-65535
    duty_word = max(0, min(65535, duty_word))  # 限制范围

    payload = struct.pack(
        ">BIHBBH",  # 16位占空比格式：增加H
        params["wave_type"],
        freq_word,
        params["phase_deg"],
        params["amplitude"],
        0,  # 偏置固定为0（已废弃）
        duty_word,  # 16位占空比 (0-65535)
    )

    return payload


def build_dds_duty_payload(duty_pct):
    """
    构建DDS占空比设置的payload（16位精度）

    Args:
        duty_pct: 占空比百分比 (0-100%，支持3位小数)

    Returns:
        payload字节序列 (bytes)
    """
    # 16位占空比转换：0-100% 映射到 0-65535
    # 🔧 修复占空比反向问题：硬件逻辑与预期相反，需要取补
    duty_word = int((100.0 - duty_pct) * 655.35)  # 反向映射：100% - duty_pct
    duty_word = max(0, min(65535, duty_word))  # 限制范围

    payload = struct.pack(">H", duty_word)  # 大端序16位
    return payload


# ============================================================================
# 序列发生器专用函数
# ============================================================================


def build_seq_parallel_payload(sequence_bytes):
    """
    构建并行模式序列payload

    Args:
        sequence_bytes: 字节序列列表 [0x00, 0xFF, 0xAA, ...]

    Returns:
        payload字节序列 (bytes)
    """
    if not sequence_bytes or len(sequence_bytes) > 255:
        raise ValueError("序列长度必须在1-255之间")

    payload = bytes([len(sequence_bytes)] + list(sequence_bytes))
    return payload


def build_seq_serial_payload(channel_id, bit_sequence):
    """
    构建串行模式单通道序列payload

    Args:
        channel_id: 通道编号 (0-7)
        bit_sequence: 比特序列列表 [0, 1, 0, 1, ...]

    Returns:
        payload字节序列 (bytes)
    """
    if channel_id < 0 or channel_id > 7:
        raise ValueError("通道编号必须在0-7之间")

    if not bit_sequence or len(bit_sequence) > 255:
        raise ValueError("序列长度必须在1-255之间")

    channel_mask = 1 << channel_id
    payload = bytes([channel_mask, len(bit_sequence)] + list(bit_sequence))
    return payload


def build_seq_freq_payload(freq_hz, fclk=125000000):
    """
    构建序列频率控制payload

    Args:
        freq_hz: 目标频率 (Hz)
        fclk: 系统时钟频率 (Hz, 默认125MHz)

    Returns:
        payload字节序列 (bytes)
    """
    freq_word = int((freq_hz / fclk) * (2**32))
    payload = struct.pack("<I", freq_word)  # 小端序32位整数
    return payload


# ============================================================================
# 序列发生器新协议函数 (0x40-0x43) - 串行独立频率
# ============================================================================


def calc_seq_freq_word(target_freq_hz, base_freq=50000000):
    """
    计算序列发生器的32位DDS频率字

    公式: freq_word = (target_freq * 2^32) / 50MHz

    Args:
        target_freq_hz: 目标输出频率 (Hz)
        base_freq: 基准时钟频率 (默认50MHz)

    Returns:
        int: 32位频率字 (0-4294967295)
    """
    if target_freq_hz <= 0:
        raise ValueError("目标频率必须大于0")

    # DDS频率字计算：freq_word = (freq_hz * 2^32) / 50MHz
    freq_word = int((target_freq_hz * 4294967296) / base_freq)

    # 限制范围
    if freq_word < 0:
        freq_word = 0
    elif freq_word > 0xFFFFFFFF:
        freq_word = 0xFFFFFFFF

    return freq_word


def cmd_seq_config_channel(channel, freq_hz, length):
    """
    生成配置通道参数命令（新协议0x40，32位DDS频率字）

    命令: 0x40 [通道ID] [频率字31:24][23:16][15:8][7:0] [长度]

    Args:
        channel: 通道ID (0-7)
        freq_hz: 输出频率 (Hz) - 范围0.01Hz-25MHz
        length: 序列长度 (1-255)

    Returns:
        bytes: 命令帧
    """
    if channel < 0 or channel > 7:
        raise ValueError("通道ID必须在0-7之间")

    if length < 1 or length > 255:
        raise ValueError("序列长度必须在1-255之间")

    freq_word = calc_seq_freq_word(freq_hz)

    # 32位频率字，大端序（高字节在前）
    freq_byte3 = (freq_word >> 24) & 0xFF  # 最高字节
    freq_byte2 = (freq_word >> 16) & 0xFF
    freq_byte1 = (freq_word >> 8) & 0xFF
    freq_byte0 = freq_word & 0xFF  # 最低字节

    payload = struct.pack(
        "BBBBBB",
        channel,
        freq_byte3,
        freq_byte2,
        freq_byte1,
        freq_byte0,
        length,
    )
    return generate_command(CMD_SEQ_CONFIG_CHANNEL, payload)


def cmd_seq_write_data(channel, address, data):
    """
    生成写入序列数据命令（新协议0x41）

    命令: 0x41 [通道ID] [地址] [数据]

    Args:
        channel: 通道ID (0-7)
        address: RAM地址 (0-255)
        data: 数据字节 (0-255)

    Returns:
        bytes: 命令帧
    """
    if channel < 0 or channel > 7:
        raise ValueError("通道ID必须在0-7之间")

    if address < 0 or address > 255:
        raise ValueError("地址必须在0-255之间")

    payload = struct.pack("BBB", channel, address, data)
    return generate_command(CMD_SEQ_WRITE_DATA, payload)


def cmd_seq_write_sequence_bulk(channel, data_list):
    """
    批量写入序列数据（新协议）

    Args:
        channel: 通道ID (0-7)
        data_list: 数据列表 [byte0, byte1, ...]

    Returns:
        list: 命令帧列表
    """
    commands = []
    for addr, data in enumerate(data_list):
        cmd = cmd_seq_write_data(channel, addr, data)
        commands.append(cmd)
    return commands


def cmd_seq_enable_channels(channel_mask):
    """
    生成使能控制命令（新协议0x42）

    命令: 0x42 [使能掩码]

    Args:
        channel_mask: 8位掩码
            bit[0] = 通道0使能
            bit[1] = 通道1使能
            ...
            bit[7] = 通道7使能

    Returns:
        bytes: 命令帧
    """
    payload = struct.pack("B", channel_mask & 0xFF)
    return generate_command(CMD_SEQ_ENABLE_CONTROL, payload)


def cmd_seq_reset_all():
    """
    生成全局复位命令（新协议0x43）

    命令: 0x43

    Returns:
        bytes: 命令帧
    """
    return generate_command(CMD_SEQ_RESET_ALL, b"")


def seq_parallel_mode_to_commands(byte_sequence, freq_hz):
    """
    并行模式: 将字节序列转换为命令列表（新协议）

    在并行模式下,8个通道组成一个字节,每个字节代表一个时间步

    Args:
        byte_sequence: 字节序列列表 [0xAB, 0xCD, ...]
        freq_hz: 输出频率 (Hz)

    Returns:
        list: 命令帧列表
    """
    commands = []

    # 序列长度
    length = len(byte_sequence)
    if length == 0:
        return commands

    # 为每个通道配置相同的参数
    for ch in range(8):
        cmd = cmd_seq_config_channel(ch, freq_hz, length)
        commands.append(cmd)

    # 为每个通道写入对应的比特数据
    for addr, byte_val in enumerate(byte_sequence):
        for ch in range(8):
            # 提取对应通道的比特 (bit[0]对应通道0)
            bit = (byte_val >> ch) & 0x01
            cmd = cmd_seq_write_data(ch, addr, bit)
            commands.append(cmd)

    # 使能所有通道
    cmd = cmd_seq_enable_channels(0xFF)
    commands.append(cmd)

    return commands


def seq_serial_mode_to_commands(channel_configs):
    """
    串行模式: 将通道配置转换为命令列表（新协议）

    在串行模式下,每个通道独立配置自己的序列

    Args:
        channel_configs: 通道配置列表
            [
                {'channel': 0, 'enabled': True, 'freq_hz': 1000, 'sequence': [0,1,0,1,...]},
                ...
            ]

    Returns:
        list: 命令帧列表
    """
    commands = []
    enable_mask = 0x00

    for config in channel_configs:
        ch = config["channel"]
        enabled = config.get("enabled", False)

        if not enabled:
            continue

        freq_hz = config["freq_hz"]
        sequence = config["sequence"]

        if len(sequence) == 0:
            continue

        # 配置通道
        cmd = cmd_seq_config_channel(ch, freq_hz, len(sequence))
        commands.append(cmd)

        # 写入序列数据
        for addr, bit in enumerate(sequence):
            cmd = cmd_seq_write_data(ch, addr, bit & 0x01)
            commands.append(cmd)

        # 标记使能
        enable_mask |= 1 << ch

    # 使能通道
    if enable_mask != 0:
        cmd = cmd_seq_enable_channels(enable_mask)
        commands.append(cmd)

    return commands


# ============================================================================
# 测试代码
# ============================================================================


if __name__ == "__main__":
    # 测试命令生成
    print("=== 测试命令生成 ===")

    # 1. DDS频率设置
    freq_word = calc_freq_word(1000)  # 1kHz
    payload = struct.pack(">I", freq_word)
    frame = generate_command(CMD_DDS_FREQ_A, payload)
    print(f"设置通道A频率1kHz: {' '.join(f'{b:02X}' for b in frame)}")

    # 2. DDS全参数设置
    params = {
        "wave_type": 0,  # 正弦波
        "freq_hz": 1000,
        "phase_deg": 90,
        "amplitude": 255,
        "duty_cycle": 50,
    }
    payload = build_dds_all_params_payload(params)
    frame = generate_command(CMD_DDS_ALL_A, payload)
    print(f"设置通道A全参数: {' '.join(f'{b:02X}' for b in frame)}")

    # 3. 序列生成器测试（使用新的统一命令码）
    # 注意：实际使用请参考 utils/sequence_protocol.py 中的封装函数
    seq = [0x00, 0xFF, 0xAA, 0x55]
    payload = build_seq_parallel_payload(seq)
    frame = generate_command(CMD_SEQ_WRITE_DATA, payload)  # 使用0x31写入数据
    print(f"序列数据写入: {' '.join(f'{b:02X}' for b in frame)}")

    # 测试应答解析
    print("\n=== 测试应答解析 ===")
    response = bytes([0xAA, 0x55, 0x01, 0x10, 0x00, 0x00, 0x11])
    result = parse_response(response)
    if result:
        print(f"解析结果: {result}")
        print(f"状态: {get_status_string(result['status'])}")

    print("\n协议模块测试完成！")
