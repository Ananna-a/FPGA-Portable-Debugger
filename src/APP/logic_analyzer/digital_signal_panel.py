#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
8路数字信号测量面板
功能：测量数字信号的频率、高低电平时间、占空比
命令：0x66 开始测量, 0x67 停止测量, 0x68 读取结果
日期：2025-11-06
"""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QTextEdit,
    QCheckBox,
    QGridLayout,
    QFrame,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from datetime import datetime
import struct

# 命令定义
CMD_DSA_START = 0x66  # 开始8路测量
CMD_DSA_STOP = 0x67  # 停止测量
CMD_DSA_READ = 0x68  # 读取指定通道结果


class DigitalSignalPanel(QWidget):
    """8路数字信号测量面板"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.show_timestamp = True
        self.measuring = False

        # 数据缓冲区和状态标志
        self.data_buffer = bytearray()
        self.waiting_data_channel = None  # 等待哪个通道的数据

        # 顺序读取状态机
        self.reading_channels = False
        self.current_reading_ch = 0
        self.read_next_timer = QTimer()
        self.read_next_timer.setSingleShot(True)
        self.read_next_timer.timeout.connect(self.read_next_channel)

        # 8路测量结果缓存
        self.channel_data = {}
        for ch in range(8):
            self.channel_data[ch] = {"freq": 0, "high_us": 0, "low_us": 0, "duty": 0.0}

        # 自动刷新定时器
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.auto_refresh_all)
        self.refresh_timer.setInterval(1500)  # 每1.5秒刷新一次

        self.init_ui()

        if self.serial_manager:
            # 🔥 修改：使用DSA专用信号（参照ADC频率测量）
            self.serial_manager.dsa_measurement_received.connect(
                self.on_dsa_measurement_received
            )

            # 保留通用信号用于应答帧日志显示
            self.serial_manager.data_received.connect(self.handle_rx_response)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ========== 控制面板 ==========
        control_group = QGroupBox("测量控制")
        control_group.setMaximumHeight(80)
        control_layout = QHBoxLayout()

        info_label = QLabel("ℹ 测量周期: 1秒 | 同时测量8路信号")
        info_label.setStyleSheet("color: #666; font-style: italic;")
        control_layout.addWidget(info_label)
        control_layout.addSpacing(20)

        self.start_btn = QPushButton("🔍 开始测量")
        self.start_btn.clicked.connect(self.start_measurement)
        self.start_btn.setStyleSheet("font-size: 13px; padding: 8px 16px;")
        self.start_btn.setToolTip("启动8路持续测量（后台运行，随时可读取结果）")
        control_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹ 停止测量")
        self.stop_btn.clicked.connect(self.stop_measurement)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("font-size: 13px; padding: 8px 16px;")
        self.stop_btn.setToolTip("停止后台测量")
        control_layout.addWidget(self.stop_btn)

        self.refresh_btn = QPushButton("🔄 刷新数据")
        self.refresh_btn.clicked.connect(self.refresh_all_channels)
        self.refresh_btn.setStyleSheet("font-size: 13px; padding: 8px 16px;")
        self.refresh_btn.setToolTip("读取8路最新测量结果")
        control_layout.addWidget(self.refresh_btn)

        control_layout.addStretch()

        self.auto_refresh_cb = QCheckBox("自动刷新")
        self.auto_refresh_cb.setChecked(False)
        self.auto_refresh_cb.toggled.connect(self.toggle_auto_refresh)
        control_layout.addWidget(self.auto_refresh_cb)

        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)

        # ========== 8路测量结果显示 ==========
        results_group = QGroupBox("📊 8路测量结果")
        results_layout = QGridLayout()
        results_layout.setSpacing(8)

        # 表头
        headers = ["通道", "频率 (Hz)", "高电平 (μs)", "低电平 (μs)", "占空比 (%)"]
        for col, header in enumerate(headers):
            label = QLabel(header)
            label.setFont(QFont("微软雅黑", 10, QFont.Bold))
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("background-color: #e0e0e0; padding: 6px;")
            results_layout.addWidget(label, 0, col)

        # 创建8行数据显示
        self.channel_labels = {}
        for ch in range(8):
            row = ch + 1

            # 通道号
            ch_label = QLabel(f"CH{ch}")
            ch_label.setFont(QFont("微软雅黑", 10, QFont.Bold))
            ch_label.setAlignment(Qt.AlignCenter)
            ch_label.setStyleSheet("background-color: #f5f5f5; padding: 4px;")
            results_layout.addWidget(ch_label, row, 0)

            # 频率
            freq_label = QLabel("0 Hz")
            freq_label.setFont(QFont("Consolas", 10))
            freq_label.setAlignment(Qt.AlignCenter)
            freq_label.setStyleSheet("padding: 4px;")
            results_layout.addWidget(freq_label, row, 1)

            # 高电平时间
            high_label = QLabel("0 μs")
            high_label.setFont(QFont("Consolas", 10))
            high_label.setAlignment(Qt.AlignCenter)
            high_label.setStyleSheet("padding: 4px;")
            results_layout.addWidget(high_label, row, 2)

            # 低电平时间
            low_label = QLabel("0 μs")
            low_label.setFont(QFont("Consolas", 10))
            low_label.setAlignment(Qt.AlignCenter)
            low_label.setStyleSheet("padding: 4px;")
            results_layout.addWidget(low_label, row, 3)

            # 占空比
            duty_label = QLabel("0.00%")
            duty_label.setFont(QFont("Consolas", 10))
            duty_label.setAlignment(Qt.AlignCenter)
            duty_label.setStyleSheet("padding: 4px;")
            results_layout.addWidget(duty_label, row, 4)

            self.channel_labels[ch] = {
                "freq": freq_label,
                "high": high_label,
                "low": low_label,
                "duty": duty_label,
            }

        results_group.setLayout(results_layout)
        main_layout.addWidget(results_group)

        # ========== 交互日志 ==========
        log_group = QGroupBox("📋 交互日志")
        log_layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)

        log_controls = QHBoxLayout()
        self.timestamp_checkbox = QCheckBox("时间戳")
        self.timestamp_checkbox.setChecked(True)
        self.timestamp_checkbox.toggled.connect(self.toggle_timestamp)
        log_controls.addWidget(self.timestamp_checkbox)

        log_controls.addStretch()

        clear_log_btn = QPushButton("清除日志")
        clear_log_btn.clicked.connect(self.clear_log)
        clear_log_btn.setMaximumWidth(100)
        log_controls.addWidget(clear_log_btn)

        log_layout.addLayout(log_controls)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def start_measurement(self):
        """开始测量"""
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_DSA_START, b"")
            self.measuring = True
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.append_log(
                "🔍 已发送开始测量命令（8路后台持续测量，门控时间1秒）", "INFO"
            )
            if self.auto_refresh_cb.isChecked():
                self.append_log("💡 自动刷新模式：持续测量，每1.5秒读取结果", "INFO")
            else:
                self.append_log("💡 单次测量模式：读取完成后自动停止", "INFO")

            # 延迟1.5秒后自动读取全部通道（等待第一次测量完成）
            QTimer.singleShot(1500, self.refresh_all_channels)
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def stop_measurement(self):
        """停止测量"""
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_DSA_STOP, b"")
            self.measuring = False
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.append_log("⏹ 已停止测量", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def refresh_all_channels(self):
        """刷新全部8路通道数据（顺序读取）"""
        if self.serial_manager and self.serial_manager.is_connected():
            if not self.reading_channels:
                self.append_log("🔄 正在读取8路测量结果...", "INFO")
                self.reading_channels = True
                self.current_reading_ch = 0
                self.read_next_channel()
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def read_next_channel(self):
        """读取下一个通道（状态机）"""
        if not self.reading_channels:
            return

        if self.current_reading_ch < 8:
            self.read_channel(self.current_reading_ch)
            self.current_reading_ch += 1
            # 50ms后读取下一个通道
            self.read_next_timer.start(50)
        else:
            # 全部读取完成
            self.reading_channels = False
            self.append_log("✅ 8路通道读取完成", "INFO")

            # 🔥 V8.7.67: 自动停止测量，释放FPGA状态机
            # 原因：避免FPGA长时间停留在MEASURING状态，阻塞后续ADC频率测量
            if self.measuring and not self.auto_refresh_cb.isChecked():
                QTimer.singleShot(100, self.auto_stop_after_read)

    def auto_stop_after_read(self):
        """读取完成后自动停止测量（仅在非自动刷新模式）"""
        if (
            self.measuring
            and self.serial_manager
            and self.serial_manager.is_connected()
        ):
            self.serial_manager.send_command(CMD_DSA_STOP, b"")
            self.measuring = False
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.append_log("⏹ 自动停止测量（释放FPGA资源）", "INFO")

    def read_channel(self, channel):
        """读取指定通道的测量结果"""
        if self.serial_manager and self.serial_manager.is_connected():
            payload = bytes([channel])
            self.serial_manager.send_command(CMD_DSA_READ, payload)
            self.waiting_data_channel = channel
            self.append_log(f"📡 读取CH{channel}数据", "DEBUG")
        else:
            self.append_log(f"❌ 无法读取CH{channel}：CDC串口未连接", "ERROR")

    def toggle_auto_refresh(self, checked):
        """切换自动刷新"""
        if checked:
            self.refresh_timer.start()
            self.append_log("✅ 自动刷新已启用（每1.5秒）", "INFO")
        else:
            self.refresh_timer.stop()
            self.append_log("⏸ 自动刷新已停用", "INFO")

    def auto_refresh_all(self):
        """自动刷新全部通道"""
        if self.measuring:
            self.refresh_all_channels()

    def handle_rx_response(self, data):
        """
        处理CH340返回的数据流（仅用于应答帧日志显示）

        注意：DSA测量数据由on_dsa_measurement_received()专门处理
        """
        if not isinstance(data, bytes):
            return

        # 只处理应答帧（用于日志显示）
        if len(data) >= 7 and data[0] == 0xAA and data[1] == 0x55:
            mod_id, func_id, status = data[2], data[3], data[4]

            # 过滤0x68的应答帧（因为数据由专用信号处理）
            if func_id == CMD_DSA_READ:
                return

            # 显示其他命令的应答帧
            if func_id == CMD_DSA_START:
                self.append_log("⚙ 开始测量确认", "INFO")
            elif func_id == CMD_DSA_STOP:
                self.append_log("⚙ 已停止测量", "INFO")

    @Slot(int, dict)
    def on_dsa_measurement_received(self, channel, measurement):
        """
        接收DSA测量数据（参照oscilloscope_tab的on_frequency_data_received）

        Args:
            channel: 通道号 (0-7)
            measurement: 测量数据字典 {freq, high_cycles, low_cycles}
        """
        freq = measurement["freq"]
        high_cycles = measurement["high_cycles"]
        low_cycles = measurement["low_cycles"]

        # 上位机计算（50MHz时钟）
        high_us = high_cycles / 50.0 if high_cycles > 0 else 0
        low_us = low_cycles / 50.0 if low_cycles > 0 else 0

        total_cycles = high_cycles + low_cycles
        if total_cycles > 0:
            duty_percent = (high_cycles * 100.0) / total_cycles
        else:
            duty_percent = 0.0

        # 更新缓存
        self.channel_data[channel] = {
            "freq": freq,
            "high_us": high_us,
            "low_us": low_us,
            "duty": duty_percent,
        }

        # 更新显示
        self.update_channel_display(channel)

        # 清除等待标志
        if self.waiting_data_channel == channel:
            self.waiting_data_channel = None

        # 调试日志（可选）
        self.append_log(
            f"✅ CH{channel}: {freq}Hz, H={high_us:.2f}μs, L={low_us:.2f}μs, D={duty_percent:.2f}%",
            "INFO",
        )

    def update_channel_display(self, channel):
        """更新指定通道的显示"""
        if channel not in self.channel_labels:
            return

        data = self.channel_data[channel]
        labels = self.channel_labels[channel]

        # 格式化频率
        freq = data["freq"]
        if freq >= 1_000_000:
            freq_str = f"{freq / 1_000_000:.3f} MHz"
        elif freq >= 1_000:
            freq_str = f"{freq / 1_000:.3f} kHz"
        else:
            freq_str = f"{freq} Hz"

        # 格式化时间
        high_us = data["high_us"]
        if high_us >= 1_000_000:
            high_str = f"{high_us / 1_000_000:.3f} s"
        elif high_us >= 1_000:
            high_str = f"{high_us / 1_000:.3f} ms"
        else:
            high_str = f"{high_us} μs"

        low_us = data["low_us"]
        if low_us >= 1_000_000:
            low_str = f"{low_us / 1_000_000:.3f} s"
        elif low_us >= 1_000:
            low_str = f"{low_us / 1_000:.3f} ms"
        else:
            low_str = f"{low_us} μs"

        # 占空比
        duty_str = f"{data['duty']:.2f}%"

        # 更新标签
        labels["freq"].setText(freq_str)
        labels["high"].setText(high_str)
        labels["low"].setText(low_str)
        labels["duty"].setText(duty_str)

        # 根据占空比调整颜色
        duty = data["duty"]
        if 45 <= duty <= 55:
            duty_color = "#4CAF50"  # 绿色：接近50%
        elif 30 <= duty <= 70:
            duty_color = "#FF9800"  # 橙色：偏离50%
        else:
            duty_color = "#F44336"  # 红色：极端占空比

        labels["duty"].setStyleSheet(
            f"color: {duty_color}; font-weight: bold; padding: 4px;"
        )

    def toggle_timestamp(self):
        self.show_timestamp = self.timestamp_checkbox.isChecked()

    def append_log(self, message, msg_type="INFO"):
        """追加日志"""
        # 只禁用某些DEBUG日志（过滤"应答帧"相关的DEBUG，但保留数据接收日志）
        if msg_type == "DEBUG":
            # 禁用频繁的"应答帧"DEBUG日志
            if "应答帧" in message and "DSA" not in message:
                return
            # 禁用"裸数据异常"的DEBUG日志（太频繁）
            if "裸数据异常" in message:
                return

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
        """清除日志"""
        self.log_text.clear()
