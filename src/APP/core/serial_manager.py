#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口管理器 - 统一管理CDC和接收串口
提供信号机制供各模块使用
扩展版：支持ADC数据流读取
"""

import serial
import serial.tools.list_ports
import threading
import time
import numpy as np
from PySide6.QtCore import QObject, Signal, QThread
from .serial_protocol import *


class SerialRxThread(QThread):
    """串口接收线程"""

    data_received = Signal(bytes)  # 接收到数据信号

    def __init__(self, serial_port):
        super().__init__()
        self.serial_port = serial_port
        self.running = True

    def run(self):
        """线程运行函数"""
        while self.running:
            if self.serial_port and self.serial_port.is_open:
                try:
                    if self.serial_port.in_waiting > 0:
                        data = self.serial_port.read(self.serial_port.in_waiting)
                        self.data_received.emit(data)
                except Exception as e:
                    print(f"接收线程错误: {e}")
            self.msleep(10)

    def stop(self):
        """停止线程"""
        self.running = False


class SerialManager(QObject):
    """
    串口管理器
    统一管理CDC发送和CH340接收串口
    扩展版：支持ADC高速数据流
    """

    # 信号定义
    connected = Signal(str, str)  # 连接成功信号 (tx_port, rx_port)
    disconnected = Signal()  # 断开连接信号
    data_received = Signal(bytes)  # 接收到数据信号（所有数据）
    command_sent = Signal(int, bytes, str)  # 命令发送信号 (cmd, payload, description)
    log_message = Signal(str)  # 日志信息信号
    adc_data_received = Signal(list)  # ADC数据专用信号（列表形式）
    frequency_data_received = Signal(bytes)  # 频率数据信号（4字节）
    adc_capture_completed = Signal()  # Buffer单次采集完成信号
    dsa_measurement_received = Signal(
        int, dict
    )  # 🔥新增：DSA测量数据专用信号 (channel, data_dict)
    can_data_received = Signal(bytes)  # 🔥V2.5新增：CAN数据专用信号（纯数据流）
    
    # 🔥V2.2: Bode分析仪数据专用信号
    # 参数: (freq_index: int, freq: float, magnitude: float, phase: float)
    # 🔥 V10.0双通道Bode数据信号（8个参数）
    bode_data_received = Signal(int, float, float, float, float, float)

    def __init__(self):
        super().__init__()
        self.serial_tx = None  # CDC发送串口
        self.serial_rx = None  # CH340接收串口
        self.rx_thread = None
        self.adc_thread = None  # ADC数据读取线程
        self.baud_rate = 115200
        self.stop_event = threading.Event()  # 停止标志

        # SPI Flash读取长度追踪（用于UART面板过滤裸数据流）
        self.pending_spi_read_length = 0

        # 频率测量状态追踪（改进为状态机模式）
        self.freq_response_state = "IDLE"  # IDLE, WAIT_RESPONSE, WAIT_DATA
        self.freq_data_buffer = b""  # 频率数据缓冲区

        # 🔥V8.8.1：DSA数据状态机（用于分包接收20字节完整帧）
        self.dsa_response_state = "IDLE"  # IDLE, WAIT_DATA
        self.dsa_data_buffer = b""
        
        # 🔥V9.2.25：Bode数据包接收缓冲区（防止21字节分包）
        self.bode_packet_buffer = b""  # Bode数据包缓冲区

    def get_available_ports(self):
        """
        获取可用串口列表

        Returns:
            list: 串口列表 [(device, description), ...]
        """
        ports = serial.tools.list_ports.comports()
        return [(port.device, port.description) for port in ports]

    def connect(self, tx_port, rx_port, baud_rate=115200):
        """
        连接串口

        Args:
            tx_port: CDC发送串口名称 (如 "COM15")
            rx_port: CH340接收串口名称 (如 "COM24")
            baud_rate: 波特率

        Returns:
            bool: 是否连接成功
        """
        try:
            # 打开发送串口
            self.serial_tx = serial.Serial(tx_port, baud_rate, timeout=1)

            # 打开接收串口
            self.serial_rx = serial.Serial(rx_port, baud_rate, timeout=1)

            # ✅ 清空接收缓存（避免残留数据）
            self.serial_rx.reset_input_buffer()

            # 🔥 V2.8.2: 重置频率测量状态机（避免第一次测频失败）
            self.freq_response_state = "IDLE"
            self.freq_data_buffer = b""

            self.baud_rate = baud_rate
            self.stop_event.clear()  # 清除停止标志

            # 启动接收线程
            self.rx_thread = SerialRxThread(self.serial_rx)
            self.rx_thread.data_received.connect(self._on_data_received)
            self.rx_thread.start()

            # 发送连接成功信号
            self.connected.emit(tx_port, rx_port)
            self.log_message.emit(f"✅ CDC(TX): {tx_port} @ {baud_rate}bps")
            self.log_message.emit(f"✅ CH340(RX): {rx_port} @ {baud_rate}bps")

            return True

        except Exception as e:
            self.log_message.emit(f"❌ 串口连接失败: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        """断开串口连接"""
        # 设置停止标志
        self.stop_event.set()

        # 停止ADC线程
        if self.adc_thread and self.adc_thread.is_alive():
            self.adc_thread.join(timeout=0.5)
            self.adc_thread = None

        # 停止接收线程
        if self.rx_thread:
            self.rx_thread.stop()
            self.rx_thread.wait()
            self.rx_thread = None

        # 关闭串口
        if self.serial_tx and self.serial_tx.is_open:
            self.serial_tx.close()
        if self.serial_rx and self.serial_rx.is_open:
            self.serial_rx.close()

        self.serial_tx = None
        self.serial_rx = None

        # 发送断开信号
        self.disconnected.emit()
        self.log_message.emit("🔌 串口已断开")

    def is_connected(self):
        """
        检查串口是否已连接

        Returns:
            bool: 是否已连接
        """
        return (
            self.serial_tx is not None
            and self.serial_tx.is_open
            and self.serial_rx is not None
            and self.serial_rx.is_open
        )

    def send_command(self, cmd, payload=b""):
        """
        发送命令

        Args:
            cmd: 命令字节
            payload: 有效载荷

        Returns:
            bool: 是否发送成功
        """
        if not self.is_connected():
            self.log_message.emit("❌ 串口未连接，无法发送命令")
            return False

        try:
            # 生成命令帧
            frame = generate_command(cmd, payload)

            # 🔥 V8.7.60修复：flush()可能在设备占用时失败，需要捕获异常
            # 原因：后台adc_thread可能仍在读取serial_tx，导致flush()抛出PermissionError
            try:
                self.serial_tx.flush()  # 尝试清空写缓冲区
            except (PermissionError, OSError) as flush_err:
                # 设备占用时忽略flush错误，直接尝试写入
                # 原因：flush()失败不影响write()操作（写入仍会缓冲）
                pass

            # 发送（带重试机制）
            max_retries = 3
            for retry in range(max_retries):
                try:
                    self.serial_tx.write(frame)
                    break  # 发送成功，退出重试循环
                except (PermissionError, OSError) as write_err:
                    if retry < max_retries - 1:
                        # 还有重试机会，等待50ms让后台线程释放资源
                        import time

                        time.sleep(0.05)
                    else:
                        # 最后一次重试也失败，抛出异常
                        raise write_err
                except Exception as e:
                    # 其他类型异常直接抛出
                    raise e

            # 发送信号
            cmd_name = get_command_name(cmd)
            hex_str = " ".join(f"{b:02X}" for b in frame)

            self.command_sent.emit(cmd, payload, cmd_name)
            self.log_message.emit(f"[TX→CDC] {cmd_name}")
            self.log_message.emit(f"         {hex_str}")

            return True

        except (PermissionError, OSError) as e:
            # 🔥 串口占用错误：提示用户并尝试恢复
            self.log_message.emit(
                f"⚠️ 串口占用错误: {e}\n" f"   提示：请稍后重试，或重新连接串口"
            )
            return False
        except Exception as e:
            self.log_message.emit(f"❌ 发送失败: {e}")
            return False

    def send_raw(self, frame):
        """
        发送原始命令帧（用于自定义协议）

        Args:
            frame: 完整的命令帧（bytes）

        Returns:
            bool: 是否发送成功
        """
        if not self.is_connected():
            return False

        try:
            self.serial_tx.write(frame)
            return True
        except Exception as e:
            self.log_message.emit(f"❌ 发送失败: {e}")
            return False

    def request_frequency_measurement(self):
        """
        发送频率测量请求命令(0x27)到FPGA
        FPGA将通过CH340串口返回4字节频率值

        Returns:
            bool: 是否发送成功
        """
        if not self.is_connected():
            self.log_message.emit("❌ 串口未连接，无法发送频率测量请求")
            return False

        # 初始化频率测量状态机
        self.freq_response_state = "WAIT_RESPONSE"
        self.freq_data_buffer = b""
        result = self.send_command(0x27, b"")  # 命令0x27，payload为空

        return result

    def _on_data_received(self, data):
        """
        处理接收到的数据（内部）
        改进版：使用状态机处理频率数据接收 + 自动识别FPGA后台测频数据
        V9.2.25: 添加Bode数据包缓冲区机制，防止21字节分包

        Args:
            data: 接收到的字节序列
        """
        # 🔥 V9.2.25: Bode数据包缓冲区处理
        # 如果缓冲区中有未完成的数据，先合并
        if self.bode_packet_buffer:
            data = self.bode_packet_buffer + data
            self.bode_packet_buffer = b""
        
        # 🔥 V2.2: 监控所有数据包，特别关注21字节
        if len(data) == 21:
            print(f"[RX] 🔥 收到21字节数据包: {data.hex()}")
            print(f"     前4字节: {data[0]:02X} {data[1]:02X} {data[2]:02X} {data[3]:02X}")
        elif len(data) > 10:
            print(f"[RX] 收到 {len(data)} 字节 (前4字节: {data[:4].hex()})")
        
        # 🔥 显示完整原始数据（用于调试）
        print(f"[CH340原始数据] {len(data)}字节: {data.hex(' ')}")
        
        # 🔥 V8.8.1修复：先检查应答帧，确定是否需要启动DSA数据接收
        # 这样可以避免无关数据被累积到DSA缓冲区
        has_response_frame = False
        is_dsa_response = False

        for i in range(len(data) - 6):
            if data[i] == 0xAA and data[i + 1] == 0x55:
                resp_frame = data[i : i + 7]
                result = parse_response(resp_frame)
                if result and result["func_id"] == 0x68:
                    # 这是0x68命令的应答帧，启动DSA数据接收
                    is_dsa_response = True
                    self.dsa_response_state = "WAIT_DATA"
                    self.dsa_data_buffer = data[i:]  # 从应答帧开始累积
                    break

        # 🔥 DSA数据状态机处理
        if self.dsa_response_state == "WAIT_DATA":
            # 继续累积数据
            if not is_dsa_response:  # 避免重复添加
                self.dsa_data_buffer += data

            # 检查是否收集到完整的20字节
            if len(self.dsa_data_buffer) >= 20:
                # 查找DSA数据帧起始位置（AA 55 01 68）
                for i in range(len(self.dsa_data_buffer) - 19):
                    if (
                        self.dsa_data_buffer[i] == 0xAA
                        and self.dsa_data_buffer[i + 1] == 0x55
                        and self.dsa_data_buffer[i + 3] == 0x68
                    ):
                        dsa_frame = self.dsa_data_buffer[i : i + 20]
                        if self._is_dsa_data_frame(dsa_frame):
                            self._parse_dsa_data(dsa_frame)
                            # 重置状态，等待下一次0x68命令
                            self.dsa_response_state = "IDLE"
                            self.dsa_data_buffer = b""
                            return  # DSA数据不转发到通用处理
                        break

                # 超过50字节还没找到有效帧，重置状态
                if len(self.dsa_data_buffer) > 50:
                    self.dsa_response_state = "IDLE"
                    self.dsa_data_buffer = b""
            return  # WAIT_DATA状态下不处理其他数据

        # 🔥 修复：先检查应答帧，避免4字节应答帧被误判为频率数据
        # 检查是否包含应答帧标志（0xAA 0x55）
        has_response_frame = False
        for i in range(len(data) - 1):
            if data[i] == 0xAA and data[i + 1] == 0x55:
                has_response_frame = True
                break

        # 只有在没有应答帧标志时，才尝试解析为频率数据
        # 🔥 V5.19修复：支持8字节双通道频率数据（CH1+CH2）
        if (
            self.freq_response_state == "IDLE"
            and len(data) == 8  # V2.0: 双通道频率（8字节）
            and not has_response_frame
        ):
            # 识别为FPGA后台自动测频的频率数据
            try:
                import struct

                freq_ch1 = struct.unpack("<I", data[0:4])[0]
                freq_ch2 = struct.unpack("<I", data[4:8])[0]
                # 合理的频率范围：0Hz ~ 100MHz（0Hz表示无信号）
                if 0 <= freq_ch1 <= 100_000_000 and 0 <= freq_ch2 <= 100_000_000:
                    # 这是有效的频率数据，不转发到data_received
                    self._parse_frequency_data(data)
                    return  # 直接返回，不继续处理
            except:
                pass  # 解析失败，按普通数据处理

        # 频率测量状态机处理（0x27命令响应）
        if self.freq_response_state == "WAIT_RESPONSE":
            # 查找0x27应答帧
            for i in range(len(data) - 6):
                if data[i] == 0xAA and data[i + 1] == 0x55:
                    resp_frame = data[i : i + 7]
                    result = parse_response(resp_frame)

                    if result and result["func_id"] == 0x27:
                        # 转发应答帧到调试日志（仅应答帧，不含数据）
                        self.data_received.emit(resp_frame)

                        # 切换到等待频率数据状态
                        self.freq_response_state = "WAIT_DATA"

                        # 检查应答帧后是否有数据
                        remaining_data = data[i + 7 :]
                        if remaining_data:
                            self.freq_data_buffer = remaining_data

                        # 检查是否已收集夙8字节（V2.0：双通道频率）
                        if len(self.freq_data_buffer) >= 8:
                            self._parse_frequency_data(self.freq_data_buffer[:8])
                            self.freq_response_state = "IDLE"
                            self.freq_data_buffer = b""

                        return  # 找到应答帧后退出

        elif self.freq_response_state == "WAIT_DATA":
            # 收集频率数据（不转发到data_received）
            self.freq_data_buffer += data

            # 检查是否已收集夙8字节（V2.0：CH1 4字节 + CH2 4字节）
            if len(self.freq_data_buffer) >= 8:
                self._parse_frequency_data(self.freq_data_buffer[:8])
                self.freq_response_state = "IDLE"
                self.freq_data_buffer = b""
            return

        # 🔥 V2.8: 检查是否为CAN纯数据流（支持扩展帧）
        # CAN数据特征: 第一字节0x00(标准帧)或0x01(扩展帧)，长度与DLC匹配
        is_can_data = False
        if len(data) >= 3 and len(data) <= 14:  # 扩展帧最多14字节(1+4+1+8)
            if data[0] == 0x00:  # 标准帧
                # 标准帧: [0x00][ID_H][ID_L_DLC][data0-7]
                # 排除应答帧(AA 55开头) - 这个判断其实冗余，因为0xAA!=0x00
                if len(data) >= 3:
                    # 验证第三字节(ID_L+DLC)的DLC字段(低4位)在0-8范围内
                    dlc = data[2] & 0x0F
                    if dlc <= 8:
                        expected_len = 3 + dlc
                        # 长度匹配才认为是CAN数据
                        if len(data) == expected_len:
                            is_can_data = True
            elif data[0] == 0x01:  # 🔥 V2.8: 扩展帧
                # 扩展帧格式: [type=0x01][ID3][ID2][ID1][ID0][data0-7]
                # 最小6字节(type+4ID+至少1data), 最多13字节(type+4ID+8data)
                if len(data) >= 6 and len(data) <= 13:
                    # 扩展帧DLC从长度推算
                    dlc = len(data) - 5  # DLC = total - 1(type) - 4(ID)
                    if dlc <= 8:
                        is_can_data = True

        if is_can_data:
            # 🔥 CAN数据专用通道，不转发到data_received
            self.can_data_received.emit(data)
            return  # 不继续处理

        # 🔥 V10.2.1: 检查是否为Bode分析仪数据包（57字节I/Q原始数据格式）
        # 数据格式: [0xAA][0x55][0x0B][0xB1][50字节Payload][校验和1B]
        
        # 🔥 V10.2.3修复：循环拆包处理（49字节包，45位 I/Q 数据）
        bode_packets_found = 0
        offset = 0
        
        while offset < len(data):
            # 查扻Bode包头 (0xB1 = I/Q原始数据模式)
            if offset + 4 <= len(data) and data[offset] == 0xAA and data[offset+1] == 0x55 and data[offset+2] == 0x0B and data[offset+3] == 0xB1:
                # 检查是否有完整的49字节包
                if offset + 49 <= len(data):
                    packet = data[offset:offset+49]
                    
                    # ✅ V10.2.3 校验和：45位 I/Q 数据，仅低4字节参与校验
                    # packet[12:16]=i_ref低4B, packet[18:22]=q_ref低4B, 
                    # packet[24:28]=i_dut低4B, packet[30:34]=q_dut低4B
                    checksum_calc = (packet[2] + packet[3] + packet[4] + packet[5] +   # 协议头
                                    sum(packet[6:12]) +      # freq_index(2B) + freq(4B)
                                    sum(packet[12:16]) +     # i_ref低4B
                                    sum(packet[18:22]) +     # q_ref低4B
                                    sum(packet[24:28]) +     # i_dut低4B
                                    sum(packet[30:34])) & 0xFF  # q_dut低4B
                    
                    if checksum_calc == packet[48]:
                        print(f"[Bode I/Q] ✅ 找到有效包 #{bode_packets_found+1}，偏移={offset}")
                        # 解析并发送
                        parsed = self._parse_bode_data(packet)
                        if parsed:
                            bode_packets_found += 1
                        offset += 49  # 跳到下一个包
                    else:
                        print(f"[Bode I/Q] ❌ 校验失败: 计算={checksum_calc:02X}, 接收={packet[48]:02X}")
                        offset += 1  # 继续查找
                else:
                    # 数据不完整，缓存剩余部分
                    remaining = data[offset:]
                    print(f"[Bode I/Q] ⚠️ 包不完整，缓存 {len(remaining)} 字节等待下次")
                    self.bode_packet_buffer = remaining
                    break
            else:
                offset += 1  # 继续查找
        
        if bode_packets_found > 0:
            print(f"[Bode I/Q] 🎯 本次共解析 {bode_packets_found} 个I/Q数据包")
            return  # 不继续处理

        # 🔥 非CAN/Bode数据，正常转发到通用信号
        self.data_received.emit(data)

        # 非频率测量模式：统一处理其他应答帧（显示在调试日志）
        # 🔥 V8.8.1修复：不再return，允许处理多个应答帧
        # 🔥 V9.2.25修复：跳过Bode数据包头（AA 55 0B B1），只识别应答帧（AA 55 01 XX）
        for i in range(len(data) - 6):
            if data[i] == 0xAA and data[i + 1] == 0x55:
                # 检查第3字节：0x01=应答帧，0x0B=Bode数据包
                if i + 2 < len(data) and data[i + 2] == 0x0B:
                    # 这是Bode数据包头，跳过（避免误识别）
                    continue
                    
                resp_frame = data[i : i + 7]
                result = parse_response(resp_frame)

                if result:
                    # 统一的应答帧日志格式
                    hex_str_frame = " ".join(f"{b:02X}" for b in resp_frame)
                    cmd_name = get_command_name(result["func_id"])
                    status_str = get_status_string(result["status"])

                    # 🔥 0x68的应答帧也要显示（DSA数据会在后续单独处理）
                    self.log_message.emit(
                        f"🔍 [RX←CH340] 应答帧({cmd_name}): {hex_str_frame}"
                    )
                    self.log_message.emit(f"✅            状态: {status_str}")

    def _parse_frequency_data(self, data):
        """解析8字节双通道频率数据（V2.0：静默版本，不输出日志）"""
        if len(data) < 8:
            return

        try:
            # 直接发送信号给上位机，不输出日志
            self.frequency_data_received.emit(data[:8])
        except Exception as e:
            self.log_message.emit(f"⚠️ 频率解析失败: {e}")
            self.freq_response_state = "IDLE"  # 重置状态

    def _is_dsa_data_frame(self, data):
        """
        识别DSA测量数据帧（20字节：7字节应答 + 13字节数据）

        Args:
            data: 接收到的字节序列

        Returns:
            bool: 是否为DSA数据帧
        """
        if len(data) != 20:
            self.log_message.emit(f"🔍 [DSA检查] 长度不符: {len(data)} != 20")
            return False

        # 检查应答帧标记和0x68命令
        if data[0] == 0xAA and data[1] == 0x55:
            resp = parse_response(data[:7])
            self.log_message.emit(f"🔍 [DSA检查] 应答帧解析: {resp}")
            if resp and resp["func_id"] == 0x68 and resp["status"] == 0x00:
                self.log_message.emit(f"✅ [DSA检查] 识别为DSA数据帧")
                return True
        return False

    def _parse_dsa_data(self, data):
        """
        解析DSA测量数据（20字节）
        格式：[7字节应答] [1字节通道] [4字节频率] [4字节高周期] [4字节低周期]

        Args:
            data: 20字节完整帧
        """
        self.log_message.emit(f"🔍 [DSA解析] 开始解析20字节数据")

        if len(data) < 20:
            self.log_message.emit(f"❌ [DSA解析] 数据长度不足: {len(data)}")
            return

        try:
            import struct

            # 跳过应答帧（7字节），解析13字节测量数据
            channel = data[7] & 0x07  # 取低3位作为通道号
            freq = struct.unpack("<I", data[8:12])[0]
            high_cycles = struct.unpack("<I", data[12:16])[0]
            low_cycles = struct.unpack("<I", data[16:20])[0]

            # 组装数据字典
            measurement = {
                "freq": freq,
                "high_cycles": high_cycles,
                "low_cycles": low_cycles,
            }

            # 🔥 V8.8.2：先输出日志
            self.log_message.emit(
                f"📊 [DSA] CH{channel}: {freq}Hz, H={high_cycles}, L={low_cycles}"
            )

            # 发送DSA专用信号
            self.log_message.emit(f"🔍 [DSA解析] 准备发送信号到GUI...")
            self.dsa_measurement_received.emit(channel, measurement)
            self.log_message.emit(f"✅ [DSA解析] 信号已发送")

        except Exception as e:
            self.log_message.emit(f"⚠️ DSA数据解析失败: {e}")

    def _parse_bode_data(self, data):
        """
        解析Bode分析仪I/Q原始数据（V10.2.1：57字节）
        格式：[0xAA][0x55][0x0B][0xB1][LEN 2B]
              [freq_index 2B][freq 4B]
              [i_ref 7B (53-bit)][q_ref 7B]
              [i_dut 7B][q_dut 7B]
              [reserved 16B][校验和1B]

        Args:
            data: 49字节完整帧 (V10.2.3)
        
        Returns:
            bool: 解析是否成功
        """
        if len(data) < 49:
            self.log_message.emit(f"❌ [Bode I/Q解析] 数据长度不足: {len(data)} (期待49字节)")
            return False

        try:
            import struct

            # 解析payload
            # ✅ V10.2.3: 45位I/Q数据，6字节编码
            # [6-7]: freq_index (16-bit LE)
            # [8-11]: freq (32-bit LE, Hz)
            # [12-17]: i_ref (6字节，45-bit有符号)
            # [18-23]: q_ref (6字节，45-bit有符号)
            # [24-29]: i_dut (6字节，45-bit有符号)
            # [30-35]: q_dut (6字节，45-bit有符号)
            freq_index = struct.unpack("<H", data[6:8])[0]
            freq = struct.unpack("<I", data[8:12])[0]
            
            # 解析45位有符号I/Q值（6字节小端序）
            def parse_45bit_signed(bytes_6):
                """解析6字节小端序45位有符号整数"""
                # 读取6字节并组合成整数
                value = 0
                for i in range(6):
                    value |= (bytes_6[i] << (i * 8))
                
                # ✅ 关键修复：屏蔽高3位，只保留低45位
                value &= 0x1FFFFFFFFFFF  # 0x1FFF_FFFF_FFFF = 45个1
                
                # 处理符号位（第45位，即bit[44]）
                if value & (1 << 44):  # 检查第45位（索引44）
                    value -= (1 << 45)  # 转换为负数
                
                return value
            
            i_ref_raw = parse_45bit_signed(data[12:18])
            q_ref_raw = parse_45bit_signed(data[18:24])
            i_dut_raw = parse_45bit_signed(data[24:30])
            q_dut_raw = parse_45bit_signed(data[30:36])
            
            # 归一化：除以CIC增益128^4（V10.2.3：抽取率128）
            CIC_GAIN = 128 ** 4  # 268,435,456
            i_ref = i_ref_raw / CIC_GAIN
            q_ref = q_ref_raw / CIC_GAIN
            i_dut = i_dut_raw / CIC_GAIN
            q_dut = q_dut_raw / CIC_GAIN
            
            print(f"[Bode I/Q解析 V10.2.3] freq_index={freq_index}, freq={freq}Hz")
            print(f"  REF: I={i_ref:+.8f}, Q={q_ref:+.8f}")
            print(f"  DUT: I={i_dut:+.8f}, Q={q_dut:+.8f}")
            
            # 发送I/Q信号（6个参数）
            self.bode_data_received.emit(
                freq_index, freq, 
                i_ref, q_ref,
                i_dut, q_dut
            )
            return True

        except Exception as e:
            self.log_message.emit(f"❌ [Bode I/Q解析] 异常: {e}")
            import traceback
            traceback.print_exc()
            return False
            self.log_message.emit(
                f"📈 [Bode] {freq}Hz: {bode_data['magnitude_db']:.2f}dB, {phase:.1f}°"
            )

        except Exception as e:
            self.log_message.emit(f"⚠️ Bode数据解析失败: {e}")

    def _process_response_frames(self, data):
        """
        处理应答帧数据（已废弃，功能合并到_on_data_received）

        Args:
            data: 字节序列
        """
        pass  # 保留接口兼容性

    def get_tx_port(self):
        """获取CDC发送串口对象"""
        return self.serial_tx

    def get_rx_port(self):
        """获取CH340接收串口对象"""
        return self.serial_rx

    # --- 示波器专用方法 ---
    def start_adc_stream(self, mode, buffer_size):
        """
        启动ADC数据读取线程

        模式说明（V3.1版）：
        - "stream"（连续采集模式）：连续采集，从CDC端口读取数据流
        - "buffer"（单次触发模式）：单次采集固定数据到DDR3后通过CDC一次性发送

        Args:
            mode: "stream" 或 "buffer"
            buffer_size: buffer模式下的预期字节数
        """
        # 🔥 V8.7.60修复：强制停止旧线程，避免资源冲突
        if self.adc_thread and self.adc_thread.is_alive():
            self.log_message.emit("🔄 检测到后台线程仍在运行，正在停止...")
            self.stop_event.set()  # 发送停止信号
            self.adc_thread.join(timeout=1.0)  # 🔥 等待最多1秒
            if self.adc_thread.is_alive():
                self.log_message.emit("⚠️ 后台线程未能及时退出，强制继续启动")
            self.adc_thread = None

        # 清空CDC串口缓冲区，避免读到上一次的残留数据
        if self.serial_tx and self.serial_tx.in_waiting > 0:
            try:
                self.serial_tx.read(self.serial_tx.in_waiting)
            except Exception as e:
                self.log_message.emit(f"⚠️ 清空串口缓冲区失败: {e}")

        self.stop_event.clear()
        self.adc_thread = threading.Thread(
            target=self._read_adc_data_task, args=(mode, buffer_size), daemon=True
        )
        self.adc_thread.start()

    def stop_adc_stream(self):
        """停止ADC数据读取线程"""
        self.stop_event.set()
        if self.adc_thread and self.adc_thread.is_alive():
            # 🔥 V8.7.60修复：主动等待线程退出，确保资源释放
            self.adc_thread.join(timeout=1.0)  # 等待最多1秒
            if self.adc_thread.is_alive():
                self.log_message.emit("⚠️ ADC数据线程未能及时退出")
            self.adc_thread = None

    def _read_adc_data_task(self, mode, buffer_size):
        """
        在后台线程中运行，读取ADC数据

        当前实现：
        - stream模式和buffer模式都从CDC端口(serial_tx)读取
        - stream模式：连续读取1K数据块
        - buffer模式：读取固定大小的完整数据块
        """
        try:
            if mode == "buffer":
                # ========== Buffer模式（单次触发）==========
                # 从CDC端口读取固定大小的数据块
                if not self.serial_tx:
                    self.log_message.emit("❌ [ADC] CDC端口未连接")
                    return

                old_timeout = self.serial_tx.timeout
                self.serial_tx.timeout = 5.0  # 等待数据块的超时可以长一些
                data = self.serial_tx.read(buffer_size)
                self.serial_tx.timeout = old_timeout  # 恢复默认短超时

                if data:
                    self.adc_data_received.emit(list(data))

                # Buffer单次模式采集完成,发送信号通知上位机自动停止
                self.adc_capture_completed.emit()

            else:  # stream mode（连续采集）
                # ========== Stream模式（连续采集）==========
                # 从CDC端口连续读取数据流
                if not self.serial_tx:
                    return

                while not self.stop_event.is_set():
                    # 从CDC端口连续读取
                    data = self.serial_tx.read(1024)  # 每次读取1K

                    if data:
                        self.adc_data_received.emit(list(data))
                    else:
                        # 短暂休眠避免CPU空转
                        time.sleep(0.001)

        except serial.SerialException as e:
            if not self.stop_event.is_set():
                self.log_message.emit(f"❌ ADC数据读取失败: {e}")
        except Exception as e:
            self.log_message.emit(f"❌ ADC错误: {e}")


# ============================================================================
# 测试代码
# ============================================================================


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    import sys

    app = QApplication(sys.argv)

    manager = SerialManager()

    # 连接日志信号
    manager.log_message.connect(lambda msg: print(msg))

    # 获取可用串口
    ports = manager.get_available_ports()
    print("可用串口:")
    for device, desc in ports:
        print(f"  {device} - {desc}")

    # 测试连接（需要根据实际情况修改串口名）
    # manager.connect("COM15", "COM24", 115200)

    # 延迟退出
    QTimer.singleShot(1000, app.quit)

    sys.exit(app.exec())
