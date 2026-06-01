#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QTextEdit,
    QCheckBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from datetime import datetime

# DS18B20命令定义
CMD_DS18B20_START = 0xA0  # 开始连续监控（FPGA实际使用）
CMD_DS18B20_STOP = 0xA2   # 停止连续监控
# 注：0xA1未被FPGA使用


class DS18B20Panel(QWidget):
    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.show_timestamp = True
        self.reading = False
        self.temp_min = float("inf")
        self.temp_max = float("-inf")
        self.temp_avg = 0.0
        self.sample_count = 0

        # 数据缓冲区和状态标志（参考SPI实现）
        self.data_buffer = bytearray()
        self.waiting_temp_data = False  # 是否正在等待温度裸数据

        self.init_ui()
        if self.serial_manager:
            self.serial_manager.data_received.connect(self.handle_rx_response)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        temp_group = QGroupBox("当前温度")
        temp_group.setMaximumHeight(140)
        temp_layout = QVBoxLayout()

        self.temp_label = QLabel("--.-°C")
        self.temp_label.setFont(QFont("Arial", 56, QFont.Bold))
        self.temp_label.setAlignment(Qt.AlignCenter)
        self.temp_label.setStyleSheet("color: #2196F3;")
        temp_layout.addWidget(self.temp_label)

        self.stats_label = QLabel(
            "最小: --.-°C  |  最大: --.-°C  |  平均: --.-°C  |  采样: 0"
        )
        self.stats_label.setFont(QFont("Consolas", 10))
        self.stats_label.setAlignment(Qt.AlignCenter)
        temp_layout.addWidget(self.stats_label)

        temp_group.setLayout(temp_layout)
        main_layout.addWidget(temp_group)

        control_group = QGroupBox("控制设置")
        control_layout = QHBoxLayout()

        info_label = QLabel("ℹ 转换间隔: 约750ms (12位精度)")
        info_label.setStyleSheet("color: #666; font-style: italic;")
        control_layout.addWidget(info_label)
        control_layout.addSpacing(20)

        self.start_btn = QPushButton(" 开始读取")
        self.start_btn.clicked.connect(self.start_reading)
        self.start_btn.setStyleSheet("font-size: 14px; padding: 8px 16px;")
        control_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton(" 停止读取")
        self.stop_btn.clicked.connect(self.stop_reading)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("font-size: 14px; padding: 8px 16px;")
        control_layout.addWidget(self.stop_btn)

        self.clear_btn = QPushButton(" 清除统计")
        self.clear_btn.clicked.connect(self.clear_stats)
        self.clear_btn.setStyleSheet("font-size: 14px; padding: 8px 16px;")
        control_layout.addWidget(self.clear_btn)

        control_layout.addStretch()

        self.timestamp_checkbox = QCheckBox("时间戳")
        self.timestamp_checkbox.setChecked(True)
        self.timestamp_checkbox.toggled.connect(self.toggle_timestamp)
        control_layout.addWidget(self.timestamp_checkbox)

        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)

        log_group = QGroupBox(" 交互日志")
        log_layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)

        clear_log_btn = QPushButton("清除日志")
        clear_log_btn.clicked.connect(self.clear_log)
        clear_log_btn.setMaximumWidth(100)
        log_layout.addWidget(clear_log_btn, alignment=Qt.AlignRight)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def start_reading(self):
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_DS18B20_START, b"")
            self.reading = True
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.append_log(" 开始读取（FPGA自动循环，间隔~750ms）", "INFO")
        else:
            self.append_log(" CDC串口未连接", "ERROR")

    def stop_reading(self):
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_DS18B20_STOP, b"")
            self.reading = False
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.append_log(" 停止读取", "INFO")
        else:
            self.append_log(" CDC串口未连接", "ERROR")

    def clear_stats(self):
        self.temp_min = float("inf")
        self.temp_max = float("-inf")
        self.temp_avg = 0.0
        self.sample_count = 0
        self.update_stats()
        self.append_log(" 统计数据已清除", "INFO")

    def update_temperature(self, temperature):
        self.temp_label.setText(f"{temperature:.2f}°C")
        self.sample_count += 1
        self.temp_min = min(self.temp_min, temperature)
        self.temp_max = max(self.temp_max, temperature)
        self.temp_avg = (
            (self.temp_avg * (self.sample_count - 1)) + temperature
        ) / self.sample_count
        self.update_stats()

    def update_stats(self):
        if self.sample_count > 0:
            self.stats_label.setText(
                f"最小: {self.temp_min:.2f}°C  |  最大: {self.temp_max:.2f}°C  |  "
                f"平均: {self.temp_avg:.2f}°C  |  采样: {self.sample_count}"
            )
        else:
            self.stats_label.setText(
                "最小: --.-°C  |  最大: --.-°C  |  平均: --.-°C  |  采样: 0"
            )

    def handle_rx_response(self, data):
        """
        处理CH340混合数据流 - 智能分离应答帧和温度数据流

        架构说明：
        - FPGA发送：应答帧(AA 55 ...) + 温度数据流(裸数据2字节)
        - 应答帧：7字节标准格式
        - 温度数据：应答帧后2字节小端序温度值

        参考：spi_panel_simple.py的handle_rx_response()方法
        """
        if not isinstance(data, bytes):
            return

        # 将数据追加到缓冲区
        self.data_buffer.extend(data)
        hex_str = " ".join(f"{b:02X}" for b in data)
        self.append_log(f"📥 收到: {hex_str}", "DEBUG")

        # 处理缓冲区数据
        while len(self.data_buffer) > 0:
            # 查找应答帧标记 AA 55
            aa_idx = -1
            for i in range(len(self.data_buffer)):
                if self.data_buffer[i] == 0xAA:
                    aa_idx = i
                    break

            if aa_idx == -1:
                # 没有AA标记，全部是温度数据（裸数据）
                if self.reading and len(self.data_buffer) >= 2:
                    temp_raw = int.from_bytes(
                        bytes(self.data_buffer[:2]), "little", signed=True
                    )
                    temperature = temp_raw * 0.0625
                    self.update_temperature(temperature)
                    self.append_log(
                        f"🌡️ 温度: {temperature:.2f}°C (原始: 0x{temp_raw:04X}={temp_raw})",
                        "RECV",
                    )
                    self.data_buffer = self.data_buffer[2:]
                else:
                    # 数据不完整或不在读取状态，丢弃
                    self.data_buffer.clear()
                break

            # 找到AA标记
            if aa_idx > 0:
                # AA前面有数据，当作温度数据处理
                if self.reading:
                    # 取前面所有数据作为温度数据候选
                    temp_bytes = self.data_buffer[:aa_idx]
                    
                    # 每2字节解析一次温度（DS18B20返回2字节小端序）
                    for i in range(0, len(temp_bytes) - 1, 2):
                        temp_raw = int.from_bytes(
                            bytes(temp_bytes[i:i+2]), "little", signed=True
                        )
                        temperature = temp_raw * 0.0625
                        self.update_temperature(temperature)
                        self.append_log(
                            f"🌡️ 温度: {temperature:.2f}°C (原始: 0x{temp_raw:04X}={temp_raw})",
                            "RECV",
                        )
                # 移动到AA位置
                self.data_buffer = self.data_buffer[aa_idx:]

            # 现在buffer开头是AA，检查是否是完整应答帧
            if len(self.data_buffer) < 7:
                # 数据不足7字节，等待更多数据
                break

            if self.data_buffer[1] == 0x55:
                # 这是应答帧，解析并处理
                frame = bytes(self.data_buffer[:7])
                self.data_buffer = self.data_buffer[7:]

                # 解析应答帧
                mod_id, func_id, status = frame[2], frame[3], frame[4]

                self.append_log(
                    f"🔍 应答帧: mod={mod_id:02X} func={func_id:02X} status={status:02X}",
                    "DEBUG",
                )

                if func_id == CMD_DS18B20_START:
                    self.append_log("⚙ 开始读取确认", "INFO")
                elif func_id == CMD_DS18B20_STOP:
                    self.append_log("⚙ 已停止", "INFO")
            else:
                # AA后面不是55，只是普通数据中的AA字节
                if self.reading:
                    temp_data = bytes([self.data_buffer[0]])
                    # 这个单字节暂存，等下一个字节
                    # 为了简化，直接丢弃并移动1字节
                self.data_buffer = self.data_buffer[1:]


    def toggle_timestamp(self):
        self.show_timestamp = self.timestamp_checkbox.isChecked()

    def append_log(self, message, msg_type="INFO"):
        # 禁用 DEBUG 日志减少刷新
        if msg_type == "DEBUG":
            return  # 禁用DEBUG
        timestamp = (
            f"[{datetime.now().strftime('%H:%M:%S')}] " if self.show_timestamp else ""
        )
        colors = {
            "SEND": "#FF9800",
            "RECV": "#4CAF50",
            "ERROR": "#F44336",
            "WARN": "#FF9800",
            "INFO": "#2196F3",
            "DEBUG": "#9E9E9E",
        }
        color = colors.get(msg_type, "#000000")
        self.log_text.append(
            f'<span style="color: {color};">{timestamp}{message}</span>'
        )
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def clear_log(self):
        self.log_text.clear()
