#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
I2C设备面板 - V4.0 极简版
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QComboBox,
    QTextEdit,
    QLineEdit,
    QCheckBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
import struct
from datetime import datetime

CMD_I2C_WRITE = 0x70
CMD_I2C_READ = 0x71
CMD_OLED_INIT = 0x73
CMD_OLED_CLEAR = 0x74
CMD_OLED_ALLON = 0x75
CMD_OLED_SHOW = 0x76


class I2CDevicePanelSimple(QWidget):
    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.show_timestamp = True

        # 数据缓冲区（用于处理分片数据）
        self.data_buffer = bytearray()

        # 频闪功能
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.blink_toggle)
        self.blink_state = False  # False=清屏, True=全亮
        self.is_blinking = False

        self.init_ui()

        # 连接数据接收信号（I2C只有应答帧，没有专用数据流）
        if self.serial_manager:
            self.serial_manager.data_received.connect(self.handle_rx_response)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        top_layout = QHBoxLayout()
        top_layout.setSpacing(8)
        top_layout.addWidget(QLabel("设备:"))
        self.device_type_combo = QComboBox()
        self.device_type_combo.addItems(["OLED SSD1306", "通用I2C设备"])
        self.device_type_combo.setMaximumWidth(150)
        self.device_type_combo.currentIndexChanged.connect(self.on_device_type_changed)
        top_layout.addWidget(self.device_type_combo)
        top_layout.addSpacing(15)
        top_layout.addWidget(QLabel("地址:"))
        self.dev_addr_input = QLineEdit("0x3C")
        self.dev_addr_input.setMaximumWidth(60)
        top_layout.addWidget(self.dev_addr_input)
        top_layout.addStretch()
        main_layout.addLayout(top_layout)

        self.oled_widget = QWidget()
        oled_layout = QHBoxLayout(self.oled_widget)
        oled_layout.setContentsMargins(0, 0, 0, 0)
        oled_layout.setSpacing(6)
        self.oled_init_btn = QPushButton("🖥️ 初始化")
        self.oled_init_btn.setMaximumWidth(85)
        self.oled_init_btn.clicked.connect(self.oled_init)
        oled_layout.addWidget(self.oled_init_btn)
        self.oled_clear_btn = QPushButton("🧹 清屏")
        self.oled_clear_btn.setMaximumWidth(75)
        self.oled_clear_btn.clicked.connect(self.oled_clear)
        oled_layout.addWidget(self.oled_clear_btn)
        self.oled_allon_btn = QPushButton("💡 全亮")
        self.oled_allon_btn.setMaximumWidth(75)
        self.oled_allon_btn.clicked.connect(self.oled_allon)
        oled_layout.addWidget(self.oled_allon_btn)
        self.oled_blink_btn = QPushButton("⚡ 频闪")
        self.oled_blink_btn.setMaximumWidth(75)
        self.oled_blink_btn.setCheckable(True)
        self.oled_blink_btn.clicked.connect(self.toggle_blink)
        oled_layout.addWidget(self.oled_blink_btn)
        self.oled_text_btn = QPushButton("📝 显示文本")
        self.oled_text_btn.setMaximumWidth(95)
        self.oled_text_btn.clicked.connect(self.oled_show_text)
        oled_layout.addWidget(self.oled_text_btn)
        oled_layout.addStretch()
        main_layout.addWidget(self.oled_widget)

        self.i2c_widget = QWidget()
        i2c_layout = QHBoxLayout(self.i2c_widget)
        i2c_layout.setContentsMargins(0, 0, 0, 0)
        i2c_layout.setSpacing(6)
        i2c_layout.addWidget(QLabel("写入HEX:"))
        self.write_data_input = QLineEdit()
        self.write_data_input.setPlaceholderText("例: 00 AE (空格分隔)")
        i2c_layout.addWidget(self.write_data_input)
        self.write_btn = QPushButton("写入")
        self.write_btn.setMaximumWidth(60)
        self.write_btn.clicked.connect(self.i2c_write)
        i2c_layout.addWidget(self.write_btn)
        i2c_layout.addStretch()
        main_layout.addWidget(self.i2c_widget)

        separator = QWidget()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #ccc;")
        main_layout.addWidget(separator)

        log_header = QHBoxLayout()
        log_header.setSpacing(8)
        log_title = QLabel("💬 I2C交互数据")
        log_title.setStyleSheet("font-weight: bold;")
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.timestamp_checkbox = QCheckBox("时间戳")
        self.timestamp_checkbox.setChecked(True)
        self.timestamp_checkbox.stateChanged.connect(self.toggle_timestamp)
        log_header.addWidget(self.timestamp_checkbox)
        self.clear_log_btn = QPushButton("清除")
        self.clear_log_btn.setMaximumWidth(60)
        self.clear_log_btn.clicked.connect(self.clear_log)
        log_header.addWidget(self.clear_log_btn)
        main_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setPlaceholderText(
            "I2C交互数据显示区\n\n引脚: SDA=L18, SCL=M20\n⚠️ 需要4.7K上拉电阻到3.3V\n"
        )
        main_layout.addWidget(self.log_text, 1)
        self.on_device_type_changed(0)

    def on_device_type_changed(self, index):
        device_type = self.device_type_combo.currentText()
        if "OLED" in device_type:
            self.oled_widget.setVisible(True)
            self.i2c_widget.setVisible(False)
            self.dev_addr_input.setText("0x3C")
            self.append_log(f"[切换] 设备类型: {device_type}", "INFO")
        else:
            self.oled_widget.setVisible(False)
            self.i2c_widget.setVisible(True)
            self.append_log(f"[切换] 设备类型: {device_type}", "INFO")

    def i2c_write(self):
        try:
            dev_addr = int(self.dev_addr_input.text(), 16)
            if dev_addr > 0x7F:
                raise ValueError("地址必须是7位")
            data_str = self.write_data_input.text().strip()
            if not data_str:
                self.append_log("❌ 请输入要写入的数据", "ERROR")
                return
            write_data = bytes.fromhex(data_str.replace(" ", ""))
            byte_count = len(write_data)
            if byte_count == 0 or byte_count > 255:
                raise ValueError("数据长度必须在1-255字节")
            payload = struct.pack("BB", dev_addr, byte_count) + write_data
            self.append_log(
                f"📤 I2C写入: 地址=0x{dev_addr:02X} 数据={write_data.hex(' ').upper()}",
                "SEND",
            )
            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_I2C_WRITE, payload)
            else:
                self.append_log("❌ CDC串口未连接", "ERROR")
        except ValueError as e:
            self.append_log(f"❌ 输入错误: {e}", "ERROR")

    def oled_init(self):
        """OLED初始化 - 如果正在频闪则先停止"""
        # 如果频闪正在运行，先停止它
        if self.is_blinking:
            self.is_blinking = False
            self.blink_timer.stop()
            self.oled_blink_btn.setChecked(False)
            self.append_log("⚡ 自动停止频闪", "INFO")

        self.append_log("🖥️ OLED初始化 (FPGA自动执行27条命令)", "INFO")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_OLED_INIT, bytes())
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def oled_clear(self):
        """手动清屏 - 如果正在频闪则先停止"""
        # 如果频闪正在运行，先停止它
        if self.is_blinking:
            self.is_blinking = False
            self.blink_timer.stop()
            self.oled_blink_btn.setChecked(False)  # 取消按钮选中状态
            self.append_log("⚡ 自动停止频闪", "INFO")

        self.append_log("🧹 OLED清屏 (清除8页显示数据)", "INFO")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_OLED_CLEAR, bytes())
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def oled_allon(self):
        """OLED全亮 - 如果正在频闪则先停止"""
        # 如果频闪正在运行，先停止它
        if self.is_blinking:
            self.is_blinking = False
            self.blink_timer.stop()
            self.oled_blink_btn.setChecked(False)
            self.append_log("⚡ 自动停止频闪", "INFO")

        self.append_log("💡 OLED全亮 (填充8页0xFF数据)", "INFO")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_OLED_ALLON, bytes())
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def oled_show_text(self):
        """OLED显示文本 - 如果正在频闪则先停止"""
        # 如果频闪正在运行，先停止它
        if self.is_blinking:
            self.is_blinking = False
            self.blink_timer.stop()
            self.oled_blink_btn.setChecked(False)
            self.append_log("⚡ 自动停止频闪", "INFO")

        self.append_log("📝 OLED显示文本: 芯辰大海 / 点亮未来", "INFO")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_OLED_SHOW, bytes())
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def toggle_blink(self):
        """切换频闪功能"""
        if self.oled_blink_btn.isChecked():
            # 开始频闪
            self.is_blinking = True
            self.blink_state = False
            self.blink_timer.start(500)  # 500ms切换一次
            # 移除按钮变色效果，保持默认样式
            self.append_log("⚡ 开始频闪 (500ms间隔)", "INFO")
        else:
            # 停止频闪
            self.is_blinking = False
            self.blink_timer.stop()
            self.append_log("⚡ 停止频闪", "INFO")

    def blink_toggle(self):
        """频闪定时器回调 - 交替清屏和全亮"""
        if not self.is_blinking:
            return

        if self.serial_manager and self.serial_manager.is_connected():
            if self.blink_state:
                # 当前是全亮，切换到清屏
                self.serial_manager.send_command(CMD_OLED_CLEAR, bytes())
                self.blink_state = False
            else:
                # 当前是清屏，切换到全亮
                self.serial_manager.send_command(CMD_OLED_ALLON, bytes())
                self.blink_state = True

    def toggle_timestamp(self):
        self.show_timestamp = self.timestamp_checkbox.isChecked()

    def append_log(self, message, msg_type="INFO"):
        timestamp = ""
        if self.show_timestamp:
            timestamp = f"[{datetime.now().strftime('%H:%M:%S')}] "
        color_map = {
            "SEND": "#2196F3",
            "RECV": "#4CAF50",
            "INFO": "#9E9E9E",
            "ERROR": "#F44336",
        }
        color = color_map.get(msg_type, "#000000")
        formatted = f'<span style="color:{color}">{timestamp}{message}</span>'
        self.log_text.append(formatted)

    def clear_log(self):
        self.log_text.clear()

    def handle_rx_response(self, data):
        """
        处理接收到的数据 - 使用缓冲区机制提高健壮性

        改进说明：
        - 添加数据缓冲区，正确处理分片传输
        - 查找应答帧标记（AA 55）
        - 提取并解析完整的7字节应答帧

        参考：DS18B20和SPI面板的数据处理机制
        """
        if not isinstance(data, bytes):
            return

        # 将数据追加到缓冲区
        self.data_buffer.extend(data)

        # 处理缓冲区中的数据
        while len(self.data_buffer) >= 7:
            # 查找应答帧起始标记 AA 55
            aa_idx = -1
            for i in range(len(self.data_buffer) - 1):
                if self.data_buffer[i] == 0xAA and self.data_buffer[i + 1] == 0x55:
                    aa_idx = i
                    break

            if aa_idx == -1:
                # 没有找到应答帧，清空缓冲区（I2C没有其他业务数据）
                self.data_buffer.clear()
                break

            # 如果AA不在开头，丢弃前面的无效数据
            if aa_idx > 0:
                self.data_buffer = self.data_buffer[aa_idx:]

            # 检查是否有完整的7字节应答帧
            if len(self.data_buffer) < 7:
                # 数据不足，等待更多数据
                break

            # 提取应答帧
            frame = bytes(self.data_buffer[:7])
            self.data_buffer = self.data_buffer[7:]

            # 解析应答帧
            func_id = frame[3]
            status = frame[4]

            # 只处理I2C相关命令
            if func_id not in [
                CMD_I2C_WRITE,
                CMD_OLED_INIT,
                CMD_OLED_CLEAR,
                CMD_OLED_ALLON,
                CMD_OLED_SHOW,
            ]:
                continue

            # 解析状态码
            status_msg = {
                0x00: "✓ 成功",
                0x01: "✗ 校验错误",
                0x02: "✗ 无效命令",
                0x03: "✗ 参数错误",
                0x04: "✗ NACK",
            }.get(status, f"✗ 状态(0x{status:02X})")

            # 根据命令类型显示对应的日志
            if func_id == CMD_I2C_WRITE:
                self.append_log(
                    f"📥 写入应答: {status_msg}", "RECV" if status == 0x00 else "ERROR"
                )
            elif func_id == CMD_OLED_INIT:
                self.append_log(
                    f"📥 OLED初始化{'完成' if status == 0x00 else '失败: ' + status_msg}",
                    "RECV" if status == 0x00 else "ERROR",
                )
            elif func_id == CMD_OLED_CLEAR:
                self.append_log(
                    f"📥 OLED清屏{'完成' if status == 0x00 else '失败: ' + status_msg}",
                    "RECV" if status == 0x00 else "ERROR",
                )
            elif func_id == CMD_OLED_ALLON:
                self.append_log(
                    f"📥 OLED全亮{'完成' if status == 0x00 else '失败: ' + status_msg}",
                    "RECV" if status == 0x00 else "ERROR",
                )
            elif func_id == CMD_OLED_SHOW:
                self.append_log(
                    f"📥 OLED显示{'完成' if status == 0x00 else '失败: ' + status_msg}",
                    "RECV" if status == 0x00 else "ERROR",
                )


I2CDevicePanel = I2CDevicePanelSimple
