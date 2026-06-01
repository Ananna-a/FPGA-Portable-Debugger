#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逻辑分析仪与序列发生器模块
功能：
  - 8通道序列发生器：并行/串行模式输出（SEQ_OUT[7:0]）
  - 8通道逻辑分析仪：采样显示 + 协议解码（LOGIC_OUT[7:0]）
  - 协议解码：I2C/SPI/UART/CAN/1-Wire（叠加显示，参考Logic软件）

设计理念：
  - 参考Saleae Logic软件，协议解码与波形显示融合在同一界面
  - 时序图上叠加气泡标注，显示解码后的数据
  - 支持协议过滤、搜索、导出功能
"""

import sys
import struct
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QComboBox,
    QTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QTabWidget,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QLineEdit,
    QHeaderView,
    QScrollArea,
    QGridLayout,
    QInputDialog,
)
from PySide6.QtCore import Qt, Signal, QTimer, QTime
from PySide6.QtGui import QFont, QColor
import pyqtgraph as pg
from core.serial_protocol import (
    cmd_seq_config_channel as cmd_config_channel,
    cmd_seq_write_data as cmd_write_data,
    cmd_seq_enable_channels as cmd_enable_channels,
    cmd_seq_reset_all as cmd_reset_all,
    calc_seq_freq_word as calc_freq_word,
)


# ============================================================================
# CDC命令定义 - 与FPGA端统一 (logic_analyzer_top.v)
# ============================================================================

# 序列发生器命令 (0x30-0x34)
CMD_SEQ_PARALLEL_MODE = 0x30  # 并行模式配置：[LEN][DATA...]
CMD_SEQ_SERIAL_MODE = 0x31  # 串行模式配置：[CH_MASK][LEN][DATA...]
CMD_SEQ_FREQ_CONTROL = (
    0x32  # 频率控制：[FREQ_WORD_L][FREQ_WORD_H][FREQ_WORD_U][FREQ_WORD_T]
)
CMD_SEQ_START = 0x33  # 启动输出
CMD_SEQ_STOP = 0x34  # 停止输出

# 逻辑分析仪命令 (0x40-0x44) - 预留框架
CMD_LA_CONFIG = 0x40  # 配置采样参数：[采样率][触发模式]
CMD_LA_ARM = 0x41  # 预备采集（等待触发）
CMD_LA_FORCE_TRIGGER = 0x42  # 强制触发
CMD_LA_READ_DATA = 0x43  # 读取采样数据
CMD_LA_STOP = 0x44  # 停止采集

# 设备中心命令 (0x50-0x8F) - 预留框架
CMD_I2C_WRITE = 0x50  # I2C写：[ADDR][LEN][DATA...]
CMD_I2C_READ = 0x51  # I2C读：[ADDR][LEN]
CMD_I2C_SCAN = 0x52  # I2C扫描总线

CMD_SPI_WRITE = 0x60  # SPI写：[LEN][DATA...]
CMD_SPI_READ = 0x61  # SPI读：[LEN]
CMD_SPI_TRANSFER = 0x62  # SPI全双工：[LEN][DATA...]

CMD_UART_SEND = 0x70  # UART发送：[LEN][DATA...]
CMD_UART_CONFIG = 0x71  # UART配置：[BAUD_L][BAUD_H][BAUD_U][BAUD_T]

CMD_PWM_CONFIG = 0x80  # PWM配置：[CH_MASK][FREQ_L][FREQ_H][DUTY]
CMD_PWM_START = 0x81  # PWM启动：[CH_MASK]
CMD_PWM_STOP = 0x82  # PWM停止：[CH_MASK]

CMD_CAN_SEND = 0x83  # CAN发送：[ID_L][ID_H][LEN][DATA...]
CMD_CAN_CONFIG = 0x84  # CAN配置：[BAUD_L][BAUD_H]

CMD_ONEWIRE_RESET = 0x85  # 单总线复位
CMD_ONEWIRE_WRITE = 0x86  # 单总线写：[DATA]
CMD_ONEWIRE_READ = 0x87  # 单总线读


# ============================================================================
# CDC命令发送辅助函数
# ============================================================================


def send_cdc_command(serial_manager, cmd, payload=b""):
    """
    发送CDC命令到FPGA
    直接使用SerialManager的send_command方法

    Args:
        serial_manager: SerialManager实例
        cmd: 命令字节 (0x30-0x8F)
        payload: 有效负载数据

    Returns:
        bool: 发送是否成功
    """
    if serial_manager is None or not serial_manager.is_connected():
        return False

    try:
        return serial_manager.send_command(cmd, payload)
    except Exception as e:
        print(f"❌ 发送CDC命令失败 (0x{cmd:02X}): {e}")
        return False


# ============================================================================
# 逻辑分析仪界面类
# ============================================================================


class LogicAnalyzerTab(QWidget):
    """逻辑分析仪/设备中心模块界面"""

    def __init__(self, serial_manager=None):
        super().__init__()
        self.serial_manager = serial_manager  # 使用主程序提供的串口管理器
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # 子标签页：逻辑分析 | 序列输出 | PWM控制 | 设备中心
        sub_tabs = QTabWidget()

        # Tab 1: 逻辑分析仪（参考Logic软件：波形 + 解码融合）
        logic_analyzer_tab = self.create_logic_analyzer_page()
        sub_tabs.addTab(logic_analyzer_tab, "🔍 逻辑分析仪")

        # Tab 2: 序列发生器（并行/串行模式）
        sequence_tab = self.create_sequence_output_page()
        sub_tabs.addTab(sequence_tab, "📊 序列发生器")

        # Tab 3: PWM控制器（8路独立PWM）
        pwm_tab = self.create_pwm_controller_page()
        sub_tabs.addTab(pwm_tab, "⚡ PWM控制器")

        # Tab 4: 设备中心（I2C/SPI/UART/电机测试）
        device_tab = self.create_device_center_page()
        sub_tabs.addTab(device_tab, "🔌 设备中心")

        main_layout.addWidget(sub_tabs)

    def create_sequence_output_page(self):
        """创建多通道序列输出页面（8通道，并行/串行模式） - 🎨 紧凑风格"""
        page = QWidget()
        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # 模式选择 - 🎨 限制高度
        mode_group = QGroupBox("输出模式")
        mode_group.setMaximumHeight(50)
        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(8, 4, 8, 4)

        self.mode_button_group = QButtonGroup()
        self.parallel_radio = QRadioButton("并行模式 (8通道→字节序列)")
        self.serial_radio = QRadioButton("串行模式 (每通道独立序列)")
        self.parallel_radio.setChecked(True)

        self.mode_button_group.addButton(self.parallel_radio, 0)
        self.mode_button_group.addButton(self.serial_radio, 1)

        mode_layout.addWidget(self.parallel_radio)
        mode_layout.addWidget(self.serial_radio)
        mode_layout.addStretch()

        # 模式切换信号
        self.parallel_radio.toggled.connect(self.on_mode_changed)

        mode_group.setLayout(mode_layout)
        main_layout.addWidget(mode_group)

        # 创建并行模式和串行模式的容器（切换显示）
        self.parallel_widget = self.create_parallel_mode_widget()
        self.serial_widget = self.create_serial_mode_widget()

        main_layout.addWidget(self.parallel_widget)
        main_layout.addWidget(self.serial_widget)
        self.serial_widget.setVisible(False)  # 默认隐藏串行模式

        # 底部控制按钮 - 🎨 参考PWM风格
        control_layout = QHBoxLayout()
        control_layout.setSpacing(8)

        # 左侧快捷操作（仅在串行模式显示）
        self.serial_enable_all_btn = QPushButton("全部使能")
        self.serial_enable_all_btn.setMaximumWidth(100)
        self.serial_enable_all_btn.clicked.connect(self.enable_all_serial_channels)
        self.serial_enable_all_btn.setVisible(False)  # 默认隐藏
        control_layout.addWidget(self.serial_enable_all_btn)

        self.serial_disable_all_btn = QPushButton("全部禁用")
        self.serial_disable_all_btn.setMaximumWidth(100)
        self.serial_disable_all_btn.clicked.connect(self.disable_all_serial_channels)
        self.serial_disable_all_btn.setVisible(False)  # 默认隐藏
        control_layout.addWidget(self.serial_disable_all_btn)

        control_layout.addStretch()

        self.apply_sequence_btn = QPushButton("应用配置并启动")
        self.apply_sequence_btn.setMaximumWidth(140)  # 🎨 限制按钮宽度
        self.apply_sequence_btn.clicked.connect(self.apply_sequence_config)
        control_layout.addWidget(self.apply_sequence_btn)

        self.stop_output_btn = QPushButton("停止输出")
        self.stop_output_btn.setMaximumWidth(100)
        self.stop_output_btn.clicked.connect(self.stop_sequence_output)
        control_layout.addWidget(self.stop_output_btn)

        main_layout.addLayout(control_layout)

        # 日志区 - 🎨 紧凑布局
        log_group = QGroupBox("输出日志")
        log_group.setMaximumHeight(110)  # 🎨 限制高度
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)
        log_layout.setSpacing(3)

        self.sequence_log = QTextEdit()
        self.sequence_log.setReadOnly(True)
        self.sequence_log.setMaximumHeight(80)  # 🎨 减小高度
        self.sequence_log.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.sequence_log)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        return page

    def create_parallel_mode_widget(self):
        """创建并行模式配置界面 - 🎨 紧凑风格"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 全局频率设置 - 🎨 限制高度
        freq_group = QGroupBox("全局输出频率配置")
        freq_group.setMaximumHeight(60)
        freq_layout = QHBoxLayout()
        freq_layout.setContentsMargins(8, 6, 8, 6)
        freq_layout.setSpacing(10)

        freq_layout.addWidget(QLabel("输出频率:"))
        self.parallel_freq_spin = QDoubleSpinBox()
        self.parallel_freq_spin.setRange(0.1, 10000000)  # 0.1Hz - 10MHz
        self.parallel_freq_spin.setValue(1000)  # 默认1kHz
        self.parallel_freq_spin.setDecimals(1)
        self.parallel_freq_spin.setSuffix(" Hz")
        self.parallel_freq_spin.setMinimumWidth(120)
        freq_layout.addWidget(self.parallel_freq_spin)

        freq_layout.addWidget(QLabel("周期:"))
        self.parallel_period_label = QLabel("1.000 ms")
        self.parallel_period_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        self.parallel_period_label.setMinimumWidth(90)
        freq_layout.addWidget(self.parallel_period_label)

        # 连接信号更新周期显示
        self.parallel_freq_spin.valueChanged.connect(self.update_parallel_period)

        freq_layout.addStretch()
        freq_group.setLayout(freq_layout)
        layout.addWidget(freq_group)

        # 序列编辑器 - 🎨 限制表格高度
        sequence_group = QGroupBox("字节序列编辑器 (每行一个字节，二进制/十六进制)")
        sequence_layout = QVBoxLayout()
        sequence_layout.setContentsMargins(8, 8, 8, 8)
        sequence_layout.setSpacing(6)

        # 序列表格
        self.parallel_table = QTableWidget()
        self.parallel_table.setColumnCount(4)
        self.parallel_table.setHorizontalHeaderLabels(
            ["步骤", "字节值(二进制)", "字节值(十六进制)", "说明"]
        )
        self.parallel_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.parallel_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.parallel_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch
        )
        self.parallel_table.setRowCount(8)  # 默认8行
        self.parallel_table.setMaximumHeight(180)  # 🎨 稍微增加表格高度，显示更多行

        # 初始化默认序列
        for i in range(8):
            self.parallel_table.setItem(i, 0, QTableWidgetItem(f"{i+1}"))
            self.parallel_table.item(i, 0).setTextAlignment(Qt.AlignCenter)  # 居中显示
            self.parallel_table.setItem(i, 1, QTableWidgetItem("00000000"))
            self.parallel_table.setItem(i, 2, QTableWidgetItem("0x00"))
            self.parallel_table.setItem(i, 3, QTableWidgetItem(""))

        # 连接信号：二进制编辑后自动更新十六进制
        self.parallel_table.itemChanged.connect(self.on_parallel_binary_changed)

        sequence_layout.addWidget(self.parallel_table)

        # 序列操作按钮 - 🎨 紧凑排列
        seq_btn_layout = QHBoxLayout()
        seq_btn_layout.setSpacing(8)

        add_row_btn = QPushButton("➕ 添加行")
        add_row_btn.setMaximumWidth(85)
        add_row_btn.clicked.connect(self.add_parallel_row)
        seq_btn_layout.addWidget(add_row_btn)

        del_row_btn = QPushButton("➖ 删除行")
        del_row_btn.setMaximumWidth(85)
        del_row_btn.clicked.connect(self.delete_parallel_row)
        seq_btn_layout.addWidget(del_row_btn)

        clear_btn = QPushButton("🗑️ 清空")
        clear_btn.setMaximumWidth(80)
        clear_btn.clicked.connect(self.clear_parallel_sequence)
        seq_btn_layout.addWidget(clear_btn)

        seq_btn_layout.addStretch()

        # 预设按钮
        preset_label = QLabel("快速预设:")
        seq_btn_layout.addWidget(preset_label)

        preset_btn1 = QPushButton("计数器(0-15)")
        preset_btn1.setMaximumWidth(105)
        preset_btn1.clicked.connect(lambda: self.load_parallel_preset("counter"))
        seq_btn_layout.addWidget(preset_btn1)

        preset_btn2 = QPushButton("交替(0xAA/0x55)")
        preset_btn2.setMaximumWidth(125)
        preset_btn2.clicked.connect(lambda: self.load_parallel_preset("alternate"))
        seq_btn_layout.addWidget(preset_btn2)

        preset_btn3 = QPushButton("自定义...")
        preset_btn3.setMaximumWidth(80)
        preset_btn3.clicked.connect(lambda: self.load_parallel_preset("custom"))
        seq_btn_layout.addWidget(preset_btn3)

        sequence_layout.addLayout(seq_btn_layout)

        sequence_group.setLayout(sequence_layout)
        layout.addWidget(sequence_group)

        return widget

    def create_serial_mode_widget(self):
        """创建串行模式配置界面"""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(6)

        # 滚动区域（容纳8个通道）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(4)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        # 创建8个通道的配置
        self.serial_channels = []
        for ch in range(8):
            channel_widget = self.create_serial_channel_widget(ch)
            self.serial_channels.append(channel_widget)
            scroll_layout.addWidget(channel_widget)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

        return widget

    def create_serial_channel_widget(self, channel_id):
        """创建单个串行通道配置控件"""
        group = QGroupBox(f"通道 {channel_id} (CH{channel_id})")
        layout = QGridLayout()

        # 使能开关
        enable_check = QCheckBox("使能")
        enable_check.setChecked(True)
        layout.addWidget(enable_check, 0, 0)

        # 序列输入
        layout.addWidget(QLabel("比特序列:"), 0, 1)
        sequence_input = QLineEdit()
        sequence_input.setPlaceholderText("例如: 00101101 (支持任意长度)")
        sequence_input.setText("10101010")  # 默认序列
        sequence_input.setMinimumWidth(200)
        layout.addWidget(sequence_input, 0, 2, 1, 2)

        # 位频率设置（改为主要输入参数，32位DDS，支持超宽范围）
        layout.addWidget(QLabel("位频率:"), 1, 1)
        bit_freq_spin = QDoubleSpinBox()
        # 32位DDS: 0.01Hz-2MHz（限制上限保证时序）
        bit_freq_spin.setRange(0.01, 2000000)
        bit_freq_spin.setValue(1000)  # 默认1kHz
        bit_freq_spin.setDecimals(2)
        bit_freq_spin.setSingleStep(100)
        bit_freq_spin.setSuffix(" Hz")
        bit_freq_spin.setMinimumWidth(120)
        bit_freq_spin.setToolTip(
            "位频率范围: 0.01Hz ~ 2MHz\n"
            "对应每位时间: 0.5μs ~ 100s\n"
            "（32位DDS高精度，三级流水线）"
        )
        layout.addWidget(bit_freq_spin, 1, 2)

        # 循环周期显示（右侧，周期+频率组合）
        layout.addWidget(QLabel("循环周期:"), 1, 3)
        period_label = QLabel("8.000 ms  (125 Hz)")
        period_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        period_label.setMinimumWidth(150)
        layout.addWidget(period_label, 1, 4)

        # 每位时间显示（第2行左侧，显示计算结果）
        layout.addWidget(QLabel("每位时间:"), 2, 1)
        bit_time_label = QLabel("1.000 ms")
        bit_time_label.setStyleSheet("font-weight: bold; color: #FF9800;")
        bit_time_label.setMinimumWidth(120)
        layout.addWidget(bit_time_label, 2, 2)

        # 连接信号更新显示
        def update_display():
            sequence = sequence_input.text().replace(" ", "")
            bit_count = len(sequence) if sequence else 1
            bit_freq = bit_freq_spin.value()  # 用户输入的位频率

            # 每位时间 = 1 / 位频率
            bit_time_ms = 1000.0 / bit_freq if bit_freq > 0 else 1.0

            # 循环周期 = 每位时间 × 序列长度
            total_period = bit_time_ms * bit_count
            # 循环频率 = 1 / 循环周期
            cycle_freq = 1000.0 / total_period if total_period > 0 else 0

            # 显示每位时间（计算结果）
            if bit_time_ms >= 1000:
                bit_time_label.setText(f"{bit_time_ms/1000:.3f} s")
            elif bit_time_ms >= 1:
                bit_time_label.setText(f"{bit_time_ms:.3f} ms")
            else:
                bit_time_label.setText(f"{bit_time_ms*1000:.3f} μs")

            # 显示循环周期和频率（组合在一个label中）
            if total_period >= 1000:
                period_str = f"{total_period/1000:.3f} s"
            elif total_period >= 1:
                period_str = f"{total_period:.3f} ms"
            else:
                period_str = f"{total_period*1000:.3f} μs"

            if cycle_freq >= 1000:
                freq_str = f"({cycle_freq/1000:.2f} kHz)"
            elif cycle_freq >= 1:
                freq_str = f"({cycle_freq:.2f} Hz)"
            else:
                freq_str = f"({cycle_freq*1000:.2f} mHz)"

            period_label.setText(f"{period_str}  {freq_str}")

        sequence_input.textChanged.connect(update_display)
        bit_freq_spin.valueChanged.connect(update_display)

        # 初始显示
        update_display()

        # 快捷预设（序列预设）
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("预设:"))

        preset1 = QPushButton("时钟")
        preset1.setMaximumWidth(60)
        preset1.clicked.connect(lambda: sequence_input.setText("10"))
        preset_layout.addWidget(preset1)

        preset2 = QPushButton("PWM")
        preset2.setMaximumWidth(60)
        preset2.clicked.connect(lambda: sequence_input.setText("11110000"))
        preset_layout.addWidget(preset2)

        preset3 = QPushButton("自定义")
        preset3.setMaximumWidth(60)
        preset3.clicked.connect(lambda: self.custom_serial_sequence(channel_id))
        preset_layout.addWidget(preset3)

        preset_layout.addStretch()
        layout.addLayout(preset_layout, 3, 1, 1, 2)  # 放在第3行左侧

        # 位频率快捷按钮（放在第3行右侧）
        freq_layout = QHBoxLayout()
        freq_layout.addWidget(QLabel("频率:"))

        freq_presets = [
            ("1MHz", 1000000),
            ("100kHz", 100000),
            ("1kHz", 1000),
            ("1Hz", 1.0),
        ]
        for name, freq in freq_presets:
            btn = QPushButton(name)
            btn.setMinimumWidth(60)
            btn.setMaximumWidth(75)
            btn.clicked.connect(lambda checked, f=freq: bit_freq_spin.setValue(f))
            freq_layout.addWidget(btn)

        freq_layout.addStretch()
        layout.addLayout(freq_layout, 3, 3, 1, 2)  # 放在第3行右侧

        group.setLayout(layout)

        # 保存控件引用
        group.enable_check = enable_check
        group.sequence_input = sequence_input
        group.bit_freq_spin = bit_freq_spin  # 改为频率输入
        group.period_label = period_label
        group.bit_time_label = bit_time_label  # 改为时间显示

        return group

    # ============ 串行模式快捷操作函数 ============

    def enable_all_serial_channels(self):
        """全部使能串行通道"""
        for channel in self.serial_channels:
            channel.enable_check.setChecked(True)
        self.log_sequence("✓ 已全部使能所有串行通道")

    def disable_all_serial_channels(self):
        """全部禁用串行通道"""
        for channel in self.serial_channels:
            channel.enable_check.setChecked(False)
        self.log_sequence("✗ 已禁用所有串行通道")

    # ============ 并行模式相关函数 ============

    def on_mode_changed(self, checked):
        """模式切换"""
        if checked:  # 切换到并行模式
            self.parallel_widget.setVisible(True)
            self.serial_widget.setVisible(False)
            # 隐藏串行模式的全部使能/禁用按钮
            self.serial_enable_all_btn.setVisible(False)
            self.serial_disable_all_btn.setVisible(False)
            self.log_sequence("切换到并行模式")
        else:  # 切换到串行模式
            self.parallel_widget.setVisible(False)
            self.serial_widget.setVisible(True)
            # 显示串行模式的全部使能/禁用按钮
            self.serial_enable_all_btn.setVisible(True)
            self.serial_disable_all_btn.setVisible(True)
            self.log_sequence("切换到串行模式")

    def update_parallel_period(self, freq):
        """更新并行模式周期显示"""
        if freq > 0:
            period_ms = 1000.0 / freq
            if period_ms >= 1:
                self.parallel_period_label.setText(f"{period_ms:.3f} ms")
            else:
                self.parallel_period_label.setText(f"{period_ms * 1000:.3f} μs")

    def on_parallel_binary_changed(self, item):
        """二进制编辑后自动更新十六进制"""
        if item.column() == 1:  # 二进制列
            binary_str = item.text().replace(" ", "")
            # 验证是否为有效二进制（8位）
            if len(binary_str) == 8 and all(c in "01" for c in binary_str):
                # 转换为十六进制
                hex_value = int(binary_str, 2)
                row = item.row()
                self.parallel_table.blockSignals(True)
                self.parallel_table.setItem(
                    row, 2, QTableWidgetItem(f"0x{hex_value:02X}")
                )
                self.parallel_table.blockSignals(False)
                # 清除错误标记
                item.setBackground(QColor(255, 255, 255))
                item.setToolTip("")
            elif binary_str:  # 非空但格式错误
                item.setBackground(QColor(255, 200, 200))
                item.setToolTip("请输入8位二进制数 (例如: 11000101)")

    def add_parallel_row(self):
        """添加并行序列行"""
        row_count = self.parallel_table.rowCount()
        self.parallel_table.insertRow(row_count)
        self.parallel_table.setItem(row_count, 0, QTableWidgetItem(f"{row_count + 1}"))
        self.parallel_table.setItem(row_count, 1, QTableWidgetItem("00000000"))
        self.parallel_table.setItem(row_count, 2, QTableWidgetItem("0x00"))
        self.parallel_table.setItem(row_count, 3, QTableWidgetItem(""))
        self.log_sequence(f"添加步骤 {row_count + 1}")

    def delete_parallel_row(self):
        """删除并行序列行"""
        current_row = self.parallel_table.currentRow()
        if current_row >= 0:
            self.parallel_table.removeRow(current_row)
            self.log_sequence(f"删除步骤 {current_row + 1}")
        else:
            QMessageBox.warning(self, "提示", "请先选择要删除的行")

    def clear_parallel_sequence(self):
        """清空并行序列"""
        reply = QMessageBox.question(
            self,
            "确认",
            "确定要清空所有序列吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.parallel_table.setRowCount(0)
            self.log_sequence("清空序列")

    def load_parallel_preset(self, preset_type):
        """加载并行模式预设"""
        self.parallel_table.setRowCount(0)

        if preset_type == "counter":
            # 计数器: 0-15
            for i in range(16):
                row = self.parallel_table.rowCount()
                self.parallel_table.insertRow(row)
                binary = format(i, "08b")
                self.parallel_table.setItem(row, 0, QTableWidgetItem(f"{i + 1}"))
                self.parallel_table.setItem(row, 1, QTableWidgetItem(binary))
                self.parallel_table.setItem(row, 2, QTableWidgetItem(f"0x{i:02X}"))
                self.parallel_table.setItem(row, 3, QTableWidgetItem(f"计数值 {i}"))
            self.log_sequence("加载预设：计数器 (0-15)")

        elif preset_type == "alternate":
            # 交替: 0xAA, 0x55
            patterns = [0xAA, 0x55]
            for i, value in enumerate(patterns * 4):  # 重复4次
                row = self.parallel_table.rowCount()
                self.parallel_table.insertRow(row)
                binary = format(value, "08b")
                self.parallel_table.setItem(row, 0, QTableWidgetItem(f"{row + 1}"))
                self.parallel_table.setItem(row, 1, QTableWidgetItem(binary))
                self.parallel_table.setItem(row, 2, QTableWidgetItem(f"0x{value:02X}"))
                desc = "高电平" if value == 0xAA else "低电平"
                self.parallel_table.setItem(row, 3, QTableWidgetItem(desc))
            self.log_sequence("加载预设：交替模式 (0xAA/0x55)")

        elif preset_type == "custom":
            # 自定义对话框
            text, ok = QInputDialog.getText(
                self,
                "自定义序列",
                "请输入字节序列（十六进制，空格分隔）：\n例如: 00 11 22 AA BB FF",
            )
            if ok and text:
                hex_values = text.split()
                for i, hex_str in enumerate(hex_values):
                    try:
                        value = int(hex_str, 16)
                        if 0 <= value <= 255:
                            row = self.parallel_table.rowCount()
                            self.parallel_table.insertRow(row)
                            binary = format(value, "08b")
                            self.parallel_table.setItem(
                                row, 0, QTableWidgetItem(f"{row + 1}")
                            )
                            self.parallel_table.setItem(
                                row, 1, QTableWidgetItem(binary)
                            )
                            self.parallel_table.setItem(
                                row, 2, QTableWidgetItem(f"0x{value:02X}")
                            )
                            self.parallel_table.setItem(row, 3, QTableWidgetItem(""))
                    except ValueError:
                        QMessageBox.warning(
                            self, "错误", f"无效的十六进制值: {hex_str}"
                        )
                        return
                self.log_sequence(f"加载自定义序列：{len(hex_values)} 个字节")

    # ============ 串行模式相关函数 ============

    def custom_serial_sequence(self, channel_id):
        """自定义串行序列"""
        text, ok = QInputDialog.getText(
            self,
            f"通道 {channel_id} 自定义序列",
            "请输入比特序列（0和1组成，任意长度）：\n例如: 00101101",
        )
        if ok and text:
            # 验证格式
            clean_text = text.replace(" ", "")
            if all(c in "01" for c in clean_text):
                self.serial_channels[channel_id].sequence_input.setText(clean_text)
                self.log_sequence(f"通道 {channel_id} 设置序列: {clean_text}")
            else:
                QMessageBox.warning(self, "错误", "序列只能包含 0 和 1")

    # ============ 应用配置函数 ============

    def apply_sequence_config(self):
        """应用序列配置到FPGA（自动启动输出）"""
        if self.parallel_radio.isChecked():
            self.apply_parallel_config()
        else:
            self.apply_serial_config()

    def apply_parallel_config(self):
        """应用并行模式配置"""
        # 收集序列数据
        row_count = self.parallel_table.rowCount()
        if row_count == 0:
            QMessageBox.warning(self, "错误", "序列为空，请先添加步骤")
            return

        sequence_data = []
        for row in range(row_count):
            binary_item = self.parallel_table.item(row, 1)
            if binary_item:
                binary_str = binary_item.text().replace(" ", "")
                if len(binary_str) == 8 and all(c in "01" for c in binary_str):
                    byte_value = int(binary_str, 2)
                    sequence_data.append(byte_value)
                else:
                    QMessageBox.warning(
                        self, "错误", f"步骤 {row + 1} 的二进制格式错误（需要8位）"
                    )
                    return

        freq = self.parallel_freq_spin.value()

        # 构建日志
        self.log_sequence("=" * 60)
        self.log_sequence(f"应用并行模式配置:")
        self.log_sequence(f"  频率: {freq:.1f} Hz")
        self.log_sequence(f"  序列长度: {len(sequence_data)} 字节")
        self.log_sequence(f"  序列数据: {' '.join(f'{b:02X}' for b in sequence_data)}")

        # 检查串口连接
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_sequence("❌ 错误: CDC串口未连接，请先在'函数发生器'页面连接串口")
            QMessageBox.warning(self, "串口未连接", "请先在顶部连接CDC串口")
            return

        # 步骤1: 发送并行模式配置 (0x30)
        # payload格式: [序列长度(1字节)][序列数据...]
        payload = bytes([len(sequence_data)] + sequence_data)
        if send_cdc_command(self.serial_manager, CMD_SEQ_PARALLEL_MODE, payload):
            self.log_sequence(f"✅ 发送命令 0x30: 并行模式配置")
        else:
            self.log_sequence(f"❌ 发送命令 0x30 失败")
            QMessageBox.critical(self, "发送失败", "无法发送并行模式配置命令")
            return

        # 步骤2: 发送频率控制 (0x32)
        # freq_word = (目标频率 / 系统时钟50MHz) * 2^32
        freq_word = int((freq / 50_000_000) * (2**32))
        payload = struct.pack("<I", freq_word)  # 小端32位整数
        if send_cdc_command(self.serial_manager, CMD_SEQ_FREQ_CONTROL, payload):
            self.log_sequence(
                f"✅ 发送命令 0x32: 频率控制 (freq_word=0x{freq_word:08X})"
            )
        else:
            self.log_sequence(f"❌ 发送命令 0x32 失败")

        # 步骤3: 启动输出 (0x33)
        if send_cdc_command(self.serial_manager, CMD_SEQ_START):
            self.log_sequence(f"✅ 发送命令 0x33: 启动输出")
            self.log_sequence("=" * 60)
            QMessageBox.information(
                self,
                "配置成功",
                f"并行模式配置已发送到FPGA\n\n"
                f"频率: {freq:.1f} Hz\n"
                f"序列长度: {len(sequence_data)} 字节\n"
                f"SEQ_OUT引脚开始输出",
            )
        else:
            self.log_sequence(f"❌ 发送命令 0x33 失败")
            self.log_sequence("=" * 60)

    def apply_serial_config(self):
        """应用串行模式配置（使用新协议0x40-0x43，支持每通道独立频率）"""
        active_channels = []

        for ch_id, channel_widget in enumerate(self.serial_channels):
            if channel_widget.enable_check.isChecked():
                sequence = channel_widget.sequence_input.text().replace(" ", "")
                bit_freq = channel_widget.bit_freq_spin.value()  # 读取位频率

                if not sequence:
                    QMessageBox.warning(self, "错误", f"通道 {ch_id} 序列为空")
                    return

                if not all(c in "01" for c in sequence):
                    QMessageBox.warning(
                        self, "错误", f"通道 {ch_id} 序列格式错误（只能包含0和1）"
                    )
                    return

                active_channels.append(
                    {"ch": ch_id, "sequence": sequence, "bit_freq": bit_freq}
                )

        if len(active_channels) == 0:
            QMessageBox.warning(self, "错误", "没有启用的通道")
            return

        # 检查串口连接
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_sequence("❌ 错误: CDC串口未连接")
            QMessageBox.warning(self, "串口未连接", "请先连接CDC串口")
            return

        # 构建日志
        self.log_sequence("=" * 60)
        self.log_sequence(f"🚀 应用串行模式配置（独立频率）:")
        self.log_sequence(f"  活跃通道数: {len(active_channels)}")

        # 使用新协议发送配置
        enable_mask = 0x00

        for ch_config in active_channels:
            ch_id = ch_config["ch"]
            sequence_str = ch_config["sequence"]
            bit_freq = ch_config["bit_freq"]  # 使用位频率

            seq_len = len(sequence_str)

            if seq_len > 255:
                self.log_sequence(f"❌ 通道 {ch_id} 序列长度超过255位: {seq_len}")
                continue

            # 检查频率范围 (0.01Hz ~ 2MHz，32位DDS + 三级流水线)
            if bit_freq < 0.01:
                self.log_sequence(
                    f"⚠️ 通道 {ch_id} 频率过低 ({bit_freq:.3f}Hz)，" f"请增大位频率"
                )
                QMessageBox.warning(
                    self,
                    "频率超出范围",
                    f"通道 {ch_id} 的位频率过低 ({bit_freq:.3f}Hz)。\n\n"
                    f"硬件限制（32位DDS + 三级流水线）:\n"
                    f"  位频率范围: 0.01Hz ~ 2MHz\n"
                    f"  对应每位时间: 0.5μs ~ 100s",
                )
                continue
            elif bit_freq > 2000000:
                self.log_sequence(
                    f"⚠️ 通道 {ch_id} 频率过高 ({bit_freq/1e6:.2f}MHz)，" f"请减小位频率"
                )
                QMessageBox.warning(
                    self,
                    "频率超出范围",
                    f"通道 {ch_id} 的位频率过高 ({bit_freq/1e6:.2f}MHz)。\n\n"
                    f"硬件限制（32位DDS + 三级流水线）:\n"
                    f"  位频率范围: 0.01Hz ~ 2MHz\n"
                    f"  对应每位时间: 0.5μs ~ 100s",
                )
                continue

            # 计算DDS频率字和每位时间（用于调试）
            freq_word = calc_freq_word(bit_freq)
            actual_freq = (freq_word * 50000000) / 4294967296  # 反算实际频率
            bit_time_ms = 1000.0 / bit_freq  # 计算每位时间

            self.log_sequence(
                f"  通道 {ch_id}: {sequence_str} "
                f"(位频率 {bit_freq:.2f}Hz, 每位 {bit_time_ms:.3f}ms)"
            )
            self.log_sequence(
                f"    🔧 频率字={freq_word} (0x{freq_word:08X}), "
                f"实际频率={actual_freq:.3f}Hz"
            )

            # 1. 配置通道参数 (0x40)
            cmd = cmd_config_channel(ch_id, bit_freq, seq_len)
            if self.serial_manager.send_raw(cmd):
                self.log_sequence(f"    ✅ 配置通道参数")
            else:
                self.log_sequence(f"    ❌ 配置通道参数失败")
                continue

            # 2. 写入序列数据 (0x41)
            # 需要将比特序列打包成字节
            # 序列 "10101010" -> 字节0存储8个比特
            # 注意：serial_ram是按字节存储，展平时每个字节的8位会展开成8个比特

            # 填充到8的倍数
            padded_len = ((seq_len + 7) // 8) * 8
            sequence_padded = sequence_str.ljust(padded_len, "0")

            # 打包成字节（每8位一个字节）
            byte_addr = 0
            for i in range(0, padded_len, 8):
                # 提取8位
                bits_str = sequence_padded[i : i + 8]
                # 转换为字节：bit0对应最低位
                # "10101010" -> bit[0]=1, bit[1]=0, bit[2]=1...
                # 需要反转，因为RAM展平时是 [byte0_bit7:byte0_bit0, byte1_bit7:byte1_bit0, ...]
                byte_val = 0
                for bit_idx, bit_char in enumerate(bits_str):
                    if bit_char == "1":
                        byte_val |= 1 << bit_idx

                # 发送字节
                cmd = cmd_write_data(ch_id, byte_addr, byte_val)
                if not self.serial_manager.send_raw(cmd):
                    self.log_sequence(f"    ❌ 写入数据失败 @字节{byte_addr}")
                    break
                byte_addr += 1

                # 添加小延时，确保FPGA有时间处理
                import time

                time.sleep(0.001)  # 1ms延时
            else:
                self.log_sequence(f"    ✅ 写入 {seq_len} 位数据 ({byte_addr}字节)")
                enable_mask |= 1 << ch_id

            # 通道间添加延时
            import time

            time.sleep(0.01)  # 10ms延时，让FPGA处理完当前通道

        # 3. 使能通道 (0x42)
        if enable_mask != 0x00:
            cmd = cmd_enable_channels(enable_mask)
            if self.serial_manager.send_raw(cmd):
                self.log_sequence(f"✅ 使能通道: 0x{enable_mask:02X}")
                self.log_sequence("=" * 60)
                QMessageBox.information(
                    self,
                    "配置成功",
                    f"串行模式配置已发送到FPGA\n\n"
                    f"活跃通道数: {len(active_channels)}\n"
                    f"每通道独立频率已配置\n"
                    f"SEQ_OUT引脚开始输出",
                )
            else:
                self.log_sequence(f"❌ 使能通道失败")
                self.log_sequence("=" * 60)
        else:
            self.log_sequence(f"❌ 没有成功配置的通道")
            self.log_sequence("=" * 60)

    def stop_sequence_output(self):
        """停止序列输出"""
        # 检查串口连接
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_sequence("⚠️ 警告: CDC串口未连接")
            return

        # 使用新协议停止：发送使能掩码0 (0x42)
        cmd = cmd_enable_channels(0x00)
        if self.serial_manager.send_raw(cmd):
            self.log_sequence("✅ 已停止所有通道输出 (0x42)")
        else:
            self.log_sequence("❌ 停止命令发送失败")

    def log_sequence(self, message):
        """记录日志"""
        self.sequence_log.append(message)
        scrollbar = self.sequence_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ============ 逻辑分析仪页面 ============

    def create_logic_analyzer_page(self):
        """
        创建逻辑分析仪页面（参考Saleae Logic 2界面设计）

        布局特点：
        - 右侧：控制面板（通道、采样、触发配置）
        - 左侧：大面积波形显示（黑色背景，类似Logic）
        - 下方：可选的协议解码数据表格
        """
        page = QWidget()
        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        # ========== 左侧：波形显示区域 ==========
        waveform_widget = QWidget()
        waveform_layout = QVBoxLayout(waveform_widget)
        waveform_layout.setContentsMargins(4, 4, 4, 4)
        waveform_layout.setSpacing(4)

        # 顶部工具栏
        top_toolbar = QHBoxLayout()

        self.la_start_btn = QPushButton("▶ Start")
        self.la_start_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; "
            "padding: 6px 16px; border-radius: 4px;"
        )
        self.la_start_btn.clicked.connect(self.arm_logic_analyzer)
        top_toolbar.addWidget(self.la_start_btn)

        self.la_stop_btn = QPushButton("⏹ Stop")
        self.la_stop_btn.setStyleSheet(
            "background-color: #f44336; color: white; "
            "padding: 6px 16px; border-radius: 4px;"
        )
        self.la_stop_btn.clicked.connect(self.stop_logic_analyzer)
        top_toolbar.addWidget(self.la_stop_btn)

        top_toolbar.addStretch()

        # 时间轴信息
        time_info_label = QLabel("⏱ 采样时间: 0.000 s | 采样点: 0")
        time_info_label.setStyleSheet("color: #666; font-size: 10pt;")
        top_toolbar.addWidget(time_info_label)

        waveform_layout.addLayout(top_toolbar)

        # 波形显示（大面积，黑色背景，类似Logic）
        self.logic_plot = pg.PlotWidget()
        self.logic_plot.setBackground("#1a1a1a")  # 深色背景
        self.logic_plot.setLabel("bottom", "时间 (ms)", color="w", size="11pt")
        self.logic_plot.showGrid(x=True, y=False, alpha=0.3)
        self.logic_plot.setYRange(-0.5, 7.5)
        self.logic_plot.setMouseEnabled(x=True, y=False)  # 只允许X轴缩放

        # 添加8个通道标签（彩色，类似Logic）
        channel_colors = [
            "#9E9E9E",  # CH0 灰色
            "#FF6B6B",  # CH1 红色
            "#FF8C00",  # CH2 橙色
            "#FFD700",  # CH3 黄色
            "#90EE90",  # CH4 绿色
            "#87CEEB",  # CH5 浅蓝
            "#4169E1",  # CH6 蓝色
            "#DA70D6",  # CH7 紫色
        ]

        for ch in range(8):
            # 通道标签
            text = pg.TextItem(
                f"Channel {ch}",
                anchor=(0, 0.5),
                color=channel_colors[ch],
            )
            text.setFont(pg.Qt.QtGui.QFont("Arial", 10, pg.Qt.QtGui.QFont.Bold))
            text.setPos(-0.02, ch)
            self.logic_plot.addItem(text)

        waveform_layout.addWidget(self.logic_plot)

        # 底部：协议解码数据表格（可折叠）
        decode_group = QGroupBox("📊 协议解码数据（双击查看详情）")
        decode_group.setMaximumHeight(150)
        decode_group.setCheckable(True)
        decode_group.setChecked(False)  # 默认折叠
        decode_layout = QVBoxLayout()
        decode_layout.setContentsMargins(4, 4, 4, 4)

        self.decode_table = QTableWidget()
        self.decode_table.setColumnCount(5)
        self.decode_table.setHorizontalHeaderLabels(
            ["时间 (ms)", "协议", "事件", "数据", "说明"]
        )
        self.decode_table.horizontalHeader().setStretchLastSection(True)
        self.decode_table.setAlternatingRowColors(True)
        self.decode_table.setFont(QFont("Consolas", 9))
        self.decode_table.setStyleSheet(
            "QTableWidget { background-color: #f5f5f5; }"
            "QHeaderView::section { background-color: #e0e0e0; font-weight: bold; }"
        )

        # 添加示例数据
        self.decode_table.setRowCount(3)
        items = [
            ["0.000", "I2C", "START", "-", "起始位"],
            ["0.125", "I2C", "ADDR", "0x3C (W)", "从机地址+写"],
            ["0.250", "I2C", "ACK", "✓", "应答"],
        ]
        for row, item_data in enumerate(items):
            for col, value in enumerate(item_data):
                self.decode_table.setItem(row, col, QTableWidgetItem(value))

        decode_layout.addWidget(self.decode_table)
        decode_group.setLayout(decode_layout)
        waveform_layout.addWidget(decode_group)

        main_layout.addWidget(waveform_widget, 4)  # 波形区域占4份

        # ========== 右侧：控制面板（类似Logic右侧栏） ==========
        control_panel = QWidget()
        control_panel.setMaximumWidth(280)
        control_panel.setStyleSheet(
            "QWidget { background-color: #f8f9fa; }"
            "QGroupBox { font-weight: bold; border: 1px solid #ddd; "
            "border-radius: 4px; margin-top: 8px; padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(8, 8, 8, 8)
        control_layout.setSpacing(8)

        # 标题
        title_label = QLabel("Logic Analyzer")
        title_label.setStyleSheet(
            "font-size: 14pt; font-weight: bold; color: #333; padding: 4px;"
        )
        control_layout.addWidget(title_label)

        # 通道选择区域
        channel_group = QGroupBox("Digital Channels")
        channel_layout = QVBoxLayout()
        channel_layout.setSpacing(4)

        # 通道快捷按钮
        channel_btn_layout = QHBoxLayout()
        all_ch_btn = QPushButton("All")
        all_ch_btn.setMaximumWidth(60)
        all_ch_btn.clicked.connect(lambda: self.log_la("选择全部通道"))
        channel_btn_layout.addWidget(all_ch_btn)

        clear_ch_btn = QPushButton("Clear")
        clear_ch_btn.setMaximumWidth(60)
        clear_ch_btn.clicked.connect(lambda: self.log_la("清除通道选择"))
        channel_btn_layout.addWidget(clear_ch_btn)
        channel_btn_layout.addStretch()
        channel_layout.addLayout(channel_btn_layout)

        # 8个通道复选框（彩色标记）
        self.channel_checks = []
        for ch in range(8):
            ch_check = QCheckBox(f"Channel {ch}")
            ch_check.setChecked(True)
            ch_check.setStyleSheet(
                f"QCheckBox {{ color: {channel_colors[ch]}; font-weight: bold; }}"
            )
            self.channel_checks.append(ch_check)
            channel_layout.addWidget(ch_check)

        channel_group.setLayout(channel_layout)
        control_layout.addWidget(channel_group)

        # 采样配置
        sample_group = QGroupBox("Sampling")
        sample_layout = QVBoxLayout()
        sample_layout.setSpacing(6)

        sample_layout.addWidget(QLabel("采样率:"))
        self.la_sample_rate_combo = QComboBox()
        self.la_sample_rate_combo.addItems(
            ["1 MS/s", "10 MS/s", "24 MS/s", "50 MS/s", "100 MS/s", "125 MS/s"]
        )
        self.la_sample_rate_combo.setCurrentIndex(2)  # 默认24MS/s
        sample_layout.addWidget(self.la_sample_rate_combo)

        sample_layout.addWidget(QLabel("采样深度:"))
        self.la_sample_depth_combo = QComboBox()
        self.la_sample_depth_combo.addItems(
            ["1K", "4K", "16K", "64K", "256K", "1M", "4M"]
        )
        self.la_sample_depth_combo.setCurrentIndex(3)  # 默认64K
        sample_layout.addWidget(self.la_sample_depth_combo)

        sample_group.setLayout(sample_layout)
        control_layout.addWidget(sample_group)

        # 触发配置
        trigger_group = QGroupBox("Trigger")
        trigger_layout = QVBoxLayout()
        trigger_layout.setSpacing(6)

        # 触发模式选择
        trigger_mode_layout = QHBoxLayout()
        self.trigger_mode_group = QButtonGroup()

        looping_radio = QRadioButton("Looping")
        looping_radio.setChecked(True)
        looping_radio.setToolTip("连续采集模式")
        self.trigger_mode_group.addButton(looping_radio, 0)
        trigger_mode_layout.addWidget(looping_radio)

        timer_radio = QRadioButton("Timer")
        timer_radio.setToolTip("定时触发模式")
        self.trigger_mode_group.addButton(timer_radio, 1)
        trigger_mode_layout.addWidget(timer_radio)

        trigger_radio = QRadioButton("Trigger")
        trigger_radio.setToolTip("条件触发模式")
        self.trigger_mode_group.addButton(trigger_radio, 2)
        trigger_mode_layout.addWidget(trigger_radio)

        trigger_layout.addLayout(trigger_mode_layout)

        # 触发通道
        trigger_layout.addWidget(QLabel("触发通道:"))
        self.la_trigger_channel_combo = QComboBox()
        self.la_trigger_channel_combo.addItems([f"CH{i}" for i in range(8)])
        trigger_layout.addWidget(self.la_trigger_channel_combo)

        # 触发边沿
        trigger_layout.addWidget(QLabel("触发条件:"))
        self.la_trigger_edge_combo = QComboBox()
        self.la_trigger_edge_combo.addItems(
            ["上升沿 ↑", "下降沿 ↓", "双边沿 ↕", "高电平", "低电平"]
        )
        trigger_layout.addWidget(self.la_trigger_edge_combo)

        trigger_group.setLayout(trigger_layout)
        control_layout.addWidget(trigger_group)

        # 协议解码器
        decoder_group = QGroupBox("Analyzers")
        decoder_layout = QVBoxLayout()
        decoder_layout.setSpacing(6)

        decoder_layout.addWidget(QLabel("添加协议解码器:"))
        self.decoder_combo = QComboBox()
        self.decoder_combo.addItems(
            ["选择协议...", "I2C", "SPI", "UART", "CAN", "1-Wire"]
        )
        decoder_layout.addWidget(self.decoder_combo)

        add_decoder_btn = QPushButton("+ Add Analyzer")
        add_decoder_btn.clicked.connect(lambda: self.log_la("添加解码器功能开发中..."))
        decoder_layout.addWidget(add_decoder_btn)

        decoder_group.setLayout(decoder_layout)
        control_layout.addWidget(decoder_group)

        control_layout.addStretch()

        # 日志输出（紧凑）
        log_label = QLabel("📝 日志:")
        log_label.setStyleSheet("font-weight: bold; color: #666;")
        control_layout.addWidget(log_label)

        self.la_log = QTextEdit()
        self.la_log.setReadOnly(True)
        self.la_log.setMaximumHeight(100)
        self.la_log.setFont(QFont("Consolas", 8))
        self.la_log.setPlaceholderText("等待开始采集...")
        self.la_log.setStyleSheet(
            "QTextEdit { background-color: white; border: 1px solid #ddd; "
            "border-radius: 4px; padding: 4px; }"
        )
        control_layout.addWidget(self.la_log)

        main_layout.addWidget(control_panel, 1)  # 控制面板占1份

        return page

    def arm_logic_analyzer(self):
        """启动逻辑分析仪采集"""
        self.log_la("▶ 开始采集，等待触发...")

        # 检查串口连接
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_la("❌ 错误: CDC串口未连接")
            QMessageBox.warning(self, "串口未连接", "请先连接CDC串口")
            return

        # 获取采样配置
        sample_rate_text = self.la_sample_rate_combo.currentText()
        sample_depth_text = self.la_sample_depth_combo.currentText()
        trigger_channel = self.la_trigger_channel_combo.currentIndex()
        trigger_edge = self.la_trigger_edge_combo.currentIndex()

        # 构建payload: [采样率ID][采样深度ID][触发通道][触发边沿]
        sample_rate_map = {
            "1 MS/s": 0,
            "10 MS/s": 1,
            "24 MS/s": 2,
            "50 MS/s": 3,
            "100 MS/s": 4,
            "125 MS/s": 5,
        }
        sample_depth_map = {
            "1K": 0,
            "4K": 1,
            "16K": 2,
            "64K": 3,
            "256K": 4,
            "1M": 5,
            "4M": 6,
        }

        sample_rate_id = sample_rate_map.get(sample_rate_text, 2)
        sample_depth_id = sample_depth_map.get(sample_depth_text, 3)

        payload = bytes(
            [sample_rate_id, sample_depth_id, trigger_channel, trigger_edge]
        )

        if send_cdc_command(self.serial_manager, CMD_LA_ARM, payload):
            self.log_la(f"✅ 发送命令 0x41: 预备采集")
            self.log_la(f"   采样率: {sample_rate_text}")
            self.log_la(f"   采样深度: {sample_depth_text}")
            self.log_la(f"   触发通道: CH{trigger_channel}")
            self.log_la(f"   触发条件: {self.la_trigger_edge_combo.currentText()}")
        else:
            self.log_la("❌ 命令发送失败")

    def stop_logic_analyzer(self):
        """停止逻辑分析仪"""
        self.log_la("⏹ 停止采集")
        if send_cdc_command(self.serial_manager, CMD_LA_STOP, b""):
            self.log_la("✅ 已发送停止命令 0x43")
        else:
            self.log_la("❌ 停止命令发送失败")

    def log_la(self, msg):
        """逻辑分析仪日志输出"""
        timestamp = QTime.currentTime().toString("HH:mm:ss")
        self.la_log.append(f"[{timestamp}] {msg}")

    # ============ 协议解码页面 ============

    def create_protocol_decoder_page(self):
        """创建协议解码页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 协议选择和配置
        config_layout = QHBoxLayout()

        # 左侧：协议选择
        protocol_group = QGroupBox("协议类型")
        protocol_group.setMaximumWidth(200)
        protocol_layout = QVBoxLayout()

        self.protocol_type_combo = QComboBox()
        self.protocol_type_combo.addItems(
            ["I2C", "SPI", "UART", "PWM", "CAN", "1-Wire"]
        )
        self.protocol_type_combo.currentTextChanged.connect(self.on_protocol_changed)
        protocol_layout.addWidget(self.protocol_type_combo)

        # 通道映射
        protocol_layout.addWidget(QLabel("通道映射:"))
        self.protocol_mapping_text = QTextEdit()
        self.protocol_mapping_text.setMaximumHeight(100)
        self.protocol_mapping_text.setReadOnly(True)
        self.protocol_mapping_text.setPlainText("I2C:\n  SCL: CH0\n  SDA: CH1")
        protocol_layout.addWidget(self.protocol_mapping_text)

        protocol_layout.addStretch()
        protocol_group.setLayout(protocol_layout)
        config_layout.addWidget(protocol_group)

        # 右侧：解码结果
        decode_group = QGroupBox("解码结果")
        decode_layout = QVBoxLayout()

        # 解码表格
        self.decode_table = QTableWidget()
        self.decode_table.setColumnCount(5)
        self.decode_table.setHorizontalHeaderLabels(
            ["时间戳", "事件类型", "地址/数据", "状态", "说明"]
        )
        self.decode_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # 添加示例数据
        self.decode_table.setRowCount(3)
        self.decode_table.setItem(0, 0, QTableWidgetItem("0.000 ms"))
        self.decode_table.setItem(0, 1, QTableWidgetItem("START"))
        self.decode_table.setItem(0, 2, QTableWidgetItem("-"))
        self.decode_table.setItem(0, 3, QTableWidgetItem("✅"))
        self.decode_table.setItem(0, 4, QTableWidgetItem("I2C起始条件"))

        self.decode_table.setItem(1, 0, QTableWidgetItem("0.008 ms"))
        self.decode_table.setItem(1, 1, QTableWidgetItem("ADDR"))
        self.decode_table.setItem(1, 2, QTableWidgetItem("0x3C (W)"))
        self.decode_table.setItem(1, 3, QTableWidgetItem("ACK"))
        self.decode_table.setItem(1, 4, QTableWidgetItem("OLED地址"))

        self.decode_table.setItem(2, 0, QTableWidgetItem("0.016 ms"))
        self.decode_table.setItem(2, 1, QTableWidgetItem("DATA"))
        self.decode_table.setItem(2, 2, QTableWidgetItem("0xA8"))
        self.decode_table.setItem(2, 3, QTableWidgetItem("ACK"))
        self.decode_table.setItem(2, 4, QTableWidgetItem("数据字节"))

        decode_layout.addWidget(self.decode_table)

        # 解码按钮
        decode_btn_layout = QHBoxLayout()

        self.decode_start_btn = QPushButton("🔍 开始解码")
        self.decode_start_btn.setMaximumWidth(100)
        self.decode_start_btn.clicked.connect(self.start_decode)
        decode_btn_layout.addWidget(self.decode_start_btn)

        self.decode_export_btn = QPushButton("💾 导出结果")
        self.decode_export_btn.setMaximumWidth(100)
        self.decode_export_btn.clicked.connect(self.export_decode_result)
        decode_btn_layout.addWidget(self.decode_export_btn)

        decode_btn_layout.addStretch()
        decode_layout.addLayout(decode_btn_layout)

        decode_group.setLayout(decode_layout)
        config_layout.addWidget(decode_group, 1)

        layout.addLayout(config_layout)

        # 协议统计
        stats_group = QGroupBox("协议统计")
        stats_group.setMaximumHeight(80)
        stats_layout = QHBoxLayout()

        self.stats_label = QLabel(
            "<b>总事件数:</b> 3 | "
            "<b>错误数:</b> 0 | "
            "<b>总时长:</b> 0.016 ms | "
            "<b>平均速率:</b> 100 kHz"
        )
        self.stats_label.setStyleSheet("font-size: 9pt;")
        stats_layout.addWidget(self.stats_label)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        return page

    def on_protocol_changed(self, protocol):
        """协议类型切换时更新通道映射"""
        mapping = {
            "I2C": "I2C:\n  SCL: CH0\n  SDA: CH1",
            "SPI": "SPI:\n  CLK: CH0\n  MOSI: CH1\n  MISO: CH2\n  CS: CH3",
            "UART": "UART:\n  TX: CH0\n  RX: CH1",
            "PWM": "PWM:\n  PWM0-7: CH0-CH7",
            "CAN": "CAN:\n  CAN_TX: CH0\n  CAN_RX: CH1",
            "1-Wire": "1-Wire:\n  DQ: CH0",
        }
        self.protocol_mapping_text.setPlainText(mapping.get(protocol, "未定义"))

    def start_decode(self):
        """开始协议解码"""
        protocol = self.protocol_type_combo.currentText()
        QMessageBox.information(
            self,
            "功能待实现",
            f"{protocol} 协议解码功能待实现\n\n"
            "需要实现：\n"
            "1. 从逻辑分析仪读取原始数据\n"
            "2. 根据协议规则解析信号\n"
            "3. 生成解码结果表格\n"
            "4. 统计协议特征参数",
        )

    def export_decode_result(self):
        """导出解码结果"""
        QMessageBox.information(
            self,
            "功能待实现",
            "解码结果导出功能待实现\n\n"
            "支持格式：\n"
            "- CSV文件\n"
            "- JSON文件\n"
            "- 文本报告",
        )

    # ============ PWM控制器页面 ============

    def create_pwm_controller_page(self):
        """创建8路PWM控制器页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 顶部说明
        info_label = QLabel(
            "⚡ 8路独立PWM控制器 | 频率范围: 1Hz - 100kHz | 占空比精度: 0.1%"
        )
        info_label.setStyleSheet(
            "font-weight: bold; font-size: 11pt; color: #333; "
            "padding: 8px; background-color: #e3f2fd; border-radius: 4px;"
        )
        layout.addWidget(info_label)

        # PWM通道控制区域（滚动区域）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(6)

        # 存储每个PWM通道的控件
        self.pwm_channels = []

        # 创建8路PWM控制
        for ch in range(8):
            channel_widget = self.create_pwm_channel_widget(ch)
            scroll_layout.addWidget(channel_widget)
            # 存储控件引用（后续实现）

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # 底部批量控制
        batch_group = QGroupBox("批量控制")
        batch_group.setMaximumHeight(120)
        batch_layout = QGridLayout()
        batch_layout.setSpacing(8)

        # 预设模式
        batch_layout.addWidget(QLabel("预设模式:"), 0, 0)
        self.pwm_preset_combo = QComboBox()
        self.pwm_preset_combo.addItems(
            [
                "自定义",
                "电机模式 (1kHz, 50%)",
                "舵机模式 (50Hz, 7.5%)",
                "LED调光 (1kHz, 30%)",
                "关闭所有PWM",
            ]
        )
        self.pwm_preset_combo.currentIndexChanged.connect(self.apply_pwm_preset)
        batch_layout.addWidget(self.pwm_preset_combo, 0, 1)

        # 全局启停
        self.pwm_start_all_btn = QPushButton("✅ 启动全部")
        self.pwm_start_all_btn.setMaximumWidth(100)
        self.pwm_start_all_btn.clicked.connect(lambda: self.pwm_batch_control(True))
        batch_layout.addWidget(self.pwm_start_all_btn, 0, 2)

        self.pwm_stop_all_btn = QPushButton("⛔ 停止全部")
        self.pwm_stop_all_btn.setMaximumWidth(100)
        self.pwm_stop_all_btn.clicked.connect(lambda: self.pwm_batch_control(False))
        batch_layout.addWidget(self.pwm_stop_all_btn, 0, 3)

        # 日志
        batch_layout.addWidget(QLabel("操作日志:"), 1, 0)
        self.pwm_log = QTextEdit()
        self.pwm_log.setReadOnly(True)
        self.pwm_log.setMaximumHeight(50)
        self.pwm_log.setFont(QFont("Consolas", 9))
        batch_layout.addWidget(self.pwm_log, 1, 1, 1, 3)

        batch_group.setLayout(batch_layout)
        layout.addWidget(batch_group)

        return page

    def create_pwm_channel_widget(self, channel_id):
        """创建单个PWM通道控制组件"""
        group = QGroupBox(f"PWM{channel_id} - CH{channel_id}")
        layout = QGridLayout()
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        # 使能复选框
        enable_check = QCheckBox("使能")
        enable_check.setStyleSheet("font-weight: bold;")
        layout.addWidget(enable_check, 0, 0)

        # 频率设置
        layout.addWidget(QLabel("频率:"), 0, 1)
        freq_spin = QDoubleSpinBox()
        freq_spin.setRange(1, 100000)
        freq_spin.setValue(1000)
        freq_spin.setSuffix(" Hz")
        freq_spin.setMaximumWidth(120)
        layout.addWidget(freq_spin, 0, 2)

        # 快捷频率按钮
        freq_btn_layout = QHBoxLayout()
        for freq_val, freq_text in [
            (50, "50Hz"),
            (1000, "1kHz"),
            (10000, "10kHz"),
        ]:
            btn = QPushButton(freq_text)
            btn.setMaximumWidth(60)
            btn.clicked.connect(lambda checked, v=freq_val: freq_spin.setValue(v))
            freq_btn_layout.addWidget(btn)
        layout.addLayout(freq_btn_layout, 0, 3, 1, 2)

        # 占空比设置
        layout.addWidget(QLabel("占空比:"), 1, 1)
        duty_spin = QDoubleSpinBox()
        duty_spin.setRange(0, 100)
        duty_spin.setValue(50)
        duty_spin.setSuffix(" %")
        duty_spin.setDecimals(1)
        duty_spin.setSingleStep(0.1)
        duty_spin.setMaximumWidth(120)
        layout.addWidget(duty_spin, 1, 2)

        # 占空比滑块
        duty_slider = QSpinBox()
        duty_slider.setRange(0, 1000)
        duty_slider.setValue(500)
        duty_slider.setSuffix(" ‰")
        duty_slider.setMaximumWidth(150)
        # 双向绑定
        duty_spin.valueChanged.connect(lambda v: duty_slider.setValue(int(v * 10)))
        duty_slider.valueChanged.connect(lambda v: duty_spin.setValue(v / 10))
        layout.addWidget(duty_slider, 1, 3, 1, 2)

        # 应用按钮
        apply_btn = QPushButton("应用")
        apply_btn.setMaximumWidth(80)
        apply_btn.clicked.connect(
            lambda: self.apply_pwm_channel(
                channel_id,
                enable_check.isChecked(),
                freq_spin.value(),
                duty_spin.value(),
            )
        )
        layout.addWidget(apply_btn, 0, 5, 2, 1)

        group.setLayout(layout)

        # 保存控件引用
        channel_data = {
            "group": group,
            "enable": enable_check,
            "freq": freq_spin,
            "duty": duty_spin,
            "duty_slider": duty_slider,
        }
        self.pwm_channels.append(channel_data)

        return group

    def apply_pwm_channel(self, channel_id, enable, freq, duty):
        """应用单路PWM配置"""
        self.pwm_log.append(
            f"PWM{channel_id}: {'启用' if enable else '停止'}, "
            f"频率={freq:.1f}Hz, 占空比={duty:.1f}%"
        )
        # TODO: 发送CDC命令到FPGA (0x80 + channel_id)

    def apply_pwm_preset(self, index):
        """应用PWM预设模式"""
        presets = {
            1: (1000, 50),  # 电机模式
            2: (50, 7.5),  # 舵机模式
            3: (1000, 30),  # LED调光
            4: (0, 0),  # 关闭
        }
        if index in presets:
            freq, duty = presets[index]
            for ch_data in self.pwm_channels:
                ch_data["freq"].setValue(freq)
                ch_data["duty"].setValue(duty)
                if index == 4:
                    ch_data["enable"].setChecked(False)
            self.pwm_log.append(f"已应用预设: {self.pwm_preset_combo.currentText()}")

    def pwm_batch_control(self, enable):
        """批量启停PWM"""
        for ch in range(8):
            self.pwm_channels[ch]["enable"].setChecked(enable)
        self.pwm_log.append(f"{'启动' if enable else '停止'}全部PWM通道")
        # TODO: 发送批量控制命令

    # ============ 设备中心页面 ============

    def create_device_center_page(self):
        """创建设备中心页面（预留接口）"""
        page = QWidget()
        layout = QVBoxLayout(page)

        # 设备列表
        device_list_group = QGroupBox("可用设备")
        device_list_layout = QVBoxLayout()

        devices_info = QTextEdit()
        devices_info.setReadOnly(True)
        devices_info.setMaximumHeight(180)
        devices_info.setHtml(
            "<h4>I2C设备：</h4>"
            "<ul>"
            "<li>🔲 OLED (SSD1306) - 0x3C</li>"
            "<li>🔲 MPU6050 (六轴传感器) - 0x68</li>"
            "</ul>"
            "<h4>SPI设备：</h4>"
            "<ul>"
            "<li>🔲 W25Q128 Flash</li>"
            "</ul>"
            "<h4>UART设备：</h4>"
            "<ul>"
            "<li>🔲 蓝牙模块 (HC-05)</li>"
            "</ul>"
            "<h4>CAN总线：</h4>"
            "<ul>"
            "<li>🔲 CAN收发器 (TJA1050/SN65HVD230)</li>"
            "</ul>"
            "<h4>单总线设备：</h4>"
            "<ul>"
            "<li>🔲 DS18B20 温度传感器</li>"
            "</ul>"
            "<h4>PWM输出：</h4>"
            "<ul>"
            "<li>🔲 8通道PWM控制器 (独立引脚)</li>"
            "</ul>"
        )
        device_list_layout.addWidget(devices_info)

        device_list_group.setLayout(device_list_layout)
        layout.addWidget(device_list_group)

        # 设备操作面板
        operation_group = QGroupBox("设备操作")
        operation_layout = QHBoxLayout()

        # OLED控制
        oled_group = QGroupBox("OLED显示")
        oled_layout = QVBoxLayout()

        self.oled_text_input = QTextEdit()
        self.oled_text_input.setPlaceholderText("输入要显示的文本...")
        self.oled_text_input.setMaximumHeight(60)
        oled_layout.addWidget(self.oled_text_input)

        self.oled_send_btn = QPushButton("发送到OLED")
        self.oled_send_btn.clicked.connect(self.show_device_todo)
        oled_layout.addWidget(self.oled_send_btn)

        oled_group.setLayout(oled_layout)
        operation_layout.addWidget(oled_group)

        # Flash控制
        flash_group = QGroupBox("Flash存储")
        flash_layout = QVBoxLayout()

        self.flash_read_btn = QPushButton("读取扇区")
        self.flash_read_btn.clicked.connect(self.show_device_todo)
        flash_layout.addWidget(self.flash_read_btn)

        self.flash_write_btn = QPushButton("写入数据")
        self.flash_write_btn.clicked.connect(self.show_device_todo)
        flash_layout.addWidget(self.flash_write_btn)

        self.flash_erase_btn = QPushButton("擦除芯片")
        self.flash_erase_btn.clicked.connect(self.show_device_todo)
        flash_layout.addWidget(self.flash_erase_btn)

        flash_group.setLayout(flash_layout)
        operation_layout.addWidget(flash_group)

        operation_group.setLayout(operation_layout)
        layout.addWidget(operation_group)

        # 操作日志
        log_group = QGroupBox("操作日志")
        log_layout = QVBoxLayout()

        self.device_log = QTextEdit()
        self.device_log.setReadOnly(True)
        self.device_log.setPlaceholderText("设备操作日志将显示在这里...")
        log_layout.addWidget(self.device_log)

        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        return page

    def show_device_todo(self):
        """显示设备中心待实现提示"""
        QMessageBox.information(
            self,
            "功能待实现",
            "设备中心功能待实现，需要：\n\n"
            "🔧 硬件支持：\n"
            "  - I2C/SPI/UART接口\n"
            "  - 设备中心转接板\n\n"
            "💻 软件支持：\n"
            "  - 设备驱动库\n"
            "  - 通信协议栈\n"
            "  - CDC命令接口",
        )


def main():
    """独立测试"""
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    widget = LogicAnalyzerTab()
    widget.resize(1200, 800)
    widget.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
