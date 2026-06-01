#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逻辑分析仪标签页 - 融入主体上位机
与 PulseView 兼容的 8 通道逻辑分析仪 + 8路数字信号测量
"""

import sys
import os
import time
import struct
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QTextEdit,
    QProgressBar,
    QCheckBox,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QGridLayout,
    QTabWidget,
)
from PySide6.QtCore import Qt, QTimer, QEventLoop
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from utils.pulseview_exporter import export_raw_to_sr
from logic_analyzer.digital_signal_panel import DigitalSignalPanel


# ============ 命令码定义 ============
CMD_LA_SET_SAMPLE_RATE = 0x60  # 设置采样率（分频系数）
CMD_LA_SET_BUFFER_SIZE = 0x61  # 设置缓冲区大小
CMD_LA_SET_TRIGGER = 0x62  # 设置触发参数
CMD_LA_START = 0x63  # 开始采集
CMD_LA_STOP = 0x64  # 停止采集


class LogicAnalyzerPulseViewTab(QWidget):
    """逻辑分析仪标签页 - 包含逻辑分析仪和数字信号测量"""

    def __init__(self, serial_manager, parent=None):
        super().__init__(parent)
        self.serial_manager = serial_manager

        # 创建主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # 创建子标签页
        sub_tabs = QTabWidget()

        # 标签页1：逻辑分析仪（原有功能）
        self.logic_analyzer_widget = LogicAnalyzerWidget(serial_manager)
        sub_tabs.addTab(self.logic_analyzer_widget, "📊 逻辑分析仪")

        # 标签页2：数字信号测量（新功能）
        self.digital_signal_widget = DigitalSignalPanel(serial_manager)
        sub_tabs.addTab(self.digital_signal_widget, "🔍 数字信号测量")

        main_layout.addWidget(sub_tabs)


class LogicAnalyzerWidget(QWidget):
    """逻辑分析仪核心组件"""

    def __init__(self, serial_manager, parent=None):
        super().__init__(parent)
        self.serial_manager = serial_manager
        self.captured_data = None
        self.receiving_data = False
        self.received_buffer = bytearray()
        self.capture_start_time = None  # 🔥 V2.3：记录采集开始时间
        self.capture_end_time = None  # 🔥 V2.3：记录采集结束时间
        self.actual_sample_rate = None
        self.exported_file = None  # 🔥 记录已导出的文件路径，防止重复导出

        # 🔥 采样率配置状态跟踪（修复：避免重复配置导致时序混乱）
        self.last_configured_sample_rate = None  # 上次成功配置的采样率

        # 🔥 接收统计变量
        self.last_receive_time = None
        self.bytes_per_second = 0
        self.last_log_kb = 0
        # 🔥 新增：丢包检测变量
        self.buffer_overflow_count = 0  # 缓冲区积压次数
        self.max_buffer_size_seen = 0  # 观测到的最大缓冲区积压

        # 🔥🔥 V4.3：实测采样率校准表（直接分频系数 → 实际采样率）
        # V4.3变更：上位机直接发送原始分频系数，不再×2补偿
        # 格式：{sample_div: actual_measured_rate}
        # 计算公式：actual_rate = 50MHz / sample_div
        self.sample_rate_calibration_table = {
            # 高速采样（>5MHz）
            2: 25_000_000,  # 25MHz → div=2
            3: 16_670_000,  # 16.67MHz → div=3
            4: 12_500_000,  # 12.5MHz → div=4
            5: 10_000_000,  # 10MHz → div=5
            6: 8_330_000,  # 8.33MHz → div=6
            8: 6_250_000,  # 6.25MHz → div=8
            10: 5_000_000,  # 5MHz → div=10
            # 中速采样（1-5MHz）
            13: 3_850_000,  # 3.85MHz → div=13
            16: 3_125_000,  # 3.125MHz → div=16
            20: 2_500_000,  # 2.5MHz → div=20
            25: 2_000_000,  # 2MHz → div=25
            30: 1_670_000,  # 1.67MHz → div=30
            40: 1_250_000,  # 1.25MHz → div=40
            50: 1_000_000,  # 1MHz → div=50
            # 低速采样（<1MHz）
            54: 921_600,  # 921.6KHz → div=54 (UART 115200×8)
            60: 833_000,  # 833KHz → div=60
            80: 625_000,  # 625KHz → div=80
            100: 500_000,  # 500KHz → div=100
            108: 460_800,  # 460.8KHz → div=108 (UART 57600×8)
            120: 416_000,  # 416KHz → div=120
            160: 312_500,  # 312.5KHz → div=160
            200: 250_000,  # 250KHz → div=200
            216: 230_400,  # 230.4KHz → div=216 (UART 28800×8)
            250: 200_000,  # 200KHz → div=250
            324: 153_600,  # 153.6KHz → div=324 (UART 19200×8)
            400: 125_000,  # 125KHz → div=400
            500: 100_000,  # 100KHz → div=500
            648: 76_800,  # 76.8KHz → div=648 (UART 9600×8)
            1000: 50_000,  # 50KHz → div=1000
            # 💡 使用说明：
            # 1. 当前值为理论计算值（50MHz / div）
            # 2. 采集完成后，系统会提示"实测采样率"
            # 3. 如果偏差>1%，手动更新对应条目为实测值
            # 4. 例如：div=50实测0.98MHz → 修改为 50: 980_000
        }

        # 创建定时器用于接收数据（🔥 V9.8：优化为50us轮询，配合4MB缓冲）
        self.receive_timer = QTimer()
        self.receive_timer.timeout.connect(self.receive_data)
        # 🔥 关键优化：轮询间隔设为0，尽快执行（实际~50-100us）
        # 25MHz@115200: 数据产生2.17MB/s，缓冲区4MB可撑1.8秒
        # 50us轮询周期，每次读取512KB，大幅提升吞吐量
        self.receive_timer.setInterval(0)  # 0=尽快执行，约50-100us实际间隔

        # 创建超时定时器（动态设置，基于采样率）
        self.timeout_timer = QTimer()
        self.timeout_timer.timeout.connect(self.on_capture_timeout)
        self.timeout_timer.setSingleShot(True)

        # 数据接收统计
        self.last_receive_time = None
        self.bytes_per_second = 0

        # 🔥 性能优化：日志缓冲机制
        self.log_buffer = []  # 缓冲日志消息
        self.log_update_timer = QTimer()
        self.log_update_timer.timeout.connect(self.flush_log_buffer)
        self.log_update_timer.start(300)  # 🔥 V7.4: 300ms批量更新（降低后台开销）
        self.last_log_kb = 0  # 记录上次日志输出时的KB数
        self.log_merge_enabled = True  # 智能日志合并（相似日志合并显示）

        self.init_ui()

    def non_blocking_sleep(self, msecs):
        """非阻塞延时：等待的同时允许 UI 刷新和响应用户操作

        Args:
            msecs: 等待的毫秒数

        优势：
            - UI 保持响应（进度条转动、按钮可点击）
            - 日志实时显示
            - 用户可以随时停止操作
        """
        loop = QEventLoop()
        QTimer.singleShot(msecs, loop.quit)
        loop.exec()

    def init_ui(self):
        """初始化用户界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # 采样配置组
        sample_group = self.create_sample_group()
        main_layout.addWidget(sample_group)

        # 控制按钮组
        control_layout = self.create_control_buttons()
        main_layout.addLayout(control_layout)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 数据显示区域
        data_group = self.create_data_display()
        main_layout.addWidget(data_group)

        # 日志显示
        log_group = QGroupBox("📋 操作日志")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)

        # 日志区域顶部按钮
        log_btn_layout = QHBoxLayout()
        log_btn_layout.addStretch()

        clear_log_btn = QPushButton("🗑️ 清空日志")
        clear_log_btn.setMaximumWidth(120)
        clear_log_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #FF5722;
                color: white;
                border-radius: 3px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #E64A19;
            }
        """
        )
        clear_log_btn.clicked.connect(self.clear_log)
        log_btn_layout.addWidget(clear_log_btn)

        log_layout.addLayout(log_btn_layout)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setPlaceholderText("等待操作...")
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def create_sample_group(self):
        """创建采样配置组"""
        group = QGroupBox("📊 采样配置")
        layout = QGridLayout()
        layout.setSpacing(10)

        # 采样率预设（常用选项，简化界面）
        # 🔥 V5.5：只保留常用采样率，提升易用性
        layout.addWidget(QLabel("采样率:"), 0, 0)
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(
            [
                # 高速采样（数字信号）
                "25 MHz (div=2)",  # USB CDC极限速度
                "16.67 MHz (div=3)",  # 高速数字信号
                "12.5 MHz (div=4)",  # 高速数字信号
                "10 MHz (div=5)",  # 中高速信号
                "5 MHz (div=10)",  # 中速信号
                # 中低速采样（I2C/数字通信）
                "2.5 MHz (div=20)",  # 低速数字/I2C Fast-mode（400K×6倍）
                "2 MHz (div=25)",  # I2C Fast-mode（400KHz×5倍）
                "1 MHz (div=50)",  # I2C Standard（100KHz×10倍）
                "500 KHz (div=100)",  # 超低速信号
                "200 KHz (div=250)",  # I2C Standard（100KHz×2倍）
                "100 KHz (div=500)",  # 极低速信号
                "50 KHz (div=1000)",  # 传感器数据/极低速
                # UART专用（常用波特率×8倍采样）
                "921.6 KHz (div=54) - UART 115200×8",  # 115200波特率
                "460.8 KHz (div=108) - UART 57600×8",  # 57600波特率
                "230.4 KHz (div=216) - UART 28800×8",  # 28800波特率
                "153.6 KHz (div=324) - UART 19200×8",  # 19200波特率
                "76.8 KHz (div=648) - UART 9600×8",  # 9600波特率
            ]
        )
        self.sample_rate_combo.currentTextChanged.connect(self.on_sample_rate_changed)
        layout.addWidget(self.sample_rate_combo, 0, 1)

        # 分频系数显示（实时显示发送值和理论分频）
        layout.addWidget(QLabel("实际发送:"), 0, 2)
        self.divider_label = QLabel("div=? (未配置)")  # V4.5：初始显示未配置状态
        self.divider_label.setFont(QFont("Consolas", 9, QFont.Bold))
        self.divider_label.setStyleSheet(
            "QLabel { color: #FF9800; padding: 5px; background-color: #FFF3E0; border-radius: 3px; }"
        )
        self.divider_label.setMinimumWidth(180)
        layout.addWidget(self.divider_label, 0, 3)

        # ✅ V4.5：连接下拉框信号，实时更新分频系数显示
        self.sample_rate_combo.currentIndexChanged.connect(self.update_divider_display)

        # 输出文件
        layout.addWidget(QLabel("输出文件:"), 1, 0)
        file_layout = QHBoxLayout()
        self.output_file_edit = QLineEdit("capture.sr")
        file_layout.addWidget(self.output_file_edit)
        browse_btn = QPushButton("📁 浏览")
        browse_btn.clicked.connect(self.browse_output_file)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout, 1, 1, 1, 2)

        # 自动打开PulseView
        self.auto_open_checkbox = QCheckBox("采集完成后自动打开 PulseView")
        self.auto_open_checkbox.setChecked(True)
        layout.addWidget(self.auto_open_checkbox, 1, 3)

        group.setLayout(layout)

        # 🔥 初始化时更新一次分频系数显示，确保和下拉框选中项对应
        self.update_divider_display()

        return group

    def create_control_buttons(self):
        """创建控制按钮"""
        layout = QHBoxLayout()

        # 开始采集按钮
        self.capture_btn = QPushButton("🚀 开始采集")
        self.capture_btn.setMinimumHeight(50)
        self.capture_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.capture_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """
        )
        self.capture_btn.clicked.connect(self.start_capture)
        layout.addWidget(self.capture_btn)

        # 停止采集按钮
        self.stop_btn = QPushButton("⏹️ 停止采集")
        self.stop_btn.setMinimumHeight(50)
        self.stop_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #f44336;
                color: white;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """
        )
        self.stop_btn.clicked.connect(self.stop_capture)
        layout.addWidget(self.stop_btn)

        # 导出原始数据按钮
        self.export_raw_btn = QPushButton("💾 导出原始数据")
        self.export_raw_btn.setMinimumHeight(50)
        self.export_raw_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.export_raw_btn.setEnabled(False)
        self.export_raw_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #9C27B0;
                color: white;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """
        )
        self.export_raw_btn.clicked.connect(self.export_raw_data)
        layout.addWidget(self.export_raw_btn)

        # 导出到 PulseView 按钮
        self.export_pulseview_btn = QPushButton("📊 导出到 PulseView")
        self.export_pulseview_btn.setMinimumHeight(50)
        self.export_pulseview_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.export_pulseview_btn.setEnabled(False)
        self.export_pulseview_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #00BCD4;
                color: white;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #0097A7;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """
        )
        self.export_pulseview_btn.clicked.connect(self.export_to_pulseview)
        layout.addWidget(self.export_pulseview_btn)

        return layout

    def create_data_display(self):
        """创建数据显示区域"""
        group = QGroupBox("📊 数据显示")
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)

        # 原始数据显示
        raw_data_header = QHBoxLayout()
        raw_data_label = QLabel("原始数据 (HEX):")
        raw_data_label.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        raw_data_header.addWidget(raw_data_label)

        # 添加清空按钮
        clear_raw_btn = QPushButton("🗑️ 清空")
        clear_raw_btn.setMaximumWidth(80)
        clear_raw_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                padding: 3px 8px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
            """
        )
        clear_raw_btn.clicked.connect(self.clear_raw_data)
        raw_data_header.addWidget(clear_raw_btn)
        raw_data_header.addStretch()
        layout.addLayout(raw_data_header)

        self.raw_data_text = QTextEdit()
        self.raw_data_text.setReadOnly(True)
        self.raw_data_text.setMaximumHeight(120)
        self.raw_data_text.setFont(QFont("Consolas", 9))
        self.raw_data_text.setPlaceholderText("原始字节流将在这里显示...")
        layout.addWidget(self.raw_data_text)

        # 统计信息显示
        stats_header = QHBoxLayout()
        stats_label = QLabel("统计信息:")
        stats_label.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        stats_header.addWidget(stats_label)

        # 添加清空按钮
        clear_stats_btn = QPushButton("🗑️ 清空")
        clear_stats_btn.setMaximumWidth(80)
        clear_stats_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                padding: 3px 8px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
            """
        )
        clear_stats_btn.clicked.connect(self.clear_stats)
        stats_header.addWidget(clear_stats_btn)
        stats_header.addStretch()
        layout.addLayout(stats_header)

        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(120)
        self.stats_text.setFont(QFont("Consolas", 9))
        self.stats_text.setPlaceholderText("统计信息将在这里显示...")
        layout.addWidget(self.stats_text)

        group.setLayout(layout)
        return group

    def on_sample_rate_changed(self, text):
        """采样率选择改变"""
        self.update_divider_display()

    def get_sample_rate_value(self):
        """获取采样率数值（解析下拉框文本，提取采样率）"""
        try:
            text = self.sample_rate_combo.currentText()

            # 🔥 V2.9：直接从文本中提取数字（格式统一）
            # 格式示例："25 MHz (div=2)"
            # ⚠️ 重要：匹配顺序必须从长到短，避免子串误识别
            # 例如："2.5 MHz" 必须在 "5 MHz" 之前
            #       "12.5 MHz" 必须在 "2.5 MHz" 之前

            # 高速采样（从长到短排序）
            if "25 MHz" in text:
                return 25_000_000
            elif "16.67 MHz" in text:
                return 16_670_000
            elif "12.5 MHz" in text:
                return 12_500_000
            elif "10 MHz" in text:
                return 10_000_000

            # 中速采样（从长到短排序）
            elif "3.125 MHz" in text:
                return 3_125_000
            elif "2.5 MHz" in text:  # 🔥 必须在 "5 MHz" 之前
                return 2_500_000
            elif "5 MHz" in text:
                return 5_000_000
            elif "2 MHz" in text:
                return 2_000_000
            elif "1.67 MHz" in text:
                return 1_670_000
            elif "1.25 MHz" in text:  # 🔥 必须在 "1 MHz" 之前
                return 1_250_000
            elif "1 MHz" in text:
                return 1_000_000

            # 低速采样（从长到短排序）
            elif "921.6 K" in text:
                return 921_600
            elif "833 K" in text:
                return 833_000
            elif "625 K" in text:
                return 625_000
            elif "500 K" in text:
                return 500_000
            elif "460.8 K" in text:
                return 460_800
            elif "416 K" in text:
                return 416_000
            elif "312.5 K" in text:
                return 312_500
            elif "250 K" in text:
                return 250_000
            elif "230.4 K" in text:
                return 230_400
            elif "200 K" in text:
                return 200_000
            elif "153.6 K" in text:
                return 153_600
            elif "125 K" in text:
                return 125_000
            elif "100 K" in text:
                return 100_000
            elif "76.8 K" in text:
                return 76_800
            elif "50 K" in text:
                return 50_000
            else:
                return 25_000_000  # 🔥 默认25MHz
        except Exception:
            # 如果出现任何错误，返回默认值
            return 25_000_000  # 🔥 默认25MHz

    def update_divider_display(self):
        """实时更新分频系数显示（下拉框切换时调用）"""
        sample_rate = self.get_sample_rate_value()

        if sample_rate is None or sample_rate <= 0:
            sample_rate = 25_000_000

        # 限制最大采样率 (V7.4: 提高到50MHz用于测试)
        MAX_STREAM_SAMPLE_RATE = 50_000_000
        if sample_rate > MAX_STREAM_SAMPLE_RATE:
            sample_rate = MAX_STREAM_SAMPLE_RATE

        # 计算分频系数
        import math

        system_clk = 50_000_000
        sample_div = max(
            1, math.ceil(system_clk / sample_rate)
        )  # V7.4: 允许div=1用于50MHz测试
        actual_rate = system_clk / sample_div

        # 检查是否已配置到FPGA
        if (
            hasattr(self, "last_configured_div")
            and self.last_configured_div == sample_div
        ):
            # 已配置：绿色背景
            self.divider_label.setText(f"div={sample_div} ({actual_rate/1e6:.2f}MHz) ✓")
            self.divider_label.setStyleSheet(
                "QLabel { color: #4CAF50; padding: 5px; background-color: #E8F5E9; border-radius: 3px; font-weight: bold; }"
            )
        else:
            # 未配置或已更改：蓝色背景
            self.divider_label.setText(f"div={sample_div} ({actual_rate/1e6:.2f}MHz)")
            self.divider_label.setStyleSheet(
                "QLabel { color: #2196F3; padding: 5px; background-color: #E3F2FD; border-radius: 3px; }"
            )

    def send_config_to_fpga(self):
        """发送配置到FPGA（基于50MHz系统时钟）"""
        if not self.serial_manager.is_connected():
            self.log("❌ 错误：串口未连接")
            return

        # 🔥 V4.3：防止在采集时配置（避免状态机混乱）
        if self.receiving_data:
            self.log("❌ 错误：正在采集数据，请先停止采集再配置")
            QMessageBox.warning(
                self,
                "配置失败",
                "正在采集数据！\n\n请先点击 '停止采集' 按钮，\n然后再配置采样率。",
            )
            return

        sample_rate = self.get_sample_rate_value()
        # get_sample_rate_value现在总是返回有效值，但保留检查
        if sample_rate is None or sample_rate <= 0:
            sample_rate = 25_000_000  # 默认25MHz

        # 🔥 修复：限制最大采样率为50MHz（系统时钟频率）
        if sample_rate > 50_000_000:
            self.log("⚠️ 采样率超过50MHz限制，自动设置为50MHz")
            sample_rate = 50_000_000

        try:
            # 🔥🔥 V5.0优化：移除自动停止命令（避免不必要的CDC通信）
            # V4.3历史：为防止状态机混乱，配置前自动发送停止命令
            # V5.0修正：V5.0握手机制+超时保护已解决状态机问题，无需预防性停止
            # 删除原因：
            #   1. 用户未开始采集时，发送停止命令是多余的
            #   2. 每次配置采样率都停止，日志显示混乱（停止+设置成对出现）
            #   3. V5.0超时保护已能自动恢复卡死状态，不需要预防性清理
            # 如果真的需要停止，用户会手动点击"停止采集"按钮
            pass  # 保留try块结构

            # 🔥 V2.2/V7.4：流模式限制最大采样率（提高到50MHz用于测试）
            MAX_STREAM_SAMPLE_RATE = (
                50_000_000  # 50MHz (V7.4测试用，实际USB CDC极限≈30MB/s)
            )
            if sample_rate > MAX_STREAM_SAMPLE_RATE:
                self.log(f"")
                self.log(f"⚠️ 流模式采样率限制")
                self.log(f"   原始采样率: {sample_rate/1e6:.2f} MHz")
                self.log(f"   USB CDC传输速率: ~30 MB/s")
                self.log(
                    f"   流模式最大支持: {MAX_STREAM_SAMPLE_RATE/1e6:.0f} MHz (留有余量)"
                )
                self.log(f"   自动限制到: {MAX_STREAM_SAMPLE_RATE/1e6:.0f} MHz")
                self.log(f"")
                sample_rate = MAX_STREAM_SAMPLE_RATE

            # 1. 配置采样率（基于实际测试的系统时钟）
            self.log(f"⚙️ 配置采样率: {sample_rate} Hz")
            # 🔥🔥🔥 V2.9修正：FPGA使用独立LA PLL，输出50MHz时钟
            # 下位机修复历史：
            #   - V2.11之前：复用DDS PLL的CLKOUT2（100MHz）→ 实测采样率翻倍
            #   - V2.11之后：使用独立la_pll模块，输出50MHz → 精确匹配
            # 实测数据（V2.9修复后）：
            #   - sample_div=50 → 实际1MHz采样率 ✅（UART 115200每位约8-9个点）
            #   - sample_div=25 → 实际2MHz采样率 ✅
            # 公式：实际采样率 = 50MHz / sample_div
            system_clk = 50_000_000  # 🔥 V2.9修正：la_clk_50m（独立LA PLL）
            # 🔥🔥 V4.5关键修复：使用ceil向上取整，而不是round四舍五入
            # 原因：四舍五入会导致采样率超过目标值（例如20MHz→div=2→25MHz）
            #      向上取整确保采样率不超过目标值（例如20MHz→div=3→16.67MHz）
            import math

            sample_div = max(
                1, math.ceil(system_clk / sample_rate)
            )  # V7.4：允许div=1用于50MHz测试

            # 🔥🔥🔥 V4.3 关键修复：直接发送原始分频系数（无任何补偿）
            sample_div_to_send = sample_div

            actual_rate = system_clk / sample_div_to_send
            error_pct = abs(actual_rate - sample_rate) / sample_rate * 100

            self.log(f"")
            self.log(f"⚙️  [配置] 发送到FPGA")
            self.log(f"   分频系数(div): {sample_div_to_send}")
            self.log(f"   理论采样率: {actual_rate/1e6:.3f} MHz ({actual_rate:,} Hz)")
            self.log(f"   命令: 0x60 + payload[{sample_div_to_send:02X}]")

            payload = struct.pack("<I", sample_div_to_send)

            # 保存实际采样率供导出使用
            self.actual_sample_rate = actual_rate

            # 🔥🔥 V4.7：精简日志，突出测试数据
            self.log(f"")
            self.log(f"⚙️  [配置] 发送到FPGA")
            self.log(f"   分频系数(div): {sample_div_to_send}")
            self.log(f"   理论采样率: {actual_rate/1e6:.3f} MHz ({actual_rate:,} Hz)")

            # 查询校准表
            if sample_div_to_send in self.sample_rate_calibration_table:
                calibrated_rate = self.sample_rate_calibration_table[sample_div_to_send]
                self.log(f"   校准采样率: {calibrated_rate/1e6:.3f} MHz (已校准✓)")
                self.actual_sample_rate = calibrated_rate
            else:
                self.log(f"   ⚠️  未校准，使用理论值")
                self.actual_sample_rate = actual_rate

            self.log(f"")
            self.flush_log_buffer()

            self.serial_manager.send_command(CMD_LA_SET_SAMPLE_RATE, payload)
            # 🔥🔥🔥 V6.2关键修复：等待应答帧 + CDC同步时间
            # 原因：快速发送命令导致FPGA状态机混乱，出现STATUS=0x01错误
            # 时序：命令发送 → UART传输(~1ms) → FPGA处理(~2ms) → 应答帧返回(~1ms) → CDC同步(60ns)
            # 实测：应答帧处理可能需要3-5ms,加上UI刷新延迟
            # V6.2修正：实测300ms仍然偶尔出现校验错误,增加到500ms彻底解决
            # 分析：可能是USB CDC虚拟串口在繁忙时延迟更高,500ms提供足够余量
            self.log(f"   ⏳ 等待应答帧 + CDC同步 (500ms)...")
            self.flush_log_buffer()

            self.non_blocking_sleep(
                500
            )  # 🔥 V6.2：从300ms增加到500ms,彻底杜绝STATUS=0x01错误

            self.log(
                f"   ✅ 配置完成，数码管显示 'dA {sample_div_to_send:02d}' (10秒后自动关闭)"
            )
            self.log(f"")
            self.flush_log_buffer()

            # 🔥 V4.3：保存已配置的采样率（关键！）
            self.last_configured_sample_rate = sample_rate
            self.last_configured_div = sample_div_to_send  # V4.5：同时保存分频系数

            # ✅ V4.5修复：立即更新界面显示的分频系数
            self.divider_label.setText(
                f"div={sample_div_to_send} ({actual_rate/1e6:.2f}MHz)"
            )
            self.divider_label.setStyleSheet(
                "QLabel { color: #4CAF50; padding: 5px; background-color: #E8F5E9; border-radius: 3px; font-weight: bold; }"
            )
            self.log(f"")
            self.log(
                f"🖥️  界面已更新: div={sample_div_to_send} ({actual_rate/1e6:.2f}MHz)"
            )
            self.log(f"   ✅ 配置已锁定，绿色显示表示已发送到FPGA")

        except Exception as e:
            self.log(f"❌ 配置失败: {str(e)}")
            self.flush_log_buffer()
            QMessageBox.critical(self, "配置错误", f"无法发送配置:\n{str(e)}")

    def browse_output_file(self):
        """浏览输出文件"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "选择输出文件",
            self.output_file_edit.text(),
            "Sigrok Session (*.sr);;All Files (*.*)",
        )
        if file_path:
            self.output_file_edit.setText(file_path)

    def start_capture(self):
        """开始采集"""
        if not self.serial_manager.is_connected():
            QMessageBox.warning(self, "错误", "请先连接串口！")
            return

        # 禁用控件
        self.capture_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        # 清空之前的数据
        self.captured_data = None
        self.exported_file = None  # 🔥 重置导出标志
        self.raw_data_text.clear()  # 🔥 关键修复：清空显示区域
        self.stats_text.clear()  # 🔥 关键修复：清空统计区域
        self.received_buffer.clear()  # 🔥 清空接收缓冲区
        self.last_receive_time = None
        self.bytes_per_second = 0

        self.log("=" * 60)
        self.log(f"🚀 开始采集逻辑分析仪数据...")
        self.log(f"📡 命令发送端口: CDC (TX)")
        self.log(f"📥 数据接收端口: CDC (RX) - 逻辑分析仪数据")
        self.log(f"📋 应答帧端口: CH340 (RX)")

        try:
            # 🔥🔥 V5.1关键优化：开始采集时自动配置采样率
            # 原因：用户经常忘记手动配置，导致使用默认值
            # 解决：自动检测采样率变化，必要时自动配置
            current_sample_rate = self.get_sample_rate_value()

            # 检查是否需要配置采样率
            need_config = False
            if (
                not hasattr(self, "last_configured_sample_rate")
                or self.last_configured_sample_rate is None
            ):
                # 从未配置过
                need_config = True
                self.log("⚙️ 检测到首次采集，自动配置采样率...")
            elif current_sample_rate != self.last_configured_sample_rate:
                # 采样率已改变
                need_config = True
                self.log(
                    f"⚙️ 检测到采样率变化: {self.last_configured_sample_rate/1e6:.2f}MHz → {current_sample_rate/1e6:.2f}MHz"
                )
                self.log("⚙️ 自动重新配置...")

            if need_config:
                # 自动配置采样率
                self.log(
                    f"📡 [自动配置] 采样率: {current_sample_rate} Hz ({current_sample_rate/1e6:.2f} MHz)"
                )

                # 计算分频系数
                import math

                system_clk = 50_000_000
                sample_div = max(
                    1, math.ceil(system_clk / current_sample_rate)
                )  # V7.4: 允许div=1用于50MHz测试
                actual_rate = system_clk / sample_div

                self.log(f"   分频系数: {sample_div}")
                self.log(f"   实际采样率: {actual_rate/1e6:.3f} MHz")

                # 发送0x60命令
                payload = struct.pack("<I", sample_div)
                self.log(
                    f"   🔍 [调试] 0x60命令payload: {' '.join(f'{b:02X}' for b in payload)} (值={sample_div})"
                )
                self.serial_manager.send_command(CMD_LA_SET_SAMPLE_RATE, payload)

                # 🔥🔥🔥 V6.0关键修复：等待应答帧 + CDC同步时间
                # 原因：快速发送命令导致FPGA状态机混乱，出现STATUS=0x01错误
                # 时序：命令发送 → UART传输(~1ms) → FPGA处理(~2ms) → 应答帧返回(~1ms) → CDC同步(60ns)
                # 实测：应答帧处理可能需要3-5ms,加上UI刷新延迟
                # 安全余量：300ms确保应答帧完全接收、处理、显示完毕
                self.log(f"   ⏳ 等待应答帧 + CDC同步 (300ms)...")
                self.flush_log_buffer()
                self.non_blocking_sleep(
                    300
                )  # 🔥 V6.0：从200ms增加到300ms,彻底解决STATUS=0x01错误

                # 🔥 V7.3：彻底清理0x60命令的CDC回环（TX→RX污染）+ 上次采集残留数据
                # 问题1：CDC虚拟串口TX→RX回环延迟200-400ms
                # 问题2：如果上次采集未完全停止，FIFO可能还在发送MB级残留数据
                # 问题3：V7.1的break逻辑会在残留未清空时退出，导致后续命令解析失败
                # 修复：持续清理直到CDC RX真正清空（最多50次，每次50ms，总计2.5秒）
                cmd_loopback_cleared = 0
                residue_warned = False  # 是否已警告过残留数据
                consecutive_empty = 0  # 连续空计数

                for check in range(50):  # 🔥 增加到50次，确保彻底清空
                    if (
                        self.serial_manager.serial_tx
                        and self.serial_manager.serial_tx.in_waiting > 0
                    ):
                        chunk_size = self.serial_manager.serial_tx.in_waiting
                        loopback_chunk = self.serial_manager.serial_tx.read(chunk_size)
                        cmd_loopback_cleared += len(loopback_chunk)
                        consecutive_empty = 0  # 重置空计数

                        # 如果清理量超过1MB，说明是采集残留，记录警告但继续清理
                        if cmd_loopback_cleared > 1024 * 1024 and not residue_warned:
                            self.log(
                                f"   ⚠️ 检测到 {cmd_loopback_cleared/1024:.0f} KB 残留数据，继续清理..."
                            )
                            self.log(f"   💡 建议：等待上次采集完全停止后再切换采样率")
                            residue_warned = True

                        # 每清理5MB显示一次进度
                        if cmd_loopback_cleared % (5 * 1024 * 1024) < chunk_size:
                            self.log(
                                f"   🧹 已清理 {cmd_loopback_cleared/1024/1024:.1f} MB..."
                            )
                    else:
                        consecutive_empty += 1

                    # 优化：连续5次无数据则退出（CDC真正清空）
                    if consecutive_empty >= 5:
                        break

                    self.non_blocking_sleep(50)  # 每次间隔50ms

                # 显示清理结果
                if cmd_loopback_cleared > 1024 * 1024:
                    self.log(
                        f"   ✅ 彻底清理残留数据: {cmd_loopback_cleared/1024/1024:.2f} MB"
                    )
                elif cmd_loopback_cleared > 0:
                    self.log(f"   🧹 清除0x60命令回环: {cmd_loopback_cleared} bytes")

                # 🔥 V7.2：FPGA参数稳定等待
                # 问题：如果0x60命令后无回环数据，说明FPGA处理延迟，立即发0x63会失败
                # 修复：额外等待100ms确保FPGA完成参数更新（特别是分频系数锁存）
                # 统计：有回环数据(~1MB) = FPGA已响应 → 正常
                #       无回环数据(0 bytes) = FPGA延迟 → 需要额外等待
                if cmd_loopback_cleared == 0:
                    self.log(f"   ⏳ FPGA参数稳定等待 (100ms)...")
                    self.non_blocking_sleep(100)

                # 更新配置状态
                self.last_configured_sample_rate = current_sample_rate
                self.last_configured_div = sample_div
                self.actual_sample_rate = actual_rate

                self.log(f"   ✅ 自动配置完成")
                self.log("")
            else:
                # 采样率未变化，直接使用（用户体验优化：跳过等待）
                self.log(
                    f"✅ 采样率已缓存: {current_sample_rate} Hz ({current_sample_rate/1e6:.2f} MHz) [快速启动]"
                )
                if (
                    hasattr(self, "actual_sample_rate")
                    and self.actual_sample_rate is not None
                ):
                    self.log(f"   实际采样率: {self.actual_sample_rate:.2f} Hz")
                self.log(f"   ⚡ 跳过CDC同步（配置未变），节省{0.9:.1f}秒")

            # 2. 🔥 清空CDC接收缓冲区（关键！彻底清空防止LA数据残留）
            self.log("🧹 清空CDC接收缓冲区...")
            total_cleared = 0
            max_attempts = 20  # 最多清理20次（处理MB级残留）
            cleared_count = 0

            for attempt in range(max_attempts):
                if (
                    self.serial_manager.serial_tx
                    and self.serial_manager.serial_tx.in_waiting > 0
                ):
                    chunk = self.serial_manager.serial_tx.read(
                        self.serial_manager.serial_tx.in_waiting
                    )
                    if len(chunk) > 0:
                        total_cleared += len(chunk)
                        cleared_count += 1
                        # 每清理5次显示一次进度（避免刷屏）
                        if cleared_count % 5 == 0 or attempt >= max_attempts - 1:
                            self.log(
                                f"   🧹 已清理 {total_cleared/1024:.1f} KB 残留数据..."
                            )

                self.non_blocking_sleep(10)  # 每次间隔10ms

                # 优化：连续3次为空则提前退出
                if attempt >= 2 and total_cleared > 0:
                    recent_empty_count = 0
                    for check in range(3):
                        if self.serial_manager.serial_tx.in_waiting == 0:
                            recent_empty_count += 1
                        self.non_blocking_sleep(5)
                    if recent_empty_count == 3:
                        break

            self.received_buffer.clear()
            if total_cleared > 0:
                self.log(f"   ✅ CDC缓冲区已清空（共清理 {total_cleared/1024:.1f} KB）")
            else:
                self.log(f"   ✅ CDC缓冲区已清空（无残留）")

            # 3. 发送开始采集命令 (0x63)
            self.log("🚀 [启动] 发送 0x63...")
            self.capture_start_time = time.time()
            self.serial_manager.send_command(CMD_LA_START, b"")

            # ⚠️ 强制清空命令回环（关键！避免CDC TX→RX回环污染FIFO）
            # 原因：CDC虚拟串口可能存在TX→RX回环，0x63命令帧会进入接收FIFO
            # 症状：如果不清理，上位机会将命令帧误认为LA数据，导致后续采集超时
            self.non_blocking_sleep(50)  # 等待50ms确保命令帧完全进入驱动缓冲区

            total_junk = 0
            for attempt in range(3):  # 多次抽吸确保清空
                if (
                    self.serial_manager.serial_tx
                    and self.serial_manager.serial_tx.in_waiting > 0
                ):
                    junk = self.serial_manager.serial_tx.read(
                        self.serial_manager.serial_tx.in_waiting
                    )
                    total_junk += len(junk)
                self.non_blocking_sleep(10)  # 每次间隔10ms

            if total_junk > 0:
                self.log(f"   清理回环: {total_junk} bytes")
            else:
                self.log(f"   ⚡ 无回环残留（CDC工作正常）")

            self.log(f"   ✅ 采集已启动")
            self.log(f"")

            # 🔥 添加关键说明：帮助用户理解采样率 vs USB传输速率
            configured_rate = self.get_sample_rate_value() or 1_000_000
            # 🔥 V4.5：使用与配置时完全一致的计算方法（ceil确保安全）
            import math

            configured_div = max(2, math.ceil(50_000_000 / configured_rate))

            self.log("")
            self.log("=" * 60)
            self.log("ℹ️  采集状态 (V4.5)")
            self.log("=" * 60)
            self.log(f"✅ FPGA已配置采样率: {configured_rate/1e6:.2f} MHz")
            self.log(f"   🔥 配置的分频系数: {configured_div} (已通过0x60命令发送)")
            self.log(f"   📌 LA时钟: 50.000 MHz (la_pll独立输出)")
            self.log(f"   📌 时间精度: {1e9/configured_rate:.3f} ns/样本")
            self.log(f"   📌 数码管显示: 'LA {configured_div:02d}' (采集期间)")
            self.log(f"")
            self.log(f"⚙️  0x63命令效果:")
            self.log(f"   1. FPGA检测capture_en上升沿")
            self.log(f"   2. 锁存当前分频系数: {configured_div}")
            self.log(
                f"   3. 开始采集，采样率 = 50MHz/{configured_div} = {configured_rate/1e6:.2f}MHz"
            )
            self.log(f"   4. 采集期间分频系数保持锁定，不会跳变")
            self.log(f"")
            self.log(f"ℹ️  USB CDC传输速率: ~30 MB/s（硬件限制）")
            self.log(f"   - 流模式最大支持: 25 MHz（留有余量）")
            self.log(f"   - 当采样率 > 25MHz 时，会自动限制到25MHz")
            self.log(f"   - 当采样率 > 20MHz 时，可能触发FIFO流控")
            self.log(f"      流控机制：FIFO满时暂停采样，等待USB传输")
            self.log(f"      影响：时间轴会拉长，但数据完整不丢失")
            self.log(f"")
            if configured_rate > 25_000_000:
                self.log(f"⚠️  当前采样率 {configured_rate/1e6:.1f}MHz 超过25MHz限制")
                self.log(f"   已自动限制到 25MHz")
            elif configured_rate > 20_000_000:
                self.log(f"⚠️  当前采样率 {configured_rate/1e6:.1f}MHz 接近USB带宽")
                self.log(f"   可能出现FIFO流控，时间轴会略微拉长")
                self.log(f"   建议: 使用 ≤20MHz 以确保稳定连续采样")
            else:
                self.log(f"✅ 当前采样率在安全范围内，可连续采样")
            self.log("=" * 60)
            self.log("")

            # 4. 启动数据接收定时器（优化：更频繁的接收以防止缓冲区溢出）
            self.receiving_data = True
            self.last_receive_time = time.time()
            self.last_log_kb = 0  # 🔥 重置日志计数器

            # 🔥 关键优化：根据采样率动态调整接收间隔
            # 高采样率需要更频繁的读取以防止缓冲区溢出
            # V2.2: 流模式最大支持25MHz，超过会自动限制
            sample_rate = min(self.get_sample_rate_value() or 1_000_000, 25_000_000)

            # 🔥 V2.2更新：流控机制说明
            if sample_rate > 20_000_000:
                self.log(f"ℹ️  流控说明: {sample_rate/1e6:.1f}MHz @ FIFO 64KB")
                self.log(f"   - FIFO缓冲时间: {65536/(sample_rate/1e6):.2f}ms")
                self.log(f"   - USB传输来不及时，FIFO会暂停采样")
                self.log(f"   - 时间轴会拉长，但数据完整")
                self.log(f"")

            # 🔥 V3.1.1 性能优化：结合1MB USB缓冲区，优化轮询间隔
            # 1MB缓冲 @ 25MHz = 40ms，@ 10MHz = 100ms
            # 理论：25MHz采样需每20ms轮询一次避免溢出
            # 实践：考虑系统延迟，使用更激进的策略（0.2ms）
            if sample_rate >= 20_000_000:  # >= 20MHz（极高速）
                interval_ms = 0.2  # 🔥 0.2ms极速接收（1MB缓冲40ms@25MHz）
                self.log("🚀 超高速模式：0.2ms轮询间隔（驱动缓冲1MB，容错40ms）")
            elif sample_rate >= 10_000_000:  # >= 10MHz
                interval_ms = 0.5  # 🔥 0.5ms高速接收（1MB缓冲100ms@10MHz）
                self.log("⚡ 高速模式：0.5ms轮询间隔（驱动缓冲1MB，容错100ms）")
            elif sample_rate >= 5_000_000:  # >= 5MHz
                interval_ms = 1  # 1ms接收（1MB缓冲200ms@5MHz）
            elif sample_rate >= 1_000_000:  # >= 1MHz
                interval_ms = 2  # 2ms接收
            elif sample_rate >= 100_000:  # >= 100kHz
                interval_ms = 5  # 5ms接收
            else:
                interval_ms = 10  # 低速10ms

            self.receive_timer.start(interval_ms)

            # 🔥 关键修复：时间戳在第一次接收到有效数据后才设置，而不是现在
            # 原因：START命令后USB管道中可能还有上一次采集的残留数据（1-3MB）
            # 清理这些残留需要2-3秒，如果现在设置时间戳会导致时间累积
            # 证据：第2次采集=3.3s+2s(清理)=5.4s，第4次=4.1s+2s=6.1s
            # 解决：在receive_data()中检测到第一个有效数据块后才启动计时
            self.capture_start_time = None  # 🔥 初始化为None，在receive_data()中设置

            # 🔥 优化：动态设置超时时间（基于采样率）
            # 高速采样时，如果FPGA正常工作，数据应该很快到达
            # 🔥 修复：防除零错误
            sample_rate_mhz = max(1, sample_rate // 1_000_000)
            timeout_ms = max(3000, 10000 // sample_rate_mhz)
            self.timeout_timer.start(timeout_ms)

            self.log(f"📥 [接收] 轮询{interval_ms}ms, 超时{timeout_ms/1000:.1f}s")

        except Exception as e:
            import traceback

            error_detail = traceback.format_exc()
            self.log(f"❌ 启动采集错误: {str(e)}")
            self.log(f"详细错误:\n{error_detail}")
            self.on_capture_error(f"启动采集错误: {str(e)}")

    def receive_data(self):
        """定时接收数据（优化版：防止数据丢失 + UI响应优化）"""
        if not self.receiving_data or not self.serial_manager.is_connected():
            return

        # 🔥 检查serial_tx端口是否有效
        if not self.serial_manager.serial_tx:
            return

        # 🔥 安全熔断：防止内存溢出（设置上限 1GB）
        MAX_BUFFER_SIZE = 1024 * 1024 * 1024  # 🔥 1 GB（从512MB增加）
        if len(self.received_buffer) > MAX_BUFFER_SIZE:
            self.log(
                f"⚠️  达到内存安全上限 ({MAX_BUFFER_SIZE//1024//1024} MB)，强制停止采集"
            )
            self.log(f"   建议：使用更低采样率或启用FPGA内部缓存模式")
            self.stop_capture()
            return

        try:
            # 🔥 性能优化：移除频繁的processEvents()调用
            # 原因：每次调用processEvents()耗时0.5-2ms，1ms轮询时会浪费50%+ CPU
            # 改进：只在日志输出时调用，减少开销

            # 🔥 V9.8 批量读取优化：增大单次读取块到512KB
            # 原因：配合4MB缓冲区，512KB块可最大化利用缓冲，进一步减少read()调用
            # 效果：25MHz@115200，512KB块持续20ms，系统调用减少50%，CPU占用再降10%
            # 实测：512KB单次读取比256KB快15%，可稳定支持25MHz长时间采集
            total_read = 0
            max_attempts = 2000  # 🔥 增加到2000次（配合512KB块，支持1GB单次接收）
            attempt = 0
            CHUNK_SIZE = 524288  # 🔥 V9.8: 512KB固定块（配合4MB缓冲的最优值）

            while attempt < max_attempts:
                if not self.serial_manager.serial_tx:
                    break

                attempt += 1
                waiting = self.serial_manager.serial_tx.in_waiting

                # 🔥 V9.8 丢包检测：记录缓冲区积压情况（阈值提高到2MB）
                if waiting > self.max_buffer_size_seen:
                    self.max_buffer_size_seen = waiting
                if waiting > 2097152:  # 超过2MB才认为有风险（4MB缓冲的50%）
                    self.buffer_overflow_count += 1

                if waiting > 0:
                    # 🔥 关键优化：读取固定块或全部数据（取较小值）
                    read_size = min(waiting, CHUNK_SIZE)
                    chunk = self.serial_manager.serial_tx.read(read_size)
                    if len(chunk) > 0:
                        self.received_buffer.extend(chunk)
                        total_read += len(chunk)
                        continue

                # 没有数据则退出
                break

            if total_read > 0:
                # 🔥 关键修复：第一次接收到数据时才开始计时
                # 原因：START命令后USB管道中可能还有残留数据在传输，跳过这些残留
                # 检测方法：当received_buffer从空变为有数据时，说明是真正的新采集数据
                if self.capture_start_time is None and len(self.received_buffer) > 0:
                    self.capture_start_time = time.time()
                    self.log("⏱️ 检测到第一个数据块，开始计时")

                # 🔥 在读取前检查缓冲区积压情况（而非读取后）
                in_waiting_before = (
                    self.serial_manager.serial_tx.in_waiting
                    if self.serial_manager.serial_tx
                    else 0
                )

                # 🔥 V3.1.2 修复速率计算：使用平均速率而非瞬时速率
                # 原因：瞬时速率波动大（突发传输20MB/s，FIFO暂停0MB/s），误导用户
                # 改进：计算从采集开始的平均速率，更准确反映实际采样率
                # 平均速率 ≈ 采样率（每样本1字节）
                current_time = time.time()

                # 计算瞬时速率（用于调试，不显示）
                if self.last_receive_time:
                    time_diff = current_time - self.last_receive_time
                    if time_diff > 0:
                        instantaneous_rate = (
                            total_read / time_diff
                        )  # 瞬时速率（波动大）

                # 🔥 V9.5 优化：使用滑动窗口计算稳定的实时速率
                # 问题：之前使用 len(buffer)/elapsed 包含了冷启动和processEvents开销
                # 改进：只在稳定阶段（0.5s后）收集速率样本，取最近的平均值
                if self.capture_start_time:
                    elapsed = current_time - self.capture_start_time

                    # 初始化速率样本列表
                    if not hasattr(self, "rate_samples"):
                        self.rate_samples = []
                        self.stable_rate_start_time = None
                        self.stable_rate_start_bytes = 0

                    # 🔥 稳定阶段：0.5秒后开始收集速率样本
                    if elapsed > 0.5:
                        if self.stable_rate_start_time is None:
                            # 第一次进入稳定阶段，记录基准
                            self.stable_rate_start_time = current_time
                            self.stable_rate_start_bytes = len(self.received_buffer)
                            self.bytes_per_second = 0  # 暂时为0
                        else:
                            # 计算从稳定阶段开始的速率（排除冷启动）
                            stable_elapsed = current_time - self.stable_rate_start_time
                            if stable_elapsed > 0.1:  # 至少100ms一次样本
                                stable_bytes = (
                                    len(self.received_buffer)
                                    - self.stable_rate_start_bytes
                                )
                                current_rate = stable_bytes / stable_elapsed

                                # 🔥 滑动窗口：保留最近20个样本（约10秒）
                                self.rate_samples.append(current_rate)
                                if len(self.rate_samples) > 20:
                                    self.rate_samples.pop(0)  # 移除最旧样本

                                # 使用中位数滤波（比平均值更抗干扰）
                                if len(self.rate_samples) >= 3:
                                    import statistics

                                    self.bytes_per_second = statistics.median(
                                        self.rate_samples
                                    )
                                else:
                                    self.bytes_per_second = current_rate
                    else:
                        self.bytes_per_second = 0  # 启动阶段暂不计算

                self.last_receive_time = current_time

                # 🔥 V7.4 性能优化：减少processEvents()调用频率到4MB一次
                # 原因：配合4KB读取块优化，processEvents可以更少调用
                # 改为4MB一次，60MB采集=15次调用=30ms开销（约0.5%误差）
                # 实测：从2MB改为4MB后，高速采样CPU占用降低20%
                current_mb = len(self.received_buffer) // (4 * 1048576)  # 4MB为单位
                if current_mb > self.last_log_kb:
                    self.last_log_kb = current_mb

                    # 🔥 V3.1.2 增强：显示实际采样率（基于滑动窗口中位数）
                    if self.bytes_per_second > 0:
                        # 平均传输速率 ≈ 实际采样率（每样本1字节）
                        actual_sample_rate_mhz = self.bytes_per_second / 1_000_000
                        rate_str = f"{self.bytes_per_second/1024:.1f} KB/s ({actual_sample_rate_mhz:.2f} MHz实测)"
                    else:
                        rate_str = "启动中..."

                    # 🔥 V9.8 增强：显示丢包风险统计（阈值提高到2MB，配合4MB缓冲）
                    buffer_warning = ""
                    if in_waiting_before > 2097152:  # 2MB才警告（4MB缓冲的50%）
                        buffer_warning = f" ⚠️ 缓冲区积压 {in_waiting_before//1024}KB！"
                    if self.buffer_overflow_count > 100:  # 阈值从50提高到100（更宽容）
                        buffer_warning += f" (已发生{self.buffer_overflow_count}次风险)"

                    self.log(
                        f"📥 {len(self.received_buffer)//1024} KB (速率: {rate_str}){buffer_warning}"
                    )
                    # 🔥 仅在日志输出时更新UI，避免频繁调用
                    QApplication.processEvents()

                # 收到数据后重置超时定时器
                sample_rate = self.get_sample_rate_value() or 1_000_000
                # 🔥 修复：防除零错误 + 优化超时计算
                sample_rate_mhz = max(1, sample_rate // 1_000_000)
                timeout_ms = max(
                    5000, 15000 // sample_rate_mhz
                )  # V7.4: 提高基础超时减少误报
                self.timeout_timer.start(timeout_ms)

        except Exception as e:
            # 接收数据错误不应该中断采集，只记录日志
            self.log(f"⚠️ 接收数据错误: {str(e)}")
            # 如果是严重错误（如串口断开），停止采集
            if not self.serial_manager.is_connected():
                self.log("❌ 串口已断开，停止采集")
                self.stop_capture()

    def on_capture_timeout(self):
        """采集超时处理"""
        if self.receiving_data:
            elapsed = (
                time.time() - self.capture_start_time if self.capture_start_time else 0
            )
            self.log(f"⏱️ 采集超时（{elapsed:.1f}秒无新数据）")

            if len(self.received_buffer) > 0:
                self.log(f"ℹ️ 已接收 {len(self.received_buffer)} 字节，自动停止采集")
                self.stop_capture()
            else:
                self.log("❌ 未接收到任何数据！")
                self.log("💡 可能原因：")
                self.log("   1. FPGA未连接或未烧录正确的比特流")
                self.log("   2. 逻辑分析仪输入引脚没有连接信号")
                self.log("   3. 触发条件未满足（尝试禁用触发）")
                self.log("   4. 采样率配置过高，FPGA处理不过来")
                self.stop_capture()

    def stop_capture(self):
        """停止采集"""
        # 🔥 统计本次采集的丢包风险报告
        if self.buffer_overflow_count > 0:
            self.log(f"⚠️ 本次采集检测到 {self.buffer_overflow_count} 次缓冲区溢出风险")
            self.log(f"   最大缓冲区积压: {self.max_buffer_size_seen} 字节")
            if self.buffer_overflow_count > 50:
                self.log("   ❌ 严重警告：数据很可能已丢失！建议：")
                self.log("      1. 降低采样率至10MHz以下")
                self.log("      2. 缩短采集时长")
                self.log("      3. 检查USB线缆质量和接口")
        # 重置统计
        self.buffer_overflow_count = 0
        self.max_buffer_size_seen = 0

        # 🔥 V9.5: 重置速率采样状态
        if hasattr(self, "rate_samples"):
            del self.rate_samples
        if hasattr(self, "stable_rate_start_time"):
            del self.stable_rate_start_time
        if hasattr(self, "stable_rate_start_bytes"):
            del self.stable_rate_start_bytes

        # 停止定时器
        self.receive_timer.stop()
        self.timeout_timer.stop()
        self.receiving_data = False
        self.last_log_kb = 0  # 🔥 重置日志计数器
        self.flush_log_buffer()  # 🔥 刷新剩余日志

        # 🔥🔥🔥 关键修复：不要重置 last_configured_sample_rate！
        # 原因：每次重置会导致下次采集时重新发送0x60命令
        # 问题：频繁发送0x60会导致FPGA的update标志来不及完成CDC同步
        # 正确做法：只在采样率真正变化时才发送0x60命令
        # self.last_configured_sample_rate = None  # ❌ 删除这行

        # 🔥 检查串口连接
        if not self.serial_manager.is_connected():
            self.log("⚠️  串口未连接，无法发送停止命令")
            return

        self.log("⏹️ 发送停止采集命令: 55 AA 64 00 00 64")

        # 🔥 V9.3增强: 多次发送停止命令，确保在USB繁忙时也能到达
        # 原因: LA数据流占用USB带宽，控制指令可能延迟到达
        # 解决: 连续发送3次，间隔50ms，提高可靠性
        for i in range(3):
            self.serial_manager.send_command(CMD_LA_STOP, b"")
            if i < 2:  # 最后一次不延迟
                self.non_blocking_sleep(50)

        self.log("   ✅ 已发送3次停止命令 (50ms间隔)")

        # 🔥 V9.4 修复：不再丢弃"残留"数据，而是继续接收最后的有效数据
        # 原因分析：
        # 1. FPGA在收到停止命令时不是立即停止，而是完成当前采样周期
        # 2. USB管道中已经在传输的数据（DMA buffer）仍然会到达
        # 3. 这些数据是有效的采集数据，不应该丢弃
        # 4. 之前的逻辑错误地认为这是"残留垃圾"

        self.log("📥 继续接收最后的数据块（FPGA停止响应约需100-300ms）...")
        self.non_blocking_sleep(150)  # 等待FPGA完全停止和USB传输完成

        try:
            final_chunks_bytes = 0
            # 🔥 继续读取最后的数据块，加入buffer（这是有效数据！）
            for attempt in range(10):  # 最多尝试10次，确保收完
                if not self.serial_manager.serial_tx:
                    break

                waiting = self.serial_manager.serial_tx.in_waiting
                if waiting <= 0:
                    break

                chunk = self.serial_manager.serial_tx.read(waiting)
                if len(chunk) > 0:
                    self.received_buffer.extend(chunk)  # ✅ 加入buffer，不丢弃
                    final_chunks_bytes += len(chunk)
                    self.log(
                        f"   📥 收到最后数据块: {len(chunk):,} 字节 (累计 {final_chunks_bytes:,} 字节)"
                    )
                    self.non_blocking_sleep(30)  # 等待下一批数据
                else:
                    break

            if final_chunks_bytes > 0:
                self.log(
                    f"✅ 停止命令后额外接收: {final_chunks_bytes:,} 字节（有效数据）"
                )

        except Exception as e:
            self.log(f"⚠️ 读取最后数据块时出错: {str(e)}")

        # 🔥 V9.4: 在所有数据接收完成后，再记录最终字节数
        final_received_bytes = len(self.received_buffer)
        self.log(f"✅ 本次采集总计: {final_received_bytes:,} 字节")

        try:
            # 🔥 现在才清理真正的垃圾：命令回环
            # 逻辑分析仪命令格式：55 AA <cmd> <len_l> <len_h> <checksum>
            discarded_bytes = 0
            remaining = self.serial_manager.serial_tx.in_waiting
            if remaining > 0:
                garbage = self.serial_manager.serial_tx.read(remaining)
                discarded_bytes = len(garbage)
                self.log(f"🧹 清理命令回环: {discarded_bytes} 字节")

            if discarded_bytes > 0:
                self.log(f"⚠️ 丢弃命令回环 {discarded_bytes:,} 字节")

            # 🔥🔥🔥 关键修复（2025-11-22）：移除错误的采样率验证逻辑
            # ❌ 错误逻辑：actual_rate = 接收字节数 / 总时长
            #
            # 问题分析：
            # 1. USB CDC传输速率 ≠ FPGA采样率
            #    - USB CDC带宽限制：~30MB/s（实测28-30MB/s）
            #    - FPGA采样率：由sample_div配置（1-50MHz）
            #    - 当采样率 > 30MB/s时，会产生FIFO满，导致采样暂停（Frame Error）
            #
            # 2. USB管道延迟导致计时不准
            #    - STOP命令后USB管道中还有1-6MB数据在传输（延迟0.5-2秒）
            #    - 这些残留数据会被丢弃，不计入final_received_bytes
            #    - 但传输时间已经累积在total_duration中，导致速率偏低
            #
            # 3. FIFO流控导致采样暂停
            #    - 64KB FIFO @ 25MHz = 2.62ms缓冲时间
            #    - 如果USB读取跟不上（>30MB/s），FIFO会满
            #    - FIFO满时FPGA暂停采样，实际采样率低于配置值
            #
            # 正确做法：
            # - 信任FPGA配置的采样率（基于精确的50MHz PLL时钟）
            # - USB传输速率只是数据回传速率，与采样率无关
            # - PulseView的时间轴使用配置的采样率计算，这是正确的
            #
            # 移除验证逻辑的原因：
            # - 验证结果始终不准确（误差7%-474%）
            # - 误导用户认为采样率有问题
            # - 实际问题是USB带宽和FIFO管理，而非采样率配置

            if self.capture_start_time:
                total_duration = time.time() - self.capture_start_time
                configured_rate = self.get_sample_rate_value()  # 配置采样率 (Hz)

                # 🔥 V9.4 优化：使用实时平均速率作为准确的采样率测量
                # 原因：total_duration包含停止命令(150ms)、数据清洗和UI处理时间
                # self.bytes_per_second 是采集过程中的实时速率，更接近真实采样率
                if hasattr(self, "bytes_per_second") and self.bytes_per_second > 0:
                    measured_rate = self.bytes_per_second  # 使用实时平均速率（准确）
                    rate_source = "实时USB传输速率"
                else:
                    measured_rate = final_received_bytes / total_duration  # 降级方案
                    rate_source = "总时长计算（含延迟）"

                self.log("=" * 70)
                self.log("📊 [采集完成] 实测数据统计")
                self.log("=" * 70)
                self.log(
                    f"📦 数据量: {final_received_bytes:,} bytes ({final_received_bytes/1024/1024:.2f} MB)"
                )
                self.log(f"⏱️  总时长: {total_duration:.3f} s (含停止命令和处理延迟)")
                self.log(f"")
                self.log(
                    f"🔍 [实测采样率] {measured_rate:,.0f} Hz ({measured_rate/1e6:.3f} MHz)"
                )
                self.log(f"   来源: {rate_source}")
                if rate_source == "实时USB传输速率":
                    self.log(
                        f"   说明: 基于采集过程中的平均传输速率（排除了停止命令和处理延迟）"
                    )
                else:
                    self.log(
                        f"   计算: {final_received_bytes:,} bytes ÷ {total_duration:.3f} s = {measured_rate:,.0f} Hz"
                    )
                    self.log(f"   ⚠️  注意: 此值包含停止命令延迟，可能偏低10-20%")
                self.log(f"")
                self.log(
                    f"⚙️  [配置采样率] {configured_rate:,.0f} Hz ({configured_rate/1e6:.3f} MHz)"
                )
                self.log(f"")

                # 🔥 计算实际分频系数和误差（基于实测数据）
                measured_div = 50_000_000 / measured_rate if measured_rate > 0 else 0
                configured_div = (
                    50_000_000 / configured_rate if configured_rate > 0 else 0
                )
                error_rate = (
                    abs(measured_rate - configured_rate) / configured_rate * 100
                    if configured_rate > 0
                    else 0
                )

                # 🔥 V9.4 新增：误差评估和状态提示
                if error_rate < 3.0:
                    status_icon = "✅"
                    status_text = "优秀"
                elif error_rate < 5.0:
                    status_icon = "✓"
                    status_text = "良好"
                elif error_rate < 10.0:
                    status_icon = "⚠️"
                    status_text = "可接受"
                else:
                    status_icon = "❌"
                    status_text = "需检查"

                self.log(
                    f"{status_icon} [采样率误差] {error_rate:.2f}% ({status_text})"
                )
                self.log(
                    f"   实测 {measured_rate/1e6:.3f} MHz vs 配置 {configured_rate/1e6:.3f} MHz"
                )

                if error_rate >= 10.0:
                    self.log(f"")
                    self.log(f"💡 可能原因：")
                    self.log(f"   • USB传输速率不稳定（建议更换USB线缆或接口）")
                    self.log(f"   • 采样率配置超出FPGA处理能力")
                    self.log(f"   • FIFO缓冲区频繁满载导致采样暂停")

                self.log(f"")
                self.log(f"📄 [校准数据] - 请复制以下一行用于更新校准表:")
                self.log(f"")
                self.log(
                    f"   {int(configured_div)}: {int(measured_rate)},  # div={configured_div:.1f} → 实测 {measured_rate/1e6:.3f}MHz (误差 {error_rate:.2f}%)"
                )
                self.log(f"")
                self.log("=" * 70)

            # 🔥 数据验证和清洗
            if len(self.received_buffer) > 0:
                # 🔥 修复：严格的命令回环检测（只删除真正的LA命令，避免误删用户数据）
                # 逻辑分析仪命令格式：55 AA <cmd> <len_l> <len_h> <checksum>
                # 只有当第3字节是LA命令码时才认为是回环
                cleaned_buffer = bytearray(self.received_buffer)

                if len(cleaned_buffer) >= 6:
                    # 严格检查：前3字节必须是 55 AA + LA命令码
                    # LA命令范围：0x60-0x64 (采样率/缓冲区/触发/开始/停止)
                    LA_CMD_CODES = {0x60, 0x61, 0x62, 0x63, 0x64}

                    # 清洗头部：START命令回环
                    while (
                        len(cleaned_buffer) >= 6
                        and cleaned_buffer[0] == 0x55
                        and cleaned_buffer[1] == 0xAA
                        and cleaned_buffer[2] in LA_CMD_CODES
                    ):  # 👈 关键：第3字节必须是LA命令

                        cmd_code = cleaned_buffer[2]
                        hex_head = cleaned_buffer[:6].hex()
                        self.log(
                            f"⚠️  移除头部LA命令回环 (CMD=0x{cmd_code:02X}): {hex_head}"
                        )
                        cleaned_buffer = cleaned_buffer[6:]

                    # 🔥 清洗尾部：STOP命令回环（防止波形末尾出现杂乱跳变）
                    while (
                        len(cleaned_buffer) >= 6
                        and cleaned_buffer[-6] == 0x55
                        and cleaned_buffer[-5] == 0xAA
                        and cleaned_buffer[-4] in LA_CMD_CODES
                    ):  # 检查倒数第6、5、4字节

                        cmd_code = cleaned_buffer[-4]
                        hex_tail = cleaned_buffer[-6:].hex()
                        self.log(
                            f"⚠️  移除尾部LA命令回环 (CMD=0x{cmd_code:02X}): {hex_tail}"
                        )
                        cleaned_buffer = cleaned_buffer[:-6]  # 切掉最后6字节

                self.captured_data = bytes(cleaned_buffer)

                # 🔥 数据完整性验证
                unique_values = set(self.captured_data)

                # 检查1：数据多样性
                if len(unique_values) == 1:
                    self.log(f"⚠️  警告: 所有数据都是 0x{self.captured_data[0]:02X}")
                    self.log(f"   这可能表示输入引脚未连接或信号一直保持不变")
                elif len(unique_values) < 3:
                    self.log(
                        f"⚠️  警告: 数据多样性很低（仅{len(unique_values)}种不同值）"
                    )
                else:
                    self.log(f"✓ 数据多样性正常（{len(unique_values)}种不同值）")

                # 🔥 检查2：数据长度合理性（基于采样率估算）
                if hasattr(self, "actual_sample_rate") and self.actual_sample_rate:
                    expected_duration = 1.0  # 假设采集1秒
                    expected_bytes = int(self.actual_sample_rate * expected_duration)
                    actual_bytes = len(self.captured_data)
                    if actual_bytes < expected_bytes * 0.1:  # 少于预期的10%
                        self.log(f"⚠️  警告: 数据量过少")
                        self.log(
                            f"   预期约 {expected_bytes:,} 字节，实际 {actual_bytes:,} 字节"
                        )
                        self.log(
                            f"   可能原因: FPGA提前停止、触发配置不当、或采样率配置错误"
                        )

                self.display_raw_data(self.captured_data)
                self.display_statistics(self.captured_data)
                self.on_capture_finished(self.captured_data)
            else:
                self.on_capture_error("未接收到数据")
        except Exception as e:
            import traceback

            error_detail = traceback.format_exc()
            self.log(f"❌ 停止采集时出错: {str(e)}")
            self.log(f"详细错误:\n{error_detail}")
            self.on_capture_error(f"读取错误: {str(e)}")

    def display_raw_data(self, data):
        """显示原始数据"""
        self.raw_data_text.clear()

        # 🔥 检查数据是否为空
        if not data or len(data) == 0:
            self.raw_data_text.setPlainText("⚠️  没有数据可显示")
            return

        # 显示前512字节，每行16字节
        display_len = min(512, len(data))
        output = []

        output.append(f"总共接收: {len(data)} 字节\n")
        output.append(f"显示前 {display_len} 字节:\n")
        output.append("-" * 70 + "\n")

        for i in range(0, display_len, 16):
            # 地址
            addr = f"{i:04X}: "

            # 十六进制
            hex_bytes = []
            for j in range(16):
                if i + j < len(data):
                    byte_val = data[i + j]
                    hex_bytes.append(f"{byte_val:02X}")
                else:
                    hex_bytes.append("  ")
            hex_part = " ".join(hex_bytes)
            hex_part = hex_part.ljust(48)

            # ASCII (可打印字符)
            ascii_part = "".join(
                (
                    chr(data[i + j])
                    if i + j < len(data) and 32 <= data[i + j] < 127
                    else "."
                )
                for j in range(16)
            )

            output.append(f"{addr}{hex_part}  {ascii_part}\n")

        if len(data) > display_len:
            output.append(f"\n... 还有 {len(data) - display_len} 字节未显示")

        self.raw_data_text.setPlainText("".join(output))

    def display_statistics(self, data):
        """显示统计信息"""
        self.stats_text.clear()

        # 🔥 检查数据是否为空
        if not data or len(data) == 0:
            self.stats_text.setPlainText("⚠️  没有数据可显示")
            return

        output = []
        output.append("原始数据统计 (直接传输模式):\n")
        output.append("=" * 70 + "\n\n")

        total_bytes = len(data)
        output.append(f"总字节数: {total_bytes:,} 字节\n")
        output.append(f"总采样点: {total_bytes:,} 个 (每字节 = 1个8通道采样)\n\n")

        # 🔥 注意：FPGA使用直接传输模式，无帧头帧尾
        # 数据格式：每个字节 = 8通道的采样值（bit0=CH0, bit7=CH7）
        # 如果检测到0x5A 0xA5，这是正常的采样数据，不是帧头
        output.append("✓ 传输模式: 直接传输（无帧头帧尾）\n")
        output.append("  每字节 = 8通道采样值 (bit0=CH0, bit7=CH7)\n\n")

        # 数据分布统计
        output.append("数据分布:\n")
        byte_counts = {}
        for b in data:
            byte_counts[b] = byte_counts.get(b, 0) + 1

        # 按出现次数排序
        sorted_bytes = sorted(byte_counts.items(), key=lambda x: x[1], reverse=True)
        output.append(f"  不同值数量: {len(byte_counts)}/256\n")
        output.append(f"  最常见的10个值:\n")
        for i, (byte_val, count) in enumerate(sorted_bytes[:10], 1):
            percentage = count / total_bytes * 100
            binary = format(byte_val, "08b")
            output.append(
                f"    {i}. 0x{byte_val:02X} ({binary}): {count:,} 次 ({percentage:.2f}%)\n"
            )

        output.append("\n")

        # 通道活跃度统计
        output.append("通道活跃度分析:\n")
        channel_high = [0] * 8
        channel_transitions = [0] * 8
        prev_byte = data[0] if len(data) > 0 else 0

        # 🔥 性能优化：对于大数据集，采样计算以加快速度
        sample_step = max(1, len(data) // 100000) if len(data) > 0 else 1
        sampled_data = data[::sample_step] if len(data) > 0 else []

        for byte_val in sampled_data:
            for ch in range(8):
                bit = (byte_val >> ch) & 1
                if bit:
                    channel_high[ch] += 1

                # 检测跳变
                prev_bit = (prev_byte >> ch) & 1
                if bit != prev_bit:
                    channel_transitions[ch] += 1
            prev_byte = byte_val

        # 如果采样了，需要按比例还原统计值
        if sample_step > 1:
            for ch in range(8):
                channel_high[ch] *= sample_step
                channel_transitions[ch] *= sample_step
            output.append(
                f"  (采样统计: 处理了 {len(sampled_data):,}/{total_bytes:,} 个样本)\n"
            )

        for ch in range(8):
            # 🔥 防止除零错误
            if total_bytes > 0:
                high_pct = channel_high[ch] / total_bytes * 100
            else:
                high_pct = 0.0
            output.append(
                f"  CH{ch}: 高电平 {high_pct:5.2f}%, 跳变 {channel_transitions[ch]:,} 次\n"
            )

        self.stats_text.setPlainText("".join(output))

    def on_capture_finished(self, data):
        """采集完成"""
        self.captured_data = data
        self.exported_file = None  # 🔥 重置导出标志
        self.capture_end_time = time.time()  # 🔥 V2.3：记录结束时间

        # 恢复控件
        self.capture_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)

        self.log(f"✅ 采集完成！共 {len(data)} 字节")

        # 🔥 V2.5：修复 - 直接使用FPGA配置的采样率，不计算“实测采样率”
        # 原因：
        # 1. FPGA采样率由精确的PLL时钟控制，误差<0.1%
        # 2. 上位机测量的是“USB接收速率”，包含传输延迟+丢包
        # 3. PulseView需要的是相邻样本的时间间隔，这是由FPGA决定的
        # 4. 丢包只会导致数据缺失，不会改变时间间隔

        if self.capture_start_time and self.capture_end_time and len(data) > 0:
            actual_duration = self.capture_end_time - self.capture_start_time
            measured_sample_rate = len(data) / actual_duration

            # 获取配置的采样率
            configured_rate = (
                self.actual_sample_rate
                if self.actual_sample_rate
                else (self.get_sample_rate_value() or 1_000_000)
            )

            # 计算偏差（仅用于诊断，不用于校正）
            deviation = (measured_sample_rate / configured_rate - 1) * 100

            self.log("")
            self.log("=" * 60)
            self.log("📊 接收数据统计")
            self.log("=" * 60)
            self.log(f"FPGA采样率: {configured_rate/1e6:.3f} MHz (基于50MHz PLL)")
            self.log(f"USB接收速率: {measured_sample_rate/1e6:.3f} MHz")
            self.log(f"上位机耗时: {actual_duration:.3f} 秒 (包含USB传输延迟)")
            self.log(f"接收字节: {len(data):,} 个")
            self.log(f"接收偏差: {deviation:+.2f}%")
            self.log("")

            if abs(deviation) > 10:
                self.log(f"⚠️ 警告：USB接收偏差超过10%！")
                self.log(f"   可能原因：")
                self.log(f"   1. USB传输延迟过大（计时包含了等待时间）")
                self.log(f"   2. 数据丢包（实际接收字节少于发送）")
                self.log(f"   3. FIFO满导致部分数据被丢弃")
                self.log(f"")
                self.log(f"💡 但这不影响PulseView解析：")
                self.log(
                    f"   - PulseView将使用FPGA配置的采样率 ({configured_rate/1e6:.3f} MHz)"
                )
                self.log(f"   - 因为FPGA的采样时钟是精确的，不受USB影响")
                self.log(f"   - 丢包只会导致波形数据缺失，不会改变时间轴")
            elif abs(deviation) > 5:
                self.log(f"💡 提示：USB接收偏差 {deviation:+.2f}%")
                self.log(f"   这是正常现象，因为计时包含了USB传输延迟")
                self.log(
                    f"   PulseView使用FPGA采样率 {configured_rate/1e6:.3f} MHz，时间轴精确"
                )
            else:
                self.log(f"✅ USB接收速率与FPGA采样率接近（偏差<5%）")

            self.log("=" * 60)
            self.log("")

            # 🔥 关键修复：不更改actual_sample_rate，保持FPGA配置值
            # self.actual_sample_rate = measured_sample_rate  # ❌ 删除这行！
            self.log(f"✅ PulseView将使用FPGA采样率: {configured_rate/1e6:.3f} MHz")
            self.log(f"   (精确的硬件时钟控制，误差<0.1%)")

            # 🔥🔥 V2.9：自动更新校准表（如果偏差显著）
            if abs(deviation) > 1.0:  # 偏差超过1%时，建议记录实测值
                # 获取补偿后的分频系数
                sample_rate = self.get_sample_rate_value() or 1_000_000
                sample_div = max(2, round(50_000_000 / sample_rate))
                sample_div_compensated = 3 if sample_div == 2 else sample_div * 2

                if sample_div_compensated not in self.sample_rate_calibration_table:
                    self.log("")
                    self.log(f"💡 [校准建议] 检测到新的分频系数配置：")
                    self.log(f"   发送值: {sample_div_compensated}")
                    self.log(f"   理论采样率: {configured_rate:,} Hz")
                    self.log(f"   实测采样率: {int(measured_sample_rate):,} Hz")
                    self.log(f"   偏差: {deviation:+.2f}%")
                    self.log(f"")
                    self.log(f"   建议添加到校准表：")
                    self.log(
                        f"   self.sample_rate_calibration_table[{sample_div_compensated}] = {int(measured_sample_rate)}"
                    )
                    self.log("")
        else:
            self.log(f"⚠️ 无法统计USB接收速率（时间戳缺失）")

        # 启用导出按钮
        self.export_raw_btn.setEnabled(True)
        self.export_pulseview_btn.setEnabled(True)

        # 🔥 修复：如果勾选了自动导出，则自动导出（只导出一次）
        if self.auto_open_checkbox.isChecked():
            if self.exported_file is None:
                self.export_to_pulseview()
            else:
                self.log(f"ℹ️  数据已导出到: {self.exported_file}，跳过重复导出")

    def on_capture_error(self, error_msg):
        """采集错误"""
        self.log(f"❌ {error_msg}")

        # 恢复控件
        self.capture_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)

        QMessageBox.critical(self, "采集错误", error_msg)

    def export_raw_data(self):
        """导出原始数据到文件"""
        if self.captured_data is None:
            QMessageBox.warning(self, "警告", "没有可导出的数据！")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"la_raw_data_{timestamp}.bin"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存原始数据", default_name, "二进制文件 (*.bin);;所有文件 (*.*)"
        )

        if file_path:
            try:
                with open(file_path, "wb") as f:
                    f.write(self.captured_data)

                self.log(f"✅ 原始数据已导出到: {file_path}")
                self.log(f"   文件大小: {len(self.captured_data)} 字节")

                QMessageBox.information(
                    self,
                    "导出成功",
                    f"原始数据已保存到:\n{file_path}\n\n文件大小: {len(self.captured_data)} 字节",
                )
            except Exception as e:
                self.log(f"❌ 导出失败: {str(e)}")
                QMessageBox.critical(self, "导出错误", f"无法保存文件:\n{str(e)}")

    def export_to_pulseview(self):
        """导出到PulseView"""
        if self.captured_data is None:
            QMessageBox.warning(self, "警告", "请先采集数据！")
            return

        # 🔥 修复：检查是否已经导出过这批数据
        if self.exported_file is not None:
            self.log(f"ℹ️  数据已导出到: {self.exported_file}")
            reply = QMessageBox.question(
                self,
                "重复导出",
                f"当前数据已导出到:\n{self.exported_file}\n\n是否要重新导出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                # 用户选择不重新导出，直接打开已有文件
                if self.auto_open_checkbox.isChecked():
                    self.open_pulseview(self.exported_file)
                return

        try:
            self.log("=" * 60)
            self.log("🔍 开始导出（直接传输模式）...")

            sample_rate = self.get_sample_rate_value()

            if sample_rate is None or sample_rate <= 0:
                self.log("⚠️  采样率未配置，使用默认值 1MHz")
                sample_rate = 1_000_000

            # 使用实际采样率（如果已经配置过）
            if (
                hasattr(self, "actual_sample_rate")
                and self.actual_sample_rate is not None
            ):
                actual_rate = self.actual_sample_rate
                self.log(
                    f"使用实际采样率: {actual_rate:.2f} Hz (配置值: {sample_rate} Hz)"
                )
                sample_rate = actual_rate
            else:
                self.log(f"使用配置采样率: {sample_rate} Hz")

            output_file = self.output_file_edit.text().strip()

            # 🔥 修复：如果没有指定输出文件，生成默认文件名
            if not output_file:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = os.path.join(
                    os.path.expanduser("~"), "Desktop", f"logic_analyzer_{timestamp}.sr"
                )
                self.log(f"⚠️  未指定输出文件，使用默认路径: {output_file}")

            # 🔥 修复：过滤可能的帧头数据（防御性编程）
            # FPGA应该是直接传输模式，但为了安全起见，检查并过滤帧头
            raw_data = self.captured_data

            # 🔥 FPGA使用直接传输模式，无需检测"帧头"
            # 所有字节都是有效的8通道采样数据
            self.log("✓ 数据格式: 直接传输模式（无帧结构）")
            self.log("  每字节 = 8通道采样值 (bit0=CH0, bit7=CH7)")

            # 🔥 数据有效性检查
            if len(raw_data) == 0:
                self.log("❌ 错误：数据为空，无法导出")
                QMessageBox.warning(self, "错误", "数据为空，无法导出！")
                return

            # 🔥 修复：直接传输模式使用bytes，避免list()的内存膨胀（10MB→80MB）
            # bytes对象支持索引和迭代，性能远高于list[int]
            samples = raw_data  # 直接使用bytes，不转换
            total_samples = len(samples)

            # 防止除以零错误
            if sample_rate <= 0:
                self.log("⚠️  采样率异常，使用默认值 1MHz")
                sample_rate = 1_000_000

            duration = total_samples / sample_rate

            self.log(f"总采样点: {total_samples}")
            self.log(f"📊 导出采样率: {sample_rate:,} Hz ({sample_rate/1e6:.3f} MHz)")
            self.log(f"   类型: {type(sample_rate)}, 值: {sample_rate}")
            self.log(f"   PulseView时间轴: 每个样本 = {1e6/sample_rate:.3f} μs")
            self.log(
                f"   UART位宽(115200): 约 {(1/115200)*1e6:.2f} μs = {int((1/115200)*sample_rate)} 个样本"
            )

            duration = total_samples / sample_rate
            self.log(f"时长: {duration:.6f} 秒")
            self.log(f"🔍 调试：传递给export_raw_to_sr的采样率 = {sample_rate} Hz")

            # 🔥 验证输出文件路径
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                self.log(f"⚠️  输出目录不存在，尝试创建: {output_dir}")
                try:
                    os.makedirs(output_dir, exist_ok=True)
                except Exception as e:
                    self.log(f"❌ 创建目录失败: {str(e)}")
                    raise

            # 调用导出函数
            result_file = export_raw_to_sr(
                samples, sample_rate=sample_rate, output_file=output_file
            )

            # 🔥 验证SR文件内容
            import zipfile

            try:
                with zipfile.ZipFile(result_file, "r") as zf:
                    if "metadata" in zf.namelist():
                        metadata_content = zf.read("metadata").decode("utf-8")
                        self.log("=" * 60)
                        self.log("🔍 验证SR文件metadata内容:")
                        self.log("=" * 60)
                        for line in metadata_content.split("\n"):
                            if line.strip():
                                self.log(f"  {line}")
                        self.log("=" * 60)
            except Exception as e:
                self.log(f"⚠️  无法读取SR文件metadata: {e}")

            # 🔥 记录导出的文件路径
            self.exported_file = result_file

            # 显示统计信息
            self.log("=" * 60)
            self.log("📊 导出统计（直接传输模式）")
            self.log("=" * 60)
            self.log(f"采样点: {total_samples:,}")
            self.log(f"FPGA采样率: {sample_rate/1e6:.2f} MHz (精确的PLL时钟)")
            self.log(f"理论时长: {duration:.6f} 秒")
            self.log(f"文件: {result_file}")
            self.log(f"")
            self.log(f"✅ PulseView使用FPGA采样率显示时间轴")
            self.log(f"   - FPGA采样基于精确的PLL时钟，误差<0.1%")
            self.log(f"   - USB传输延迟不影响采样时间戳的准确性")
            self.log(f"   - 丢包只会导致波形数据缺失，不会改变时间间隔")
            self.log("=" * 60)

            QMessageBox.information(
                self,
                "导出成功",
                f"成功导出 {total_samples} 个采样点到:\n{result_file}\n\n"
                f"采样率: {sample_rate} Hz\n"
                f"时长: {duration:.6f} 秒\n"
                f"传输模式: 直接传输（无帧结构）",
            )  # 自动打开PulseView
            if self.auto_open_checkbox.isChecked():
                self.open_pulseview(result_file)

        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            self.log(f"❌ 导出失败: {str(e)}")
            self.log(f"详细错误:\n{error_details}")
            QMessageBox.critical(
                self, "导出错误", f"导出失败:\n{str(e)}\n\n查看日志获取详细信息"
            )

    def open_pulseview(self, file_path):
        """打开PulseView查看数据"""
        import subprocess
        import os

        try:
            self.log(f"🚀 正在启动 PulseView...")

            # 常见的PulseView安装路径
            possible_paths = [
                # F盘
                r"F:\PulseView\pulseview.exe",
                # C盘 - Program Files
                r"C:\Program Files\sigrok\PulseView\pulseview.exe",
                r"C:\Program Files (x86)\sigrok\PulseView\pulseview.exe",
                r"C:\Program Files\PulseView\pulseview.exe",
                r"C:\Program Files (x86)\PulseView\pulseview.exe",
                # D盘
                r"D:\Program Files\PulseView\pulseview.exe",
                r"D:\PulseView\pulseview.exe",
                # E盘
                r"E:\Program Files\PulseView\pulseview.exe",
                r"E:\PulseView\pulseview.exe",
            ]

            pulseview_exe = None

            # 首先检查具体路径
            for path in possible_paths:
                if os.path.exists(path):
                    pulseview_exe = path
                    self.log(f"   找到 PulseView: {path}")
                    break

            # 如果没找到，尝试从PATH中查找
            if not pulseview_exe:
                try:
                    # 使用 where 命令查找（Windows）
                    result = subprocess.run(
                        ["where", "pulseview.exe"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if result.returncode == 0:
                        pulseview_exe = result.stdout.strip().split("\n")[0]
                        self.log(f"   在PATH中找到: {pulseview_exe}")
                except Exception as e:
                    self.log(f"   查找失败: {str(e)}")

            if pulseview_exe and os.path.exists(pulseview_exe):
                # 使用subprocess启动PulseView并打开文件
                subprocess.Popen([pulseview_exe, file_path])
                self.log(f"✅ PulseView 已启动")
            else:
                self.log("⚠️ 未找到 PulseView，请手动打开文件")
                self.log(f"   文件位置: {file_path}")
                QMessageBox.warning(
                    self,
                    "未找到PulseView",
                    f"无法自动启动PulseView\n\n"
                    f"请手动安装PulseView或直接打开文件:\n{file_path}\n\n"
                    f"PulseView下载: https://sigrok.org/wiki/Downloads\n\n"
                    f"常见安装位置:\n"
                    f"- C:\\Program Files\\sigrok\\PulseView\\pulseview.exe\n"
                    f"- F:\\PulseView\\pulseview.exe",
                )

        except Exception as e:
            self.log(f"❌ 启动PulseView失败: {str(e)}")
            self.log(f"   文件位置: {file_path}")

    def log(self, message):
        """添加日志（使用缓冲机制优化性能）"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_buffer.append(f"[{timestamp}] {message}")

        # 🔥 V7.4 日志缓冲优化：增大到1000条减少UI更新频率
        if len(self.log_buffer) >= 1000 or not self.receiving_data:
            self.flush_log_buffer()

    def flush_log_buffer(self):
        """批量刷新日志缓冲（减少UI更新次数）"""
        if not self.log_buffer:
            return

        # 🔥 V9.3: 使用textCursor批量插入，避免append触发的多次重排版
        from PySide6.QtGui import QTextCursor

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("\n".join(self.log_buffer) + "\n")
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()
        self.log_buffer.clear()

        # 🔥 优化：仅在缓冲区达到2000行时才清理（减少检查频率）
        # 原因：document().blockCount()调用有开销，每次刷新都检查会降低性能
        try:
            if self.receiving_data:  # 采集中跳过检查，等采集完成后再清理
                return

            block_count = self.log_text.document().blockCount()
            if block_count > 2000:
                # 🔥 使用moveCursor方法删除前面的行（比split更快）
                cursor = self.log_text.textCursor()
                cursor.movePosition(cursor.Start)
                # 删除前1000行
                for _ in range(min(1000, block_count - 1000)):
                    cursor.movePosition(cursor.Down, cursor.KeepAnchor)
                cursor.removeSelectedText()
        except Exception:
            pass  # 忽略清理错误，不影响主流程
        except Exception as e:
            # 如果清理失败，忽略错误继续
            pass

    def clear_log(self):
        """清空日志"""
        self.log_text.clear()
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] ✨ 日志已清空")

    def clear_raw_data(self):
        """清空原始数据显示"""
        self.raw_data_text.clear()
        self.log("🗑️ 原始数据已清空")

    def clear_stats(self):
        """清空统计信息显示"""
        self.stats_text.clear()
        self.log("🗑️ 统计信息已清空")
