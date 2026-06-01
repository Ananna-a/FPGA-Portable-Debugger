#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
波特图分析模块 V2.1 - 完整实现
功能：
  - 自动扫频测量（线性/对数）
  - 幅频特性曲线（dB刻度）
  - 相频特性曲线（度刻度）
  - 实时Bode图绘制
  - 数据导出（CSV格式）

协议：
  - 命令：0xB0 (配置), 0xB1 (启动), 0xB2 (停止), 0xB3 (查询)
  - 数据：21字节 [0xAA55][0x0B0xB0][频率4B][幅度4B][相位4B][保留2B][校验1B]

作者：AI辅助开发
日期：2026-01-13
"""

import sys
import os
import struct
import numpy as np
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QDoubleSpinBox,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QMessageBox,
    QFileDialog,
    QTextEdit,
)
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont, QTextCursor
import pyqtgraph as pg

# 导入协议命令
from core.serial_protocol import (
    CMD_BODE_CONFIG,
    CMD_BODE_START,
    CMD_BODE_STOP,
    CMD_BODE_QUERY,
)

# PyQtGraph配置
pg.setConfigOption("background", "w")  # 白色背景
pg.setConfigOption("foreground", "k")  # 黑色前景


class BodePlotterTab(QWidget):
    """波特图分析模块界面"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager

        # 数据存储
        self.freq_list = []  # 频率列表
        # 🔥 V10.2.1 I/Q原始数据存储
        self.i_ref_list = []  # 参考通道I值列表
        self.q_ref_list = []  # 参考通道Q值列表
        self.i_dut_list = []  # 测量通道I值列表
        self.q_dut_list = []  # 测量通道Q值列表
        # 计算结果（从I/Q导出）
        self.magnitude_ref_list = []  # 参考通道幅度
        self.magnitude_dut_list = []  # 测量通道幅度
        self.phase_ref_list = []      # 参考通道相位（度）
        self.phase_dut_list = []      # 测量通道相位（度）
        self.magnitude_db_list = []   # 增益dB
        self.phase_diff_list = []     # 相位差（度）

        # 扫频状态
        self.is_sweeping = False
        self.current_point = 0
        self.total_points = 0
        self.sweep_frequencies = []  # 扫频频率点列表
        
        # ✨ V10.2：时间跟踪
        self.sweep_start_time = 0
        self.last_data_time = 0
        
        # 🔥 V10.2.1：去重机制（FPGA经常重复发送相同freq_index的数据）
        self.last_received_index = -1  # 上一个接收的freq_index

        # 连接信号
        self.serial_manager.bode_data_received.connect(self._on_bode_data_received)
        self.serial_manager.connected.connect(self.on_serial_connected)
        self.serial_manager.disconnected.connect(self.on_serial_disconnected)
        # 🔥 V9.2.17d：连接日志信号以显示原始数据
        self.serial_manager.log_message.connect(self.on_serial_log)

        # 扫频定时器
        self.sweep_timer = QTimer()
        self.sweep_timer.timeout.connect(self.on_sweep_timer)

        self.init_ui()
        self.update_ui_state()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # 左右分栏
        content_layout = QHBoxLayout()

        # 左侧控制面板
        control_panel = self.create_control_panel()
        content_layout.addWidget(control_panel, 1)

        # 右侧Bode图显示
        bode_panel = self.create_bode_plot_panel()
        content_layout.addWidget(bode_panel, 3)

        main_layout.addLayout(content_layout)

    def create_status_banner(self, parent_layout):
        """创建状态横幅"""
        banner = QWidget()
        banner_layout = QHBoxLayout(banner)
        banner.setStyleSheet(
            "background-color: #E3F2FD; border: 2px solid #2196F3; border-radius: 5px; padding: 8px;"
        )

        icon_label = QLabel("📈")
        icon_label.setStyleSheet("font-size: 20pt;")
        banner_layout.addWidget(icon_label)

        self.status_label = QLabel(
            "<b>波特图分析仪 V2.1</b><br>"
            "⚡ 功能：自动扫频测量、幅频/相频特性分析、Bode图实时绘制<br>"
            "📡 状态：等待串口连接..."
        )
        self.status_label.setStyleSheet("font-size: 9pt;")
        banner_layout.addWidget(self.status_label, 1)

        parent_layout.addWidget(banner)

    def create_control_panel(self):
        """创建控制面板"""
        group = QGroupBox("扫频设置")
        layout = QVBoxLayout()

        # 频率范围设置
        freq_group = QGroupBox("频率范围")
        freq_layout = QVBoxLayout()

        # 起始频率
        freq_layout.addWidget(QLabel("起始频率 (Hz):"))
        self.start_freq_spin = QDoubleSpinBox()
        self.start_freq_spin.setRange(1, 10000000)
        self.start_freq_spin.setValue(1000)  # ✅ V10.2.5: 匹配FPGA默认值1kHz
        self.start_freq_spin.setDecimals(1)
        self.start_freq_spin.setSingleStep(100)
        freq_layout.addWidget(self.start_freq_spin)

        # 结束频率
        freq_layout.addWidget(QLabel("结束频率 (Hz):"))
        self.end_freq_spin = QDoubleSpinBox()
        self.end_freq_spin.setRange(1, 10000000)
        self.end_freq_spin.setValue(1000000)  # ✅ V10.2.5: 匹配FPGA默认值1MHz
        self.end_freq_spin.setDecimals(1)
        self.end_freq_spin.setSingleStep(1000)
        freq_layout.addWidget(self.end_freq_spin)

        freq_group.setLayout(freq_layout)
        layout.addWidget(freq_group)

        # ✨ V10.2新增：参数预设快捷按钮
        preset_group = QGroupBox("⚡ 快速预设")
        preset_layout = QHBoxLayout()
        
        # 快速测试预设
        quick_test_btn = QPushButton("快速验证")
        quick_test_btn.setToolTip("3点扫描，100Hz-10kHz，200ms采样\n用于快速验证连接")
        quick_test_btn.clicked.connect(lambda: self.apply_preset(100, 10000, 3, 200))
        preset_layout.addWidget(quick_test_btn)
        
        # 标准测试预设
        standard_test_btn = QPushButton("标准测量")
        standard_test_btn.setToolTip("50点扫描，100Hz-100kHz，500ms采样\n平衡速度和精度")
        standard_test_btn.clicked.connect(lambda: self.apply_preset(100, 100000, 50, 500))
        preset_layout.addWidget(standard_test_btn)
        
        # 高精度预设
        precision_test_btn = QPushButton("高精度")
        precision_test_btn.setToolTip("100点扫描，10Hz-100kHz，1000ms采样\n最高精度，耗时较长")
        precision_test_btn.clicked.connect(lambda: self.apply_preset(10, 100000, 100, 1000))
        preset_layout.addWidget(precision_test_btn)
        
        preset_group.setLayout(preset_layout)
        layout.addWidget(preset_group)

        # 扫频参数
        param_group = QGroupBox("扫频参数")
        param_layout = QVBoxLayout()

        # 扫描类型
        param_layout.addWidget(QLabel("扫描类型:"))
        self.sweep_type_combo = QComboBox()
        self.sweep_type_combo.addItems(["对数扫频", "线性扫频"])  # ✅ V10.2.7: 对数扫频已实现（ROM查表）
        self.sweep_type_combo.setCurrentIndex(0)  # 默认选择对数扫频
        param_layout.addWidget(self.sweep_type_combo)

        # 采样点数
        param_layout.addWidget(QLabel("采样点数:"))
        self.points_spin = QSpinBox()
        self.points_spin.setRange(1, 500)
        self.points_spin.setValue(20)  # ⚠️ V10.2.7: 临时降至20点测试UART稳定性
        self.points_spin.setToolTip("⚠️ 当前UART不稳定，建议先测20点验证系统，待FPGA更新后恢复100点")
        param_layout.addWidget(self.points_spin)

        # 稳定时间（ms）
        param_layout.addWidget(QLabel("稳定时间 (ms):"))
        self.settle_time_spin = QSpinBox()
        self.settle_time_spin.setRange(10, 5000)
        self.settle_time_spin.setValue(50)
        self.settle_time_spin.setSingleStep(10)
        self.settle_time_spin.setToolTip(
            "⚠️ 仅界面显示，FPGA使用硬编码4ms稳定时间\n"
            "（SETTLING_BASE=500000 @ 125MHz）"
        )
        param_layout.addWidget(self.settle_time_spin)

        # 采样时间（ms）
        param_layout.addWidget(QLabel("采样时间 (ms):"))
        self.sample_time_spin = QSpinBox()
        self.sample_time_spin.setRange(50, 5000)
        self.sample_time_spin.setValue(1000)  # ✅ V10.2.7: 增加到1000ms，避免UART传输冲突
        self.sample_time_spin.setSingleStep(50)
        self.sample_time_spin.setToolTip("每个频率点的测量时间（建议500-1500ms，1000ms最稳定）")
        param_layout.addWidget(self.sample_time_spin)

        param_group.setLayout(param_layout)
        layout.addWidget(param_group)

        # 控制按钮
        button_layout = QVBoxLayout()

        self.start_sweep_btn = QPushButton("▶ 开始扫频")
        self.start_sweep_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;"
        )
        self.start_sweep_btn.clicked.connect(self.start_sweep)
        button_layout.addWidget(self.start_sweep_btn)

        self.stop_sweep_btn = QPushButton("⏸ 停止扫频")
        self.stop_sweep_btn.setStyleSheet(
            "background-color: #F44336; color: white; font-weight: bold; padding: 8px;"
        )
        self.stop_sweep_btn.clicked.connect(self.stop_sweep)
        button_layout.addWidget(self.stop_sweep_btn)

        self.clear_btn = QPushButton("🗑 清除数据")
        self.clear_btn.clicked.connect(self.clear_data)
        button_layout.addWidget(self.clear_btn)

        self.export_btn = QPushButton("💾 导出数据")
        self.export_btn.clicked.connect(self.export_data)
        button_layout.addWidget(self.export_btn)

        layout.addLayout(button_layout)

        # ✨ V10.2增强：进度显示（含剩余时间估算）
        progress_group = QGroupBox("📊 扫频进度")
        progress_layout = QVBoxLayout()

        self.progress_label = QLabel("准备就绪")
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setStyleSheet("font-weight: bold; color: #2196F3; font-size: 11pt;")
        progress_layout.addWidget(self.progress_label)
        
        # 剩余时间估算
        self.time_label = QLabel("预计时间: --")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet("color: #666; font-size: 9pt;")
        progress_layout.addWidget(self.time_label)
        
        # 数据速率显示
        self.rate_label = QLabel("数据速率: --")
        self.rate_label.setAlignment(Qt.AlignCenter)
        self.rate_label.setStyleSheet("color: #666; font-size: 9pt;")
        progress_layout.addWidget(self.rate_label)

        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)

        # 🔥 V9.2.17d新增：原始数据监控
        raw_data_group = QGroupBox("📡 串口原始数据")
        raw_data_layout = QVBoxLayout()
        
        self.raw_data_display = QTextEdit()
        self.raw_data_display.setReadOnly(True)
        self.raw_data_display.setMaximumHeight(150)
        self.raw_data_display.setStyleSheet(
            "font-family: 'Courier New'; font-size: 8pt; background-color: #1E1E1E; color: #00FF00;"
        )
        self.raw_data_display.setPlaceholderText("等待数据...")
        raw_data_layout.addWidget(self.raw_data_display)
        
        raw_data_group.setLayout(raw_data_layout)
        layout.addWidget(raw_data_group)

        layout.addStretch()

        group.setLayout(layout)
        return group

    def create_bode_plot_panel(self):
        """创建I/Q调试面板 - V10.2.1（6图布局）"""
        group = QGroupBox("I/Q原始数据分析 (V10.2.1 调试模式)")
        layout = QVBoxLayout()
        
        # 创建3x2网格布局
        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout()
        
        # 子图1: 参考通道I/Q
        self.plot_ref_iq = pg.PlotWidget(title="参考通道 I/Q值")
        self.plot_ref_iq.setLabel('left', '归一化值')
        self.plot_ref_iq.setLabel('bottom', '频率 (Hz)')
        self.plot_ref_iq.setLogMode(x=True, y=False)
        self.plot_ref_iq.showGrid(x=True, y=True, alpha=0.3)
        self.curve_i_ref = self.plot_ref_iq.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=4, name='I (REF)')
        self.curve_q_ref = self.plot_ref_iq.plot(pen=pg.mkPen('r', width=2), symbol='s', symbolSize=4, name='Q (REF)')
        self.plot_ref_iq.addLegend()
        grid.addWidget(self.plot_ref_iq, 0, 0)
        
        # 子图2: 测量通道I/Q
        self.plot_dut_iq = pg.PlotWidget(title="测量通道 I/Q值")
        self.plot_dut_iq.setLabel('left', '归一化值')
        self.plot_dut_iq.setLabel('bottom', '频率 (Hz)')
        self.plot_dut_iq.setLogMode(x=True, y=False)
        self.plot_dut_iq.showGrid(x=True, y=True, alpha=0.3)
        self.curve_i_dut = self.plot_dut_iq.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=4, name='I (DUT)')
        self.curve_q_dut = self.plot_dut_iq.plot(pen=pg.mkPen('r', width=2), symbol='s', symbolSize=4, name='Q (DUT)')
        self.plot_dut_iq.addLegend()
        grid.addWidget(self.plot_dut_iq, 0, 1)
        
        # 子图3: 幅度对比
        self.plot_magnitude = pg.PlotWidget(title="幅度对比")
        self.plot_magnitude.setLabel('left', '幅度')
        self.plot_magnitude.setLabel('bottom', '频率 (Hz)')
        self.plot_magnitude.setLogMode(x=True, y=True)
        self.plot_magnitude.showGrid(x=True, y=True, alpha=0.3)
        self.curve_mag_ref = self.plot_magnitude.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=4, name='REF')
        self.curve_mag_dut = self.plot_magnitude.plot(pen=pg.mkPen('r', width=2), symbol='s', symbolSize=4, name='DUT')
        self.plot_magnitude.addLegend()
        grid.addWidget(self.plot_magnitude, 1, 0)
        
        # 子图4: 相位对比
        self.plot_phase_compare = pg.PlotWidget(title="相位对比")
        self.plot_phase_compare.setLabel('left', '相位 (度)')
        self.plot_phase_compare.setLabel('bottom', '频率 (Hz)')
        self.plot_phase_compare.setLogMode(x=True, y=False)
        self.plot_phase_compare.showGrid(x=True, y=True, alpha=0.3)
        self.curve_phase_ref = self.plot_phase_compare.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=4, name='REF')
        self.curve_phase_dut = self.plot_phase_compare.plot(pen=pg.mkPen('r', width=2), symbol='s', symbolSize=4, name='DUT')
        self.plot_phase_compare.addLegend()
        grid.addWidget(self.plot_phase_compare, 1, 1)
        
        # 子图5: 增益(dB)
        self.gain_plot = pg.PlotWidget(title="传递函数 - 增益")
        self.gain_plot.setLabel('left', '增益 (dB)')
        self.gain_plot.setLabel('bottom', '频率 (Hz)')
        self.gain_plot.setLogMode(x=True, y=False)
        self.gain_plot.showGrid(x=True, y=True, alpha=0.3)
        self.gain_curve = self.gain_plot.plot(pen=pg.mkPen('g', width=2), symbol='o', symbolSize=4, name='DUT/REF')
        self.gain_plot.addLegend()
        self.gain_plot.addLine(y=0, pen=pg.mkPen('k', style=Qt.PenStyle.DashLine))
        grid.addWidget(self.gain_plot, 2, 0)
        
        # 子图6: 相位差
        self.phase_plot = pg.PlotWidget(title="传递函数 - 相位")
        self.phase_plot.setLabel('left', '相位差 (度)')
        self.phase_plot.setLabel('bottom', '频率 (Hz)')
        self.phase_plot.setLogMode(x=True, y=False)
        self.phase_plot.showGrid(x=True, y=True, alpha=0.3)
        self.phase_curve = self.phase_plot.plot(pen=pg.mkPen('m', width=2), symbol='o', symbolSize=4, name='DUT - REF')
        self.phase_plot.addLegend()
        self.phase_plot.addLine(y=0, pen=pg.mkPen('k', style=Qt.PenStyle.DashLine))
        grid.addWidget(self.phase_plot, 2, 1)
        
        layout.addLayout(grid)
        group.setLayout(layout)
        return group

    def update_ui_state(self):
        """更新UI状态"""
        is_connected = self.serial_manager.is_connected()
        is_idle = not self.is_sweeping

        # 参数控件
        self.start_freq_spin.setEnabled(is_connected and is_idle)
        self.end_freq_spin.setEnabled(is_connected and is_idle)
        self.sweep_type_combo.setEnabled(is_connected and is_idle)
        self.points_spin.setEnabled(is_connected and is_idle)
        self.settle_time_spin.setEnabled(is_connected and is_idle)
        self.sample_time_spin.setEnabled(is_connected and is_idle)

        # 按钮状态
        self.start_sweep_btn.setEnabled(is_connected and is_idle)
        self.stop_sweep_btn.setEnabled(is_connected and self.is_sweeping)
        self.clear_btn.setEnabled(is_idle)
        self.export_btn.setEnabled(is_idle and len(self.freq_list) > 0)

    @Slot(str, str)
    def on_serial_connected(self, tx_port, rx_port):
        """串口连接成功"""
        self.update_ui_state()

    @Slot()
    def on_serial_disconnected(self):
        """串口断开"""
        if self.is_sweeping:
            self.stop_sweep()
        self.update_ui_state()

    def start_sweep(self):
        """开始扫频测量 - V10.2增强版（含参数验证）"""
        print("\n" + "="*60)
        print("[Bode] 🚀 开始扫频流程")
        print("="*60)
        
        # ✨ V10.2：参数验证
        if not self._validate_parameters():
            return
        
        print("[Bode] 💡 LED状态指示（V10.1.1 - 实际硬件映射）：")
        print("       LED0: 心跳灯（系统运行）")
        print("       LED1: DDR3初始化完成 ← ADC启动前提")
        print("       LED2: 扫频进行中（bode_sweep_active）")
        print("       LED3: IQ解调输出有效（bode_iq_valid，97.656kHz脉冲）← 关键！")
        print("       LED4: ADC CH1采集状态（adc_ch1_stream_active）")
        print("       LED5: Formatter忙状态（bode_formatter_busy）")
        print("       LED6: UART底层忙（uart_tx_busy）")
        print("       LED7: UART发送请求（bode_uart_tx_send_active）")
        print("="*60)
        
        if not self.serial_manager.is_connected():
            QMessageBox.warning(self, "错误", "串口未连接！")
            print("[Bode] ❌ 串口未连接")
            return

        # 🔥 关键修复0：禁用触发功能（避免ADC卡在STATE_WAIT_TRIGGER）
        CMD_TRIGGER_CONFIG = 0x22
        # trigger_payload: [enable(1), channel(1), edge(1), level(1), reserved(4)]
        trigger_payload = struct.pack("<BBBB", 0, 0, 0, 128) + b'\x00\x00\x00\x00'  # enable=0 禁用触发
        trigger_result = self.serial_manager.send_command(CMD_TRIGGER_CONFIG, trigger_payload)
        print(f"[Bode] ✅ ADC触发功能已禁用 (0x22, enable=0), 结果={trigger_result}")
        
        import time
        time.sleep(0.05)  # 等待触发配置生效
        
        # 🔥 关键修复1：设置ADC为流模式（0x20命令, payload=0表示流模式）
        CMD_ADC_MODE = 0x20  # ✅ 修正：0x20才是设置模式，0x21是设置Buffer大小
        mode_payload = struct.pack("<B", 0)  # 0=流模式, 1=缓冲模式
        mode_result = self.serial_manager.send_command(CMD_ADC_MODE, mode_payload)
        print(f"[Bode] ✅ ADC模式设置为流模式 (0x20, mode=0), 结果={mode_result}")
        
        time.sleep(0.05)  # 等待模式切换
        
        # 🔥 关键修复2：设置采样率（确保采样率已设置）
        CMD_ADC_SAMPLE_RATE = 0x26
        # 采样率分频系数：0=50MHz, 1=25MHz, 2=12.5MHz, 3=6.25MHz, 4=3.125MHz
        sample_div_payload = struct.pack("<I", 0)  # 使用50MHz最高采样率
        sample_rate_result = self.serial_manager.send_command(CMD_ADC_SAMPLE_RATE, sample_div_payload)
        print(f"[Bode] ✅ ADC采样率设置为50MHz (0x26, div=0), 结果={sample_rate_result}")
        
        time.sleep(0.05)  # 等待采样率设置生效
        
        # 🔥 关键修复3：启动ADC采集（Bode分析仪需要adc_ch1_stream_active=1）
        CMD_ADC_START = 0x23  # ADC启动采集命令
        adc_start_result = self.serial_manager.send_command(CMD_ADC_START)
        print(f"[Bode] ✅ ADC采集启动命令已发送 (0x23), 结果={adc_start_result}")
        
        # 短暂延迟等待ADC状态机进入CAPTURING状态
        time.sleep(0.2)  # 增加到200ms等待ADC稳定
        print(f"[Bode] ⏳ 等待ADC稳定...")
        print(f"[Bode] � 检查LED状态：")
        print(f"       ✅ LED1应亮（DDR3初始化） ← ADC启动前提")
        print(f"       ✅ LED4应亮（ADC采集中）")
        print(f"       ✅ LED3应快速闪烁（50MHz ADC数据同步到125MHz）")
        print(f"       ❌ 如果LED1不亮 → DDR3未初始化")
        print(f"       ❌ 如果LED4不亮 → ADC未启动")
        print(f"       ❌ 如果LED3不闪烁 → CDC同步失败，扫频会卡住")
        print(f"[Bode] 🔧 提示：按任意键继续...或等待3秒自动继续")
        
        # 给用户一个机会检查LED
        import threading
        continue_event = threading.Event()
        
        def wait_for_input():
            input()  # 等待用户按键
            continue_event.set()
        
        input_thread = threading.Thread(target=wait_for_input, daemon=True)
        input_thread.start()
        
        # 等待3秒或用户按键
        continue_event.wait(timeout=3.0)

        # 获取参数
        start_freq = self.start_freq_spin.value()
        end_freq = self.end_freq_spin.value()
        points = self.points_spin.value()
        sweep_type = self.sweep_type_combo.currentText()
        settle_time = self.settle_time_spin.value()
        sample_time = self.sample_time_spin.value()
        
        print(f"[Bode] 📋 扫频参数:")
        print(f"       频率范围: {start_freq} Hz - {end_freq} Hz")
        print(f"       采样点数: {points}")
        print(f"       扫描类型: {sweep_type}")
        print(f"       稳定时间: {settle_time} ms (⚠️ 仅界面显示，FPGA使用硬编码4ms)")
        print(f"       采样时间: {sample_time} ms (✅ 实际发送给FPGA)")

        if start_freq >= end_freq and points > 1:
            QMessageBox.warning(self, "参数错误", "多点扫频时起始频率必须小于结束频率！")
            print("[Bode] ❌ 无效的频率范围")
            return

        # 生成频率点列表
        if sweep_type == "对数扫频":
            # 对数扫频：FPGA ROM查表实现（V10.2.6）
            self.sweep_frequencies = np.logspace(
                np.log10(start_freq), np.log10(end_freq), points
            ).tolist()
            print(f"[Bode] ✅ 对数扫频：{self.sweep_frequencies[0]:.1f}Hz - {self.sweep_frequencies[-1]:.1f}Hz（FPGA ROM查表）")
        else:  # 线性扫频
            self.sweep_frequencies = np.linspace(start_freq, end_freq, points).tolist()
            print(f"[Bode] ✅ 线性扫频：{self.sweep_frequencies[0]:.1f}Hz - {self.sweep_frequencies[-1]:.1f}Hz")

        # 清空数据
        self.freq_list = []
        self.magnitude_list = []
        self.magnitude_db_list = []
        self.phase_list = []
        
        # 🔥 V10.2.1：重置去重状态
        self.last_received_index = -1

        # 更新状态
        self.is_sweeping = True
        self.current_point = 0
        self.total_points = points
        self.update_ui_state()
        
        # ✨ V10.2：记录开始时间，用于剩余时间估算
        import time
        self.sweep_start_time = time.time()
        self.last_data_time = time.time()

        # 发送配置命令
        print(f"[Bode] 📤 发送配置命令 (0xB0)...")
        self.send_config_command(start_freq, end_freq, points, settle_time, sample_time)

        # ✅ V10.1.1修正：定时器超时基于FPGA实际时序
        # FPGA时序: FLUSH(1.6μs) + SETTLING(4ms) + WAIT_SETTLE(262μs) + 
        #          MEASURING(sample_time) + WAIT_STABLE(4ms) + 
        #          WAIT_CORDIC(0.8μs) + WAIT_TX(2.86ms) + ADVANCE_FREQ(16ns)
        # 总计 ≈ sample_time + 12ms
        expected_time_per_point = sample_time + 12  # ms
        self.sweep_timer.start(expected_time_per_point)
        print(f"[Bode] ⏱️  定时器启动: {expected_time_per_point}ms/点 (FPGA实际时序)")
        self.progress_label.setText(f"扫频中: 0/{points} (0%)")

        # 发送启动命令
        print(f"[Bode] 📤 发送启动命令 (0xB1)...")
        self.send_start_command()
        
        expected_total_time = expected_time_per_point * points / 1000.0
        print(f"[Bode] ✅ 扫频已启动，预计总耗时: {expected_total_time:.2f}秒")
        print(f"[Bode] 💡 扫频期间LED状态（V10.1.1）：")
        print(f"       ✅ LED2应持续亮{expected_total_time:.1f}秒（sweep_active）")
        print(f"       ✅ LED3应持续快速闪烁（iq_valid，97.656kHz）← 最关键！")
        print(f"       ✅ LED4应持续亮（ADC CH1采集中）")
        print(f"       ✅ LED5应闪烁{points}次（formatter_busy，每点2.86ms）")
        print(f"       ✅ LED6亮时=UART底层忙（uart_tx_busy）")
        print(f"       ✅ LED7应闪烁{points}次（UART发送请求，每点33字节）")
        print(f"")
        print(f"       🔍 故障诊断（V10.1.1）：")
        print(f"       ❌ LED3不闪烁 → IQ解调失败！CDC同步问题或CIC无输出")
        print(f"       ❌ LED5不亮 → formatter未启动，sample_count可能未达标")
        print(f"       ❌ LED7不闪烁 → UART发送请求未发出，data_ready可能=0")
        print(f"       ❌ LED6常亮 → UART卡死，uart_tx_busy一直=1")
        print(f"       ❌ LED2一直亮 → 扫频未结束，sweep_controller卡在某状态")
        print("="*60 + "\n")

    def stop_sweep(self):
        """停止扫频"""
        # 🔥 停止Bode扫频
        CMD_BODE_STOP = 0xB2  # Bode停止扫频命令
        CMD_ADC_STOP = 0x24   # ADC停止采集命令
        
        bode_stop_result = self.serial_manager.send_command(CMD_BODE_STOP)
        print(f"[Bode] 扫频停止命令已发送 (0xB2), 结果={bode_stop_result}")
        
        # 🔥 停止ADC采集（释放FPGA资源）
        adc_stop_result = self.serial_manager.send_command(CMD_ADC_STOP)
        print(f"[Bode] ADC停止命令已发送 (0x24), 结果={adc_stop_result}")
        
        self.sweep_timer.stop()
        self.is_sweeping = False
        self.send_stop_command()
        self.progress_label.setText("已停止")
        self.update_ui_state()

    def clear_data(self):
        """清除数据"""
        self.freq_list = []
        # 清除I/Q原始数据
        self.i_ref_list = []
        self.q_ref_list = []
        self.i_dut_list = []
        self.q_dut_list = []
        # 清除计算结果
        self.magnitude_ref_list = []
        self.magnitude_dut_list = []
        self.phase_ref_list = []
        self.phase_dut_list = []
        self.magnitude_db_list = []
        self.phase_diff_list = []
        # 清除所有图表
        self.curve_i_ref.setData([], [])
        self.curve_q_ref.setData([], [])
        self.curve_i_dut.setData([], [])
        self.curve_q_dut.setData([], [])
        self.curve_mag_ref.setData([], [])
        self.curve_mag_dut.setData([], [])
        self.curve_phase_ref.setData([], [])
        self.curve_phase_dut.setData([], [])
        self.gain_curve.setData([], [])
        self.phase_curve.setData([], [])
        self.progress_label.setText("数据已清除")
        self.update_ui_state()
    
    @Slot(str)
    def on_serial_log(self, message):
        """显示串口原始数据日志（仅Bode相关）"""
        # 只显示Bode相关的消息
        if "Bode" in message or "0x0B" in message or "0xB0" in message or "AA 55" in message:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.raw_data_display.append(f"[{timestamp}] {message}")
            # 自动滚动到最新
            self.raw_data_display.verticalScrollBar().setValue(
                self.raw_data_display.verticalScrollBar().maximum()
            )
            # 限制显示行数（保留最新100行）
            if self.raw_data_display.document().blockCount() > 100:
                cursor = self.raw_data_display.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()  # 删除换行符

    def on_sweep_timer(self):
        """扫频定时器 - V10.1.1修正：仅用于进度更新，不控制结束
        
        ⚠️ 重要：定时器仅用于进度显示刷新，不判断扫频完成
        扫频结束由_on_bode_data_received中接收完所有数据后判断
        """
        # ✅ V10.1.1修正：仅更新进度显示，不判断完成
        # 扫频结束由数据接收完成触发（在_on_bode_data_received中）
        if self.current_point >= self.total_points:
            # 已收到所有数据，停止定时器但不重复处理
            self.sweep_timer.stop()
            return

        # 更新进度显示（当前进度基于已接收数据点数）
        progress = int((self.current_point / self.total_points) * 100)
        self.progress_label.setText(
            f"扫频中: {self.current_point}/{self.total_points} ({progress}%)"
        )

    @Slot(int, float, float, float, float, float)
    def _on_bode_data_received(
        self, freq_index: int, freq: float, 
        i_ref: float, q_ref: float,
        i_dut: float, q_dut: float
    ):
        """接收Bode I/Q原始数据（从串口管理器信号触发）
        
        V10.2.1 I/Q调试版本：
        - i_ref, q_ref: 参考通道I/Q值（53位有符号，已归一化）
        - i_dut, q_dut: 测量通道I/Q值（53位有符号，已归一化）
        
        ✅ 自动计算幅度相位用于显示
        """
        if not self.is_sweeping:
            print(f"[Bode] ⚠️  扫频未启动(is_sweeping=False)，丢弃数据")
            return
        
        # 🔥 V10.2.1：去重逻辑
        if freq_index == self.last_received_index:
            print(f"[Bode] ⚠️  检测到重复数据 freq_index={freq_index}，跳过")
            return
        
        if freq_index != self.last_received_index + 1 and self.last_received_index != -1:
            print(f"[Bode] ⚠️  freq_index跳跃: {self.last_received_index} → {freq_index}，可能丢包")
        
        self.last_received_index = freq_index

        # 存储I/Q原始数据
        self.freq_list.append(freq)
        self.i_ref_list.append(i_ref)
        self.q_ref_list.append(q_ref)
        self.i_dut_list.append(i_dut)
        self.q_dut_list.append(q_dut)
        
        # 🔧 V10.2.8待排查：低频段出现正增益问题
        # 硬件连接（用户确认）：ADC1=DDS直连(REF), ADC2=RC滤波器(DUT)
        # 实测异常：1k-2.8MHz显示+0.6到+4.6dB增益（应该是≈0dB或负值）
        # 需要检查：FPGA内部ADC1/ADC2到REF/DUT的映射是否正确
        SWAP_CHANNELS = False  # 暂不交换，待查FPGA代码
        
        if SWAP_CHANNELS:
            # 交换REF和DUT数据（软件修正硬件接线错误）
            i_ref, i_dut = i_dut, i_ref
            q_ref, q_dut = q_dut, q_ref
        
        # 计算幅度和相位
        magnitude_ref = np.sqrt(i_ref**2 + q_ref**2)
        magnitude_dut = np.sqrt(i_dut**2 + q_dut**2)
        phase_ref = np.arctan2(q_ref, i_ref) * 180 / np.pi  # 转换为度
        phase_dut = np.arctan2(q_dut, i_dut) * 180 / np.pi
        
        self.magnitude_ref_list.append(magnitude_ref)
        self.magnitude_dut_list.append(magnitude_dut)
        self.phase_ref_list.append(phase_ref)
        self.phase_dut_list.append(phase_dut)
        
        # 计算增益和相位差
        if magnitude_ref > 1e-10:
            gain_db = 20 * np.log10(magnitude_dut / magnitude_ref)
        else:
            gain_db = -120.0
        
        phase_diff = phase_dut - phase_ref
        # 归一化到±180度
        while phase_diff > 180:
            phase_diff -= 360
        while phase_diff < -180:
            phase_diff += 360
        
        self.magnitude_db_list.append(gain_db)
        self.phase_diff_list.append(phase_diff)
        
        # 🔥 调试输出
        print(f"[Bode V10.2.1] 📊 freq={freq:7.0f}Hz")
        print(f"  REF: I={i_ref:+.6f}, Q={q_ref:+.6f} → mag={magnitude_ref:.6f}, phase={phase_ref:+7.2f}°")
        print(f"  DUT: I={i_dut:+.6f}, Q={q_dut:+.6f} → mag={magnitude_dut:.6f}, phase={phase_dut:+7.2f}°")
        print(f"  结果: gain={gain_db:+6.2f}dB, phase_diff={phase_diff:+7.2f}°")

        # 更新所有6个图表
        self.curve_i_ref.setData(self.freq_list, self.i_ref_list)
        self.curve_q_ref.setData(self.freq_list, self.q_ref_list)
        self.curve_i_dut.setData(self.freq_list, self.i_dut_list)
        self.curve_q_dut.setData(self.freq_list, self.q_dut_list)
        self.curve_mag_ref.setData(self.freq_list, self.magnitude_ref_list)
        self.curve_mag_dut.setData(self.freq_list, self.magnitude_dut_list)
        self.curve_phase_ref.setData(self.freq_list, self.phase_ref_list)
        self.curve_phase_dut.setData(self.freq_list, self.phase_dut_list)
        self.gain_curve.setData(self.freq_list, self.magnitude_db_list)
        self.phase_curve.setData(self.freq_list, self.phase_diff_list)

        # 移动到下一个频率点
        self.current_point += 1

        # ✨ V10.2：增强进度显示（剩余时间估算 + 数据速率）
        import time
        current_time = time.time()
        elapsed_time = current_time - self.sweep_start_time
        
        # 计算平均每点耗时
        if self.current_point > 0:
            avg_time_per_point = elapsed_time / self.current_point
            remaining_points = self.total_points - self.current_point
            remaining_time = avg_time_per_point * remaining_points
            
            # 计算数据速率（点/秒）
            data_rate = self.current_point / elapsed_time if elapsed_time > 0 else 0
            
            # 格式化时间显示
            if remaining_time < 60:
                time_str = f"{remaining_time:.0f}秒"
            else:
                minutes = int(remaining_time // 60)
                seconds = int(remaining_time % 60)
                time_str = f"{minutes}分{seconds}秒"
        else:
            time_str = "计算中..."
            data_rate = 0
        
        progress = int((self.current_point / self.total_points) * 100)
        self.progress_label.setText(
            f"扫频中: {self.current_point}/{self.total_points} ({progress}%)"
        )
        self.time_label.setText(f"剩余时间: {time_str}")
        self.rate_label.setText(f"数据速率: {data_rate:.2f} 点/秒")
        
        # 记录本次数据接收时间
        self.last_data_time = current_time
        
        # ✅ V10.1.1：检测扫频完成（接收完所有数据点）
        if self.current_point >= self.total_points:
            total_elapsed = current_time - self.sweep_start_time
            print(f"\n[Bode] ✅ 所有数据已接收完成: {self.current_point}/{self.total_points}")
            print(f"       总耗时: {total_elapsed:.1f}秒, 平均: {total_elapsed/self.total_points:.2f}秒/点")
            self.stop_sweep()
            self.progress_label.setText(f"✅ 完成: {self.total_points}/{self.total_points}")
            self.time_label.setText(f"总耗时: {total_elapsed:.1f}秒")
            self.rate_label.setText(f"平均速率: {self.total_points/total_elapsed:.2f} 点/秒")
            
            QMessageBox.information(
                self,
                "扫频完成",
                f"测量完成！\n"
                f"共采集 {self.total_points} 个频率点\n"
                f"频率范围: {self.freq_list[0]:.1f} - {self.freq_list[-1]:.1f} Hz\n"
                f"总耗时: {total_elapsed:.1f} 秒\n"
                f"平均速率: {self.total_points/total_elapsed:.2f} 点/秒"
            )

    def send_config_command(
        self, start_freq, end_freq, points, settle_time, sample_time
    ):
        """发送配置命令（0xB0）
        
        FPGA协议格式（14字节）：
            Byte 0-3:   freq_start (小端序, 4字节)
            Byte 4-7:   freq_stop (小端序, 4字节)
            Byte 8-9:   freq_steps (小端序, 2字节)
            Byte 10-13: samples_per_freq (小端序, 4字节)
        
        计算samples_per_freq:
            samples_per_freq = (sample_time_ms * 采样率) / 1000
            例如：sample_time=100ms, 采样率=50MHz → 5,000,000采样点
                 sample_time=10ms, 采样率=50MHz → 500,000采样点
        """
        print(f"[Bode] send_config_command: start={start_freq}Hz, end={end_freq}Hz, points={points}")
        
        # 🔥 关键修复：计算samples_per_freq（FPGA期望的参数）
        # 注意：IQ解调器使用CIC滤波器，抽取率R=128
        # ADC采样率50MHz → IQ输出速率 = 50MHz/128 = 390625 Hz
        # 
        # ✅ V10.1修复：使用异步FIFO替代两级寄存器，不再需要CDC补偿
        # V9.2.3的CDC_OVERSAMPLE=2.5是错误的，会导致采样时间超时
        CIC_DECIMATION = 128  # ✅ V10.2.8: 修正抽取比（之前错误写成512）
        adc_sample_rate_hz = 50_000_000  # 50MHz ADC
        iq_output_rate_hz = adc_sample_rate_hz / CIC_DECIMATION  # = 390625 Hz
        
        # 计算需要的IQ采样点数（不是ADC采样点数！）
        # ✅ V10.1：直接使用实际IQ采样数，无需CDC补偿
        samples_per_freq_fpga = int((sample_time / 1000.0) * iq_output_rate_hz)
        
        # 限制范围：最小2048（足够CIC滤波器收敛），最大500,000（约5秒）
        samples_per_freq_fpga = max(2048, min(samples_per_freq_fpga, 500_000))
        
        print(f"[Bode] 计算参数: sample_time={sample_time}ms → samples_fpga={samples_per_freq_fpga} (IQ采样数，无CDC补偿)")
        
        try:
            # 构建payload: [freq_start(4B)][freq_stop(4B)][freq_steps(2B)][samples_per_freq(4B)]
            payload = struct.pack(
                "<IIHI",  # 🔥 修复：匹配FPGA协议（I=4字节, H=2字节, I=4字节）
                int(start_freq),      # 起始频率（Hz）
                int(end_freq),        # 结束频率（Hz）
                points,               # 频率点数（2字节）
                samples_per_freq_fpga # 每频点采样数（4字节，已含CDC×2.5补偿）
            )
            print(f"[Bode] Payload: {len(payload)}字节 = {payload.hex()}")
            result = self.serial_manager.send_command(CMD_BODE_CONFIG, payload)
            print(f"[Bode] 配置命令发送结果: {result}")
        except Exception as e:
            print(f"[Bode] 配置命令发送异常: {e}")
            import traceback
            traceback.print_exc()

    def send_start_command(self):
        """发送启动命令（0xB1）"""
        print(f"[DEBUG] send_start_command called")
        try:
            result = self.serial_manager.send_command(CMD_BODE_START, b"")
            print(f"[DEBUG] send_start_command result: {result}")
        except Exception as e:
            print(f"[DEBUG] Exception in send_start_command: {e}")
            import traceback
            traceback.print_exc()

    def send_stop_command(self):
        """发送停止命令（0xB2）"""
        self.serial_manager.send_command(CMD_BODE_STOP, b"")
    
    # ============================================================================
    # ✨ V10.2新增：用户友好功能
    # ============================================================================
    
    def _validate_parameters(self):
        """验证扫频参数的合理性
        
        Returns:
            bool: 参数有效返回True，否则返回False
        """
        start_freq = self.start_freq_spin.value()
        end_freq = self.end_freq_spin.value()
        points = self.points_spin.value()
        sample_time = self.sample_time_spin.value()
        
        # 检查频率范围
        if start_freq <= 0:
            QMessageBox.warning(self, "参数错误", "起始频率必须大于0 Hz！")
            return False
        
        if end_freq <= 0:
            QMessageBox.warning(self, "参数错误", "结束频率必须大于0 Hz！")
            return False
        
        if start_freq >= end_freq and points > 1:
            QMessageBox.warning(self, "参数错误", "多点扫频时起始频率必须小于结束频率！")
            return False
        
        # 检查频率是否超过Nyquist极限
        max_freq = 50_000_000 / 2  # ADC 50MHz, Nyquist = 25MHz
        if end_freq > max_freq:
            reply = QMessageBox.question(
                self,
                "频率警告",
                f"结束频率 {end_freq/1e6:.1f} MHz 超过Nyquist频率 ({max_freq/1e6:.1f} MHz)！\n"
                f"这可能导致频率混叠，测量结果不准确。\n\n"
                f"是否继续？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return False
        
        # 检查点数合理性
        if points < 1 or points > 500:
            QMessageBox.warning(self, "参数错误", "采样点数必须在1-500之间！")
            return False
        
        # 检查采样时间
        if sample_time < 50:
            reply = QMessageBox.question(
                self,
                "采样时间警告",
                f"采样时间 {sample_time} ms 过短，可能导致测量不准确！\n"
                f"建议使用至少 200ms 以上。\n\n"
                f"是否继续？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return False
        
        # 预估总时间并警告
        time_per_point = (sample_time + 12) / 1000.0  # 秒
        total_time = time_per_point * points
        
        if total_time > 300:  # 超过5分钟
            reply = QMessageBox.question(
                self,
                "时间警告",
                f"根据当前参数，预计总耗时约 {total_time/60:.1f} 分钟！\n"
                f"这可能需要较长等待时间。\n\n"
                f"是否继续？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return False
        
        # 所有检查通过
        print(f"[Bode] ✅ 参数验证通过")
        print(f"       频率范围: {start_freq} - {end_freq} Hz")
        print(f"       采样点数: {points}")
        print(f"       预计耗时: {total_time:.1f} 秒")
        return True
    
    def apply_preset(self, start_freq, end_freq, points, sample_time):
        """应用参数预设
        
        Args:
            start_freq: 起始频率（Hz）
            end_freq: 结束频率（Hz）
            points: 采样点数
            sample_time: 采样时间（ms）
        """
        self.start_freq_spin.setValue(start_freq)
        self.end_freq_spin.setValue(end_freq)
        self.points_spin.setValue(points)
        self.sample_time_spin.setValue(sample_time)
        
        # 估算总时间
        time_per_point = (sample_time + 12) / 1000.0  # 秒
        total_time = time_per_point * points
        
        print(f"[Bode] 📋 应用预设: {start_freq}Hz-{end_freq}Hz, {points}点, {sample_time}ms采样")
        print(f"       预计耗时: {total_time:.1f}秒")
        
        QMessageBox.information(
            self,
            "预设已应用",
            f"参数预设已应用：\n"
            f"频率范围: {start_freq} - {end_freq} Hz\n"
            f"采样点数: {points}\n"
            f"采样时间: {sample_time} ms\n"
            f"预计总耗时: {total_time:.1f} 秒"
        )
    
    def auto_scale_plots(self):
        """自动缩放Y轴以适应数据"""
        if len(self.magnitude_db_list) > 0:
            # 增益曲线自动缩放
            min_gain = min(self.magnitude_db_list)
            max_gain = max(self.magnitude_db_list)
            margin = (max_gain - min_gain) * 0.1  # 10%边距
            self.gain_plot.setYRange(min_gain - margin, max_gain + margin, padding=0)
            print(f"[Bode] 增益曲线缩放: {min_gain:.1f}dB ~ {max_gain:.1f}dB")
        
        if len(self.phase_list) > 0:
            # 相位曲线自动缩放（展开后）
            unwrapped_phase = np.unwrap(np.deg2rad(self.phase_list)) * 180 / np.pi
            min_phase = min(unwrapped_phase)
            max_phase = max(unwrapped_phase)
            margin = (max_phase - min_phase) * 0.1
            self.phase_plot.setYRange(min_phase - margin, max_phase + margin, padding=0)
            print(f"[Bode] 相位曲线缩放: {min_phase:.1f}° ~ {max_phase:.1f}°")
    
    def mark_peak_points(self):
        """标记增益曲线的峰值和谷值"""
        if len(self.magnitude_db_list) < 3:
            QMessageBox.warning(self, "数据不足", "需要至少3个数据点才能标记峰值")
            return
        
        # 清除旧标记
        self.clear_markers()
        
        # 使用scipy查找峰值（需要至少3个点）
        try:
            from scipy.signal import find_peaks
            
            # 查找峰值（局部最大值）
            peaks, _ = find_peaks(self.magnitude_db_list, distance=3)
            # 查找谷值（局部最小值）
            valleys, _ = find_peaks([-x for x in self.magnitude_db_list], distance=3)
            
            # 标记峰值（红色三角）
            for idx in peaks:
                marker = pg.ScatterPlotItem(
                    [self.freq_list[idx]], 
                    [self.magnitude_db_list[idx]],
                    symbol='t', size=15, pen=pg.mkPen('r', width=2), brush='r'
                )
                self.gain_plot.addItem(marker)
                self.gain_markers.append(marker)
                
                # 添加文本标签
                text = pg.TextItem(
                    f"峰值: {self.magnitude_db_list[idx]:.2f}dB\n{self.freq_list[idx]:.0f}Hz",
                    anchor=(0.5, 1.2), color='r'
                )
                text.setPos(self.freq_list[idx], self.magnitude_db_list[idx])
                self.gain_plot.addItem(text)
                self.gain_markers.append(text)
            
            # 标记谷值（蓝色倒三角）
            for idx in valleys:
                marker = pg.ScatterPlotItem(
                    [self.freq_list[idx]], 
                    [self.magnitude_db_list[idx]],
                    symbol='d', size=15, pen=pg.mkPen('b', width=2), brush='b'
                )
                self.gain_plot.addItem(marker)
                self.gain_markers.append(marker)
                
                text = pg.TextItem(
                    f"谷值: {self.magnitude_db_list[idx]:.2f}dB\n{self.freq_list[idx]:.0f}Hz",
                    anchor=(0.5, -0.2), color='b'
                )
                text.setPos(self.freq_list[idx], self.magnitude_db_list[idx])
                self.gain_plot.addItem(text)
                self.gain_markers.append(text)
            
            print(f"[Bode] 标记了 {len(peaks)} 个峰值, {len(valleys)} 个谷值")
            
        except ImportError:
            QMessageBox.warning(
                self, "缺少依赖", 
                "需要安装scipy库才能使用峰值检测功能\n"
                "请运行: pip install scipy"
            )
    
    def clear_markers(self):
        """清除所有标记点"""
        for marker in self.gain_markers:
            self.gain_plot.removeItem(marker)
        self.gain_markers.clear()
        
        for marker in self.phase_markers:
            self.phase_plot.removeItem(marker)
        self.phase_markers.clear()
    
    def on_mouse_moved_gain(self, pos):
        """增益曲线鼠标移动事件 - 显示数据点信息"""
        if len(self.freq_list) == 0:
            return
        
        # 转换鼠标位置到数据坐标
        mouse_point = self.gain_plot.plotItem.vb.mapSceneToView(pos)
        x = mouse_point.x()
        
        # 查找最近的数据点
        if x > 0 and len(self.freq_list) > 0:
            # 使用对数距离查找最近点（因为X轴是对数刻度）
            log_freqs = np.log10(self.freq_list)
            log_x = np.log10(x)
            distances = np.abs(log_freqs - log_x)
            nearest_idx = np.argmin(distances)
            
            # 只在鼠标靠近数据点时显示
            if distances[nearest_idx] < 0.1:  # 阈值可调
                self.gain_label.setPos(self.freq_list[nearest_idx], self.magnitude_db_list[nearest_idx])
                self.gain_label.setText(
                    f"频率: {self.freq_list[nearest_idx]:.1f} Hz\n"
                    f"增益: {self.magnitude_db_list[nearest_idx]:.2f} dB"
                )
                self.gain_label.setVisible(True)
            else:
                self.gain_label.setVisible(False)
    
    def on_mouse_moved_phase(self, pos):
        """相位曲线鼠标移动事件 - 显示数据点信息"""
        if len(self.freq_list) == 0:
            return
        
        mouse_point = self.phase_plot.plotItem.vb.mapSceneToView(pos)
        x = mouse_point.x()
        
        if x > 0 and len(self.freq_list) > 0:
            log_freqs = np.log10(self.freq_list)
            log_x = np.log10(x)
            distances = np.abs(log_freqs - log_x)
            nearest_idx = np.argmin(distances)
            
            if distances[nearest_idx] < 0.1:
                # 显示展开后的相位
                unwrapped_phase = np.unwrap(np.deg2rad(self.phase_list)) * 180 / np.pi
                self.phase_label.setPos(self.freq_list[nearest_idx], unwrapped_phase[nearest_idx])
                self.phase_label.setText(
                    f"频率: {self.freq_list[nearest_idx]:.1f} Hz\n"
                    f"相位: {unwrapped_phase[nearest_idx]:.2f}°"
                )
                self.phase_label.setVisible(True)
            else:
                self.phase_label.setVisible(False)

    def export_data(self):
        """导出数据到CSV - V10.2.1增强版（含I/Q原始数据）"""
        if len(self.freq_list) == 0:
            QMessageBox.warning(self, "错误", "没有数据可导出！")
            return

        # 选择保存文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"bode_iq_data_{timestamp}.csv"
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出Bode I/Q数据", default_filename, "CSV Files (*.csv)"
        )

        if not filename:
            return

        try:
            with open(filename, "w", encoding="utf-8") as f:
                # ✨ V10.2.1：写入测试信息头部
                f.write("# Bode Analyzer I/Q Raw Data (V10.2.1 Debug Mode)\n")
                f.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# Frequency Range: {self.freq_list[0]:.1f} - {self.freq_list[-1]:.1f} Hz\n")
                f.write(f"# Total Points: {len(self.freq_list)}\n")
                f.write(f"# Sample Time: {self.sample_time_spin.value()} ms\n")
                
                # 计算统计信息
                max_gain = max(self.magnitude_db_list)
                min_gain = min(self.magnitude_db_list)
                max_gain_freq = self.freq_list[self.magnitude_db_list.index(max_gain)]
                min_gain_freq = self.freq_list[self.magnitude_db_list.index(min_gain)]
                
                f.write(f"# Max Gain: {max_gain:.2f} dB @ {max_gain_freq:.1f} Hz\n")
                f.write(f"# Min Gain: {min_gain:.2f} dB @ {min_gain_freq:.1f} Hz\n")
                f.write(f"# Gain Range: {max_gain - min_gain:.2f} dB\n")
                f.write("#\n")
                
                # I/Q原始数据表头
                f.write("# --- I/Q Raw Data and Calculated Results ---\n")
                f.write("Frequency(Hz),I_REF,Q_REF,I_DUT,Q_DUT,"
                       "Mag_REF,Phase_REF(deg),Mag_DUT,Phase_DUT(deg),"
                       "Gain(dB),Phase_Diff(deg)\n")

                # 写入数据
                for i in range(len(self.freq_list)):
                    f.write(
                        f"{self.freq_list[i]:.2f},"
                        f"{self.i_ref_list[i]:+.8f},"
                        f"{self.q_ref_list[i]:+.8f},"
                        f"{self.i_dut_list[i]:+.8f},"
                        f"{self.q_dut_list[i]:+.8f},"
                        f"{self.magnitude_ref_list[i]:.8f},"
                        f"{self.phase_ref_list[i]:+.2f},"
                        f"{self.magnitude_dut_list[i]:.8f},"
                        f"{self.phase_dut_list[i]:+.2f},"
                        f"{self.magnitude_db_list[i]:+.2f},"
                        f"{self.phase_diff_list[i]:+.2f}\n"
                    )

            QMessageBox.information(
                self, "导出成功", f"I/Q数据已导出到：\n{filename}"
            )

        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出失败：{str(e)}")


def main():
    """独立测试"""
    from PySide6.QtWidgets import QApplication
    from core.serial_manager import SerialManager

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 创建串口管理器
    serial_mgr = SerialManager()

    # 创建波特图界面
    widget = BodePlotterTab(serial_mgr)
    widget.setWindowTitle("Bode分析仪 - 独立测试")
    widget.resize(1200, 800)
    widget.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
