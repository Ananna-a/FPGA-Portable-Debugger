#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPI设备面板 - V2.0 极简版
日期: 2025-11-01

功能:
  - SPI快捷配置（频率选择，固定Mode2）
  - W25Q128 Flash快捷操作（读ID、读取、写入）
  - 通用SPI传输
  - 交互数据显示（不显示原始应答帧）

引脚定义:
  - SCLK: B1, CS: B2, MOSI: M17, MISO: A1
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

# SPI命令码定义
CMD_SPI_CONFIG = 0x80  # SPI配置 [freq_khz(16)][cpol][cpha][msb_first]
CMD_SPI_TRANSFER = 0x81  # SPI传输 [byte_count][tx_data...]
CMD_SPI_FLASH_ID = 0x82  # 读取Flash ID (快捷命令)
CMD_SPI_FLASH_READ = 0x83  # Flash读取 [addr(24)][byte_count]
CMD_SPI_FLASH_WRITE = 0x84  # Flash写入 [addr(24)][data...]
CMD_SPI_FLASH_ERASE_SECTOR = 0x85  # 扇区擦除 [addr(24)] - 4KB
CMD_SPI_FLASH_ERASE_CHIP = 0x86  # 全片擦除
CMD_SPI_FLASH_READ_STATUS = 0x87  # 读状态寄存器


class SPIDevicePanelSimple(QWidget):
    """SPI设备控制面板 - 极简版"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.show_timestamp = True
        self.last_read_count = 0  # 记录最后一次Flash读取的字节数

        # 数据缓冲区，用于智能分离应答帧和Flash数据流
        self.data_buffer = bytearray()

        # Flash数据流累积缓冲区，用于批量显示减少刷新
        self.flash_data_accumulator = bytearray()

        # 当前等待的Flash数据类型和长度
        self.waiting_flash_data = None  # 'id', 'read', 'status' 或 None
        self.waiting_data_length = 0

        # 定时器：用于批量显示累积的Flash数据
        self.flash_display_timer = QTimer()
        self.flash_display_timer.timeout.connect(self.flush_flash_data)
        self.flash_display_timer.setSingleShot(True)

        self.init_ui()

        # 注册串口数据监听器
        if self.serial_manager:
            self.serial_manager.data_received.connect(self.handle_rx_response)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ========== 顶部：设备类型 + 快捷配置 ==========
        top_layout = QHBoxLayout()
        top_layout.setSpacing(8)

        # 设备类型选择
        top_layout.addWidget(QLabel("设备:"))
        self.device_type_combo = QComboBox()
        self.device_type_combo.addItems(["W25Q128 Flash", "通用SPI设备"])
        self.device_type_combo.setMaximumWidth(150)
        self.device_type_combo.currentIndexChanged.connect(self.on_device_type_changed)
        top_layout.addWidget(self.device_type_combo)

        top_layout.addSpacing(15)

        # 频率选择
        top_layout.addWidget(QLabel("频率:"))
        self.freq_combo = QComboBox()
        self.freq_combo.addItems(["1 MHz", "2 MHz", "4 MHz", "8 MHz", "12 MHz"])
        self.freq_combo.setMaximumWidth(80)
        self.freq_combo.setCurrentIndex(0)  # 默认1MHz (适合24MHz逻辑分析仪)
        self.freq_combo.setToolTip(
            "SPI时钟频率\n"
            "1MHz: 精确1.00MHz\n"
            "2MHz: 实际2.08MHz\n"
            "4MHz: 实际4.17MHz\n"
            "8MHz: 实际8.33MHz\n"
            "12MHz: 实际12.5MHz\n"
            "\n⚠️ 注意：24MHz逻辑分析仪建议≤8MHz"
        )
        top_layout.addWidget(self.freq_combo)

        # SPI模式选择
        top_layout.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Mode 0", "Mode 1", "Mode 2", "Mode 3"])
        self.mode_combo.setMaximumWidth(80)
        self.mode_combo.setCurrentIndex(0)  # 默认Mode 0（匹配逻辑分析仪默认）
        self.mode_combo.setToolTip(
            "Mode 0: CPOL=0,CPHA=0\nMode 1: CPOL=0,CPHA=1\nMode 2: CPOL=1,CPHA=0\nMode 3: CPOL=1,CPHA=1"
        )
        top_layout.addWidget(self.mode_combo)

        # 位序选择（新增）
        top_layout.addWidget(QLabel("位序:"))
        self.bit_order_combo = QComboBox()
        self.bit_order_combo.addItems(["MSB First", "LSB First"])
        self.bit_order_combo.setMaximumWidth(90)
        self.bit_order_combo.setCurrentIndex(0)  # 默认MSB First（标准）
        self.bit_order_combo.setToolTip(
            "MSB First: 高位先发（标准SPI）\nLSB First: 低位先发（某些特殊设备）"
        )
        top_layout.addWidget(self.bit_order_combo)

        # 应用配置按钮
        self.apply_config_btn = QPushButton("⚙️ 应用")
        self.apply_config_btn.setMaximumWidth(70)
        self.apply_config_btn.clicked.connect(self.apply_spi_config)
        top_layout.addWidget(self.apply_config_btn)

        top_layout.addStretch()

        main_layout.addLayout(top_layout)

        # ========== W25Q128 Flash快捷操作（可切换显示）==========
        self.flash_widget = QWidget()
        flash_main_layout = QVBoxLayout(self.flash_widget)
        flash_main_layout.setContentsMargins(0, 0, 0, 0)
        flash_main_layout.setSpacing(4)

        # 第一行：快捷功能按钮
        flash_row1 = QHBoxLayout()
        flash_row1.setSpacing(8)

        self.read_id_btn = QPushButton("读ID")
        self.read_id_btn.setToolTip("读取Flash芯片ID (0x9F指令)")
        self.read_id_btn.setFixedWidth(70)
        self.read_id_btn.clicked.connect(self.flash_read_id)
        flash_row1.addWidget(self.read_id_btn)

        self.read_status_btn = QPushButton("读状态")
        self.read_status_btn.setToolTip("读取Flash状态寄存器")
        self.read_status_btn.setFixedWidth(70)
        self.read_status_btn.clicked.connect(self.flash_read_status)
        flash_row1.addWidget(self.read_status_btn)

        self.erase_chip_btn = QPushButton("全片擦除")
        self.erase_chip_btn.setToolTip(
            "擦除整个Flash芯片 (需要几十秒)\n⚠️ 危险操作！请确认"
        )
        self.erase_chip_btn.setFixedWidth(90)
        self.erase_chip_btn.clicked.connect(self.flash_erase_chip)
        flash_row1.addWidget(self.erase_chip_btn)

        self.erase_sector_btn = QPushButton("扇区擦除")
        self.erase_sector_btn.setToolTip("擦除4KB扇区 (地址会自动对齐)")
        self.erase_sector_btn.setFixedWidth(90)
        self.erase_sector_btn.clicked.connect(self.flash_erase_sector)
        flash_row1.addWidget(self.erase_sector_btn)

        flash_row1.addStretch()
        flash_main_layout.addLayout(flash_row1)

        # 第二行：地址和数据操作
        flash_row2 = QHBoxLayout()
        flash_row2.setSpacing(8)

        flash_row2.addWidget(QLabel("地址:"))
        self.flash_addr_input = QLineEdit()
        self.flash_addr_input.setText("000000")
        self.flash_addr_input.setFixedWidth(80)
        self.flash_addr_input.setToolTip("24位地址 (000000-FFFFFF)")
        flash_row2.addWidget(self.flash_addr_input)

        # 地址快捷按钮
        addr_0_btn = QPushButton("0x0")
        addr_0_btn.setFixedWidth(50)
        addr_0_btn.clicked.connect(lambda: self.flash_addr_input.setText("000000"))
        flash_row2.addWidget(addr_0_btn)

        addr_1k_btn = QPushButton("1K")
        addr_1k_btn.setFixedWidth(50)
        addr_1k_btn.clicked.connect(lambda: self.flash_addr_input.setText("000400"))
        flash_row2.addWidget(addr_1k_btn)

        addr_4k_btn = QPushButton("4K")
        addr_4k_btn.setFixedWidth(50)
        addr_4k_btn.clicked.connect(lambda: self.flash_addr_input.setText("001000"))
        flash_row2.addWidget(addr_4k_btn)

        flash_row2.addSpacing(15)

        flash_row2.addWidget(QLabel("字节:"))
        self.flash_read_len_spin = QSpinBox()
        self.flash_read_len_spin.setRange(1, 256)
        self.flash_read_len_spin.setValue(16)
        self.flash_read_len_spin.setFixedWidth(60)
        flash_row2.addWidget(self.flash_read_len_spin)

        # 长度快捷按钮
        len_16_btn = QPushButton("16")
        len_16_btn.setFixedWidth(45)
        len_16_btn.clicked.connect(lambda: self.flash_read_len_spin.setValue(16))
        flash_row2.addWidget(len_16_btn)

        len_64_btn = QPushButton("64")
        len_64_btn.setFixedWidth(45)
        len_64_btn.clicked.connect(lambda: self.flash_read_len_spin.setValue(64))
        flash_row2.addWidget(len_64_btn)

        len_256_btn = QPushButton("256")
        len_256_btn.setFixedWidth(50)
        len_256_btn.clicked.connect(lambda: self.flash_read_len_spin.setValue(256))
        flash_row2.addWidget(len_256_btn)

        flash_row2.addSpacing(15)

        self.flash_read_btn = QPushButton("读取")
        self.flash_read_btn.setFixedWidth(70)
        self.flash_read_btn.clicked.connect(self.flash_read)
        flash_row2.addWidget(self.flash_read_btn)

        flash_row2.addStretch()
        flash_main_layout.addLayout(flash_row2)

        # 第三行：写入操作
        flash_row3 = QHBoxLayout()
        flash_row3.setSpacing(8)

        flash_row3.addWidget(QLabel("写入数据:"))
        self.flash_write_input = QLineEdit()
        self.flash_write_input.setPlaceholderText(
            "HEX (例: 48 65 6C 6C 6F 或 AA 55 BB CC)"
        )
        flash_row3.addWidget(self.flash_write_input)

        # 快捷写入按钮
        write_hello_btn = QPushButton("Hello")
        write_hello_btn.setFixedWidth(60)
        write_hello_btn.setToolTip("快捷写入'Hello'字符串")
        write_hello_btn.clicked.connect(
            lambda: self.flash_write_input.setText("48 65 6C 6C 6F")
        )
        flash_row3.addWidget(write_hello_btn)

        write_test_btn = QPushButton("Test")
        write_test_btn.setFixedWidth(60)
        write_test_btn.setToolTip("快捷写入'Test'字符串")
        write_test_btn.clicked.connect(
            lambda: self.flash_write_input.setText("54 65 73 74")
        )
        flash_row3.addWidget(write_test_btn)

        self.flash_write_btn = QPushButton("写入")
        self.flash_write_btn.setFixedWidth(70)
        self.flash_write_btn.clicked.connect(self.flash_write)
        flash_row3.addWidget(self.flash_write_btn)

        flash_main_layout.addLayout(flash_row3)

        main_layout.addWidget(self.flash_widget)

        # ========== 通用SPI操作（可切换显示）==========
        self.spi_widget = QWidget()
        spi_layout = QHBoxLayout(self.spi_widget)
        spi_layout.setContentsMargins(0, 0, 0, 0)
        spi_layout.setSpacing(6)

        # 发送数据
        spi_layout.addWidget(QLabel("发送HEX:"))
        self.tx_data_input = QLineEdit()
        self.tx_data_input.setPlaceholderText("例: 9F 或 03 00 00 00")
        spi_layout.addWidget(self.tx_data_input)

        self.transfer_btn = QPushButton("传输")
        self.transfer_btn.setMaximumWidth(60)
        self.transfer_btn.clicked.connect(self.spi_transfer)
        spi_layout.addWidget(self.transfer_btn)

        main_layout.addWidget(self.spi_widget)

        # ========== 分隔线 ==========
        separator = QWidget()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #ccc;")
        main_layout.addWidget(separator)

        # ========== 交互日志区（占据剩余空间）==========
        log_header = QHBoxLayout()
        log_header.setSpacing(8)

        log_title = QLabel("💬 SPI交互数据")
        log_title.setStyleSheet("font-weight: bold;")
        log_header.addWidget(log_title)

        log_header.addStretch()

        self.timestamp_checkbox = QCheckBox("时间戳")
        self.timestamp_checkbox.setChecked(self.show_timestamp)
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
            "SPI交互数据显示区\n\n"
            "引脚: SCLK=B1, CS=B2, MOSI=M17, MISO=A1\n"
            "默认: 1MHz, Mode 0 (CPOL=0, CPHA=0), MSB First\n\n"
            "⚠️ 首次使用请点击'应用'配置SPI参数\n"
            "⚠️ 24MHz逻辑分析仪建议频率≤8MHz\n\n"
            "W25Q128功能:\n"
            "  • 读ID: 读取厂商和设备ID\n"
            "  • 读状态: 查看BUSY/WEL等状态位\n"
            "  • 读取/写入: 指定地址进行数据操作\n"
            "  • 扇区擦除: 擦除4KB扇区 (地址自动对齐)\n"
            "  • 全片擦除: 擦除整个Flash (需20-80秒)\n"
        )
        main_layout.addWidget(self.log_text, 1)  # stretch=1，占据剩余空间

        # 初始化设备类型（默认显示Flash控制）
        self.on_device_type_changed(0)

    # ========== 设备类型切换方法 ==========

    def on_device_type_changed(self, index):
        """设备类型切换"""
        device_type = self.device_type_combo.currentText()

        # 根据设备类型显示/隐藏对应控件
        if "W25Q128" in device_type:
            self.flash_widget.setVisible(True)
            self.spi_widget.setVisible(False)
            self.append_log(f"[切换] 设备类型: {device_type}", "INFO")
        else:
            self.flash_widget.setVisible(False)
            self.spi_widget.setVisible(True)
            self.append_log(f"[切换] 设备类型: {device_type}", "INFO")

    # ========== 功能方法 ==========

    def apply_spi_config(self):
        """应用SPI配置"""
        try:
            # 解析频率选择
            freq_text = self.freq_combo.currentText()
            freq_map = {
                "1 MHz": 1000,
                "2 MHz": 2000,
                "4 MHz": 4000,
                "8 MHz": 8000,
                "12 MHz": 12000,
            }
            freq_khz = freq_map.get(freq_text, 1000)

            # 解析SPI模式
            mode_text = self.mode_combo.currentText()
            mode_num = int(mode_text.split()[1])  # 提取数字 0/1/2/3

            # 根据模式设置CPOL和CPHA
            # Mode 0: CPOL=0, CPHA=0 (默认，标准)
            # Mode 1: CPOL=0, CPHA=1
            # Mode 2: CPOL=1, CPHA=0
            # Mode 3: CPOL=1, CPHA=1
            cpol = (mode_num >> 1) & 0x01  # 高位是CPOL
            cpha = mode_num & 0x01  # 低位是CPHA

            # 解析位序
            bit_order_text = self.bit_order_combo.currentText()
            msb_first = 1 if "MSB" in bit_order_text else 0

            # 构造payload: [freq_khz(16)][cpol][cpha][msb_first]
            payload = struct.pack("<HBBB", freq_khz, cpol, cpha, msb_first)

            self.append_log(
                f"📤 配置SPI: {freq_text}, {mode_text} (CPOL={cpol}, CPHA={cpha}), {bit_order_text}",
                "SEND",
            )

            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_SPI_CONFIG, payload)
                self.append_log("✅ 配置已发送，等待生效...", "INFO")
                # 延迟提示
                from PySide6.QtCore import QTimer

                QTimer.singleShot(
                    50, lambda: self.append_log("  ✓ 配置应已生效", "INFO")
                )
            else:
                self.append_log("❌ CDC串口未连接", "ERROR")

        except Exception as e:
            self.append_log(f"❌ 配置失败: {e}", "ERROR")

    def spi_transfer(self):
        """SPI传输"""
        try:
            data_str = self.tx_data_input.text().strip()
            if not data_str:
                self.append_log("❌ 请输入要发送的数据", "ERROR")
                return

            # 解析十六进制数据
            tx_data = bytes.fromhex(data_str.replace(" ", ""))
            byte_count = len(tx_data)

            if byte_count == 0 or byte_count > 255:
                self.append_log("❌ 数据长度必须在1-255字节", "ERROR")
                return

            # 构造payload: [byte_count][tx_data...]
            payload = struct.pack("B", byte_count) + tx_data

            self.append_log(
                f"📤 SPI传输: 发送 {byte_count} 字节 → {tx_data.hex(' ').upper()}",
                "SEND",
            )

            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_SPI_TRANSFER, payload)
            else:
                self.append_log("❌ CDC串口未连接", "ERROR")

        except ValueError as e:
            self.append_log(f"❌ 数据格式错误: {e}", "ERROR")

    def flash_read_id(self):
        """读取Flash ID (快捷命令: 发送0x9F)"""
        self.append_log("📤 读取Flash ID (命令: 0x9F)", "SEND")

        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_SPI_FLASH_ID, b"")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def flash_read_status(self):
        """读取Flash状态寄存器 (命令: 0x05)"""
        self.append_log("📤 读取Flash状态寄存器 (命令: 0x05)", "SEND")

        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_SPI_FLASH_READ_STATUS, b"")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def flash_erase_sector(self):
        """扇区擦除 (4KB)"""
        try:
            # 解析地址
            addr_str = self.flash_addr_input.text().strip()
            addr = int(addr_str, 16)
            if addr > 0xFFFFFF:
                raise ValueError("地址必须在0x000000-0xFFFFFF范围")

            # 地址对齐到4KB边界
            addr_aligned = (addr // 0x1000) * 0x1000

            # 确认对话框
            from PySide6.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self,
                "确认擦除",
                f"确定要擦除扇区吗?\n\n"
                f"地址: 0x{addr:06X}\n"
                f"对齐地址: 0x{addr_aligned:06X}\n"
                f"扇区大小: 4KB\n\n"
                f"⚠️ 此操作不可恢复！",
                QMessageBox.Yes | QMessageBox.No,
            )

            if reply != QMessageBox.Yes:
                self.append_log("  ✗ 用户取消擦除操作", "INFO")
                return

            # 构造payload: [addr(24)]
            payload = struct.pack(">I", addr_aligned)[1:]

            self.append_log(
                f"📤 Flash扇区擦除: 地址=0x{addr_aligned:06X} (4KB)", "SEND"
            )
            self.append_log("  ⏳ 擦除需要约100-400ms，请稍候...", "INFO")

            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_SPI_FLASH_ERASE_SECTOR, payload)
            else:
                self.append_log("❌ CDC串口未连接", "ERROR")

        except ValueError as e:
            self.append_log(f"❌ 参数错误: {e}", "ERROR")

    def flash_erase_chip(self):
        """全片擦除"""
        from PySide6.QtWidgets import QMessageBox

        # 确认对话框
        reply = QMessageBox.warning(
            self,
            "⚠️ 危险操作",
            "确定要擦除整个Flash芯片吗?\n\n"
            "芯片: W25Q128 (16MB)\n"
            "时间: 约20-80秒\n\n"
            "⚠️ 所有数据将被清除，此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            self.append_log("  ✗ 用户取消全片擦除", "INFO")
            return

        self.append_log("📤 Flash全片擦除 (命令: 0xC7 或 0x60)", "SEND")
        self.append_log("  ⚠️ 全片擦除需要20-80秒，请耐心等待...", "INFO")
        self.append_log("  💡 可通过'读状态'按钮查看擦除进度 (BUSY位)", "INFO")

        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_SPI_FLASH_ERASE_CHIP, b"")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def flash_read(self):
        """Flash读取"""
        try:
            # 解析地址
            addr_str = self.flash_addr_input.text().strip()
            addr = int(addr_str, 16)
            if addr > 0xFFFFFF:
                raise ValueError("地址必须在0x000000-0xFFFFFF范围")

            byte_count = self.flash_read_len_spin.value()
            self.last_read_count = byte_count  # 保存读取字节数用于应答处理

            # ✅ 通知serial_manager即将有Flash读取数据（用于UART面板过滤）
            if self.serial_manager:
                self.serial_manager.pending_spi_read_length = byte_count

            # 构造payload: [addr(24)][byte_count]
            # 地址按大端序（MSB first）
            payload = struct.pack(">I", addr)[1:] + struct.pack("B", byte_count)

            self.append_log(
                f"📤 Flash读取: 地址=0x{addr:06X} 字节数={byte_count}", "SEND"
            )

            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_SPI_FLASH_READ, payload)
            else:
                self.append_log("❌ CDC串口未连接", "ERROR")

        except ValueError as e:
            self.append_log(f"❌ 参数错误: {e}", "ERROR")

    def flash_write(self):
        """Flash写入"""
        try:
            # 解析地址
            addr_str = self.flash_addr_input.text().strip()
            addr = int(addr_str, 16)
            if addr > 0xFFFFFF:
                raise ValueError("地址必须在0x000000-0xFFFFFF范围")

            # 解析写入数据
            data_str = self.flash_write_input.text().strip()
            if not data_str:
                self.append_log("❌ 请输入要写入的数据", "ERROR")
                return

            write_data = bytes.fromhex(data_str.replace(" ", ""))
            byte_count = len(write_data)

            if byte_count == 0 or byte_count > 256:
                raise ValueError("数据长度必须在1-256字节")

            # 构造payload: [addr(24)][data...]
            # 地址按大端序（MSB first）
            payload = struct.pack(">I", addr)[1:] + write_data

            self.append_log(
                f"📤 Flash写入: 地址=0x{addr:06X} 数据={write_data.hex(' ').upper()}",
                "SEND",
            )

            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_SPI_FLASH_WRITE, payload)
            else:
                self.append_log("❌ CDC串口未连接", "ERROR")

        except ValueError as e:
            self.append_log(f"❌ 参数错误: {e}", "ERROR")

    # ========== 辅助方法 ==========

    def toggle_timestamp(self):
        """切换时间戳显示"""
        self.show_timestamp = self.timestamp_checkbox.isChecked()

    def append_log(self, message, msg_type="INFO"):
        """追加日志"""
        timestamp = ""
        if self.show_timestamp:
            timestamp = f"[{datetime.now().strftime('%H:%M:%S')}] "

        color_map = {
            "SEND": "#2196F3",
            "RECV": "#4CAF50",
            "INFO": "#9E9E9E",
            "ERROR": "#F44336",
            "SUCCESS": "#4CAF50",
            "WARNING": "#FF9800",
        }
        color = color_map.get(msg_type, "#000000")

        formatted = f'<span style="color:{color}">{timestamp}{message}</span>'
        self.log_text.append(formatted)

    def clear_log(self):
        """清除日志"""
        self.log_text.clear()

    def handle_rx_response(self, data):
        """
        处理CH340混合数据流 - 智能分离应答帧和Flash数据流

        架构说明：
        - FPGA发送：应答帧(AA 55...) + Flash数据流(裸数据)
        - 应答帧：由serial_manager统一处理，显示在调试日志
        - Flash数据：在此处提取并显示在Flash面板

        参考：UART设备面板的handle_uart_data()方法
        """
        if not isinstance(data, bytes):
            return

        # 将数据追加到缓冲区
        self.data_buffer.extend(data)

        # 处理缓冲区数据
        while len(self.data_buffer) > 0:
            # 查找应答帧标记 AA 55
            aa_idx = -1
            for i in range(len(self.data_buffer)):
                if self.data_buffer[i] == 0xAA:
                    aa_idx = i
                    break

            if aa_idx == -1:
                # 没有AA标记，全部是Flash数据
                if self.waiting_flash_data:
                    flash_data = bytes(self.data_buffer)
                    self.accumulate_flash_data(flash_data)
                else:
                    # 丢弃非预期数据
                    pass
                self.data_buffer.clear()
                break

            # 找到AA标记
            if aa_idx > 0:
                # AA前面有数据，当作Flash数据
                if self.waiting_flash_data:
                    flash_data = bytes(self.data_buffer[:aa_idx])
                    self.accumulate_flash_data(flash_data)
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
                mod_id = frame[2]
                func_id = frame[3]
                status = frame[4]

                # 处理应答帧（只处理SPI命令）
                self.process_response_frame(func_id, status)
            else:
                # AA后面不是55，只是普通数据中的AA字节
                if self.waiting_flash_data:
                    flash_data = bytes([self.data_buffer[0]])
                    self.accumulate_flash_data(flash_data)
                self.data_buffer = self.data_buffer[1:]

    def process_response_frame(self, func_id, status):
        """处理SPI应答帧"""
        # 只处理SPI相关的命令应答
        if func_id not in [
            CMD_SPI_CONFIG,
            CMD_SPI_TRANSFER,
            CMD_SPI_FLASH_ID,
            CMD_SPI_FLASH_READ,
            CMD_SPI_FLASH_WRITE,
            CMD_SPI_FLASH_ERASE_SECTOR,
            CMD_SPI_FLASH_ERASE_CHIP,
            CMD_SPI_FLASH_READ_STATUS,
        ]:
            return

        status_msg = {
            0x00: "✓ 成功",
            0x01: "✗ 校验错误",
            0x02: "✗ 无效命令",
            0x03: "✗ 参数错误",
        }.get(status, f"✗ 状态(0x{status:02X})")

        # 根据命令类型显示不同的交互数据
        if func_id == CMD_SPI_CONFIG:
            # SPI配置应答
            self.append_log(
                f"📥 配置应答: {status_msg}", "RECV" if status == 0x00 else "ERROR"
            )
            self.waiting_flash_data = None

        elif func_id == CMD_SPI_FLASH_ID:
            # Flash读ID应答 + 3字节数据流
            if status == 0x00:
                self.append_log(f"📥 Flash ID读取成功", "RECV")
                # 标记等待3字节Flash ID数据
                self.waiting_flash_data = "id"
                self.waiting_data_length = 3
                self.flash_data_accumulator.clear()
            else:
                self.append_log(f"📥 Flash ID读取失败: {status_msg}", "ERROR")
                self.waiting_flash_data = None

        elif func_id == CMD_SPI_TRANSFER:
            # SPI传输应答（不返回数据）
            if status == 0x00:
                self.append_log(f"📥 传输完成 (数据请用逻辑分析仪查看)", "RECV")
            else:
                self.append_log(f"📥 传输失败: {status_msg}", "ERROR")
            self.waiting_flash_data = None

        elif func_id == CMD_SPI_FLASH_READ:
            # Flash读取应答 + N字节数据流
            if status == 0x00:
                self.append_log(f"📥 Flash读取成功", "RECV")
                # 标记等待N字节Flash数据
                self.waiting_flash_data = "read"
                self.waiting_data_length = self.last_read_count
                self.flash_data_accumulator.clear()
            else:
                self.append_log(f"📥 Flash读取失败: {status_msg}", "ERROR")
                self.waiting_flash_data = None

        elif func_id == CMD_SPI_FLASH_WRITE:
            # Flash写入应答
            if status == 0x00:
                self.append_log(f"📥 写入完成", "RECV")
                self.append_log(f"  💡 提示: 可通过'读取'按钮验证写入结果", "INFO")
            else:
                self.append_log(f"📥 写入失败: {status_msg}", "ERROR")
            self.waiting_flash_data = None

        elif func_id == CMD_SPI_FLASH_ERASE_SECTOR:
            # 扇区擦除应答
            if status == 0x00:
                self.append_log(f"📥 扇区擦除完成", "RECV")
                self.append_log(f"  ✓ 4KB扇区已擦除 (所有位变为0xFF)", "INFO")
            else:
                self.append_log(f"📥 扇区擦除失败: {status_msg}", "ERROR")
            self.waiting_flash_data = None

        elif func_id == CMD_SPI_FLASH_ERASE_CHIP:
            # 全片擦除应答
            if status == 0x00:
                self.append_log(f"📥 全片擦除完成", "RECV")
                self.append_log(f"  ✓ 整个Flash已擦除 (16MB = 0xFF)", "SUCCESS")
            else:
                self.append_log(f"📥 全片擦除失败: {status_msg}", "ERROR")
            self.waiting_flash_data = None

        elif func_id == CMD_SPI_FLASH_READ_STATUS:
            # Flash读状态应答 + 1字节数据流
            if status == 0x00:
                self.append_log(f"📥 状态寄存器读取成功", "RECV")
                # 标记等待1字节状态寄存器数据
                self.waiting_flash_data = "status"
                self.waiting_data_length = 1
                self.flash_data_accumulator.clear()
            else:
                self.append_log(f"📥 状态寄存器读取失败: {status_msg}", "ERROR")
                self.waiting_flash_data = None

    def accumulate_flash_data(self, data):
        """累积Flash数据流，批量显示以减少刷新"""
        if not data or not self.waiting_flash_data:
            return

        # 累积数据
        self.flash_data_accumulator.extend(data)

        # 检查是否已收集到足够的数据
        if len(self.flash_data_accumulator) >= self.waiting_data_length:
            # 立即显示（数据已完整）
            self.flush_flash_data()
        else:
            # 重启定时器（100ms内没有新数据就显示）
            self.flash_display_timer.stop()
            self.flash_display_timer.start(100)

    def flush_flash_data(self):
        """刷新显示累积的Flash数据"""
        if len(self.flash_data_accumulator) == 0 or not self.waiting_flash_data:
            return

        data = bytes(self.flash_data_accumulator)
        data_type = self.waiting_flash_data

        try:
            if data_type == "id":
                # Flash ID数据（3字节）
                if len(data) >= 3:
                    manufacturer = data[0]
                    mem_type = data[1]
                    capacity = data[2]

                    self.append_log(f"  📋 原始数据: {data[:3].hex().upper()}", "INFO")
                    self.append_log(
                        f"  🏭 Manufacturer ID: 0x{manufacturer:02X}", "INFO"
                    )
                    self.append_log(f"  📦 Memory Type: 0x{mem_type:02X}", "INFO")
                    self.append_log(f"  💾 Capacity: 0x{capacity:02X}", "INFO")

                    # 芯片厂商识别
                    flash_types = {
                        0xEF: "Winbond",
                        0xC8: "GigaDevice",
                        0xC2: "Macronix",
                        0x20: "Micron",
                        0x01: "Spansion",
                    }
                    vendor = flash_types.get(manufacturer, "Unknown")
                    self.append_log(f"  🏢 芯片厂商: {vendor}", "INFO")
                else:
                    self.append_log(
                        f"  ⚠️ Flash ID数据不完整: 收到{len(data)}字节，期望3字节",
                        "WARNING",
                    )

            elif data_type == "read":
                # Flash读取数据（N字节）
                expected = self.waiting_data_length
                if len(data) == expected:
                    # 显示数据（HEX格式）
                    hex_str = " ".join([f"{b:02X}" for b in data])
                    self.append_log(f"  � 读取数据({len(data)}字节): {hex_str}", "INFO")

                    # 如果数据可打印，尝试显示ASCII
                    try:
                        ascii_str = data.decode("ascii", errors="ignore")
                        printable_chars = [
                            c for c in ascii_str if c.isprintable() or c in "\n\r\t"
                        ]
                        if len(printable_chars) > len(data) * 0.7:  # 70%以上可打印
                            self.append_log(f"  � ASCII: {ascii_str}", "INFO")
                    except:
                        pass
                else:
                    self.append_log(
                        f"  ⚠️ 数据不完整: 期望{expected}字节, 收到{len(data)}字节",
                        "WARNING",
                    )
                    if len(data) > 0:
                        hex_str = " ".join([f"{b:02X}" for b in data])
                        self.append_log(f"  � 部分数据: {hex_str}", "INFO")

            elif data_type == "status":
                # Flash状态寄存器（1字节）
                if len(data) >= 1:
                    status_reg = data[0]
                    self.append_log(
                        f"  📊 状态寄存器: 0x{status_reg:02X} (0b{status_reg:08b})",
                        "INFO",
                    )

                    # 解析状态位
                    busy = (status_reg >> 0) & 0x01
                    wel = (status_reg >> 1) & 0x01
                    bp = (status_reg >> 2) & 0x0F  # BP0-BP3

                    self.append_log(
                        f"  🔄 BUSY: {busy} {'(忙碌中⏳)' if busy else '(空闲✓)'}",
                        "INFO",
                    )
                    self.append_log(
                        f"  🔓 WEL: {wel} {'(写使能✓)' if wel else '(写禁止)'}",
                        "INFO",
                    )
                    self.append_log(f"  🔒 BP[3:0]: 0b{bp:04b} (块保护)", "INFO")
                else:
                    self.append_log(
                        f"  ⚠️ 状态寄存器数据不完整: 收到{len(data)}字节", "WARNING"
                    )

        except Exception as e:
            self.append_log(f"  ❌ 数据处理异常: {e}", "ERROR")
        finally:
            # 清空累积缓冲区和等待标记
            self.flash_data_accumulator.clear()
            self.waiting_flash_data = None
            self.waiting_data_length = 0


# 为了兼容性，保留旧的类名
SPIDevicePanel = SPIDevicePanelSimple
