#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DDS双通道上位机控制工具 - V1.3 优化版
基于PySide6，使用统一串口管理器

功能：
  - 双通道DDS参数设置（波形、频率、相位、幅度）
  - 实时串口通信（CDC发送，CH340接收）
  - 命令日志显示
  - 快速预设配置

架构优化：
  - 使用主程序提供的串口管理器
  - 使用公共协议层（serial_protocol.py）
  - 移除冗余的串口管理代码
"""

import sys
import struct
import math
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QPushButton,
    QSlider,
    QGridLayout,
    QCheckBox,
    QSizePolicy,
    QMessageBox,
    QTextEdit,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

# 导入协议层函数
from core.serial_protocol import calc_freq_word, build_dds_all_params_payload


# ============================================================================
# 通道控制面板
# ============================================================================


class ChannelPanel(QGroupBox):
    """单通道DDS控制面板"""

    def __init__(self, channel_name, channel_id):
        super().__init__(channel_name)
        self.channel_id = channel_id
        self.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )  # 垂直方向固定，不拉伸
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 波形选择
        wave_layout = QHBoxLayout()
        wave_layout.addWidget(QLabel("波形:"))
        self.wave_combo = QComboBox()
        self.wave_combo.addItems(
            ["正弦波", "方波", "三角波", "锯齿波", "反锯齿波", "脉冲波", "任意波形"]
        )
        wave_layout.addWidget(self.wave_combo)
        layout.addLayout(wave_layout)

        # 频率设置（滑块 + 数字框 + 预设按钮）
        freq_layout = QVBoxLayout()

        # 频率标签和数字框
        freq_label_layout = QHBoxLayout()
        freq_label_layout.addWidget(QLabel("频率 (Hz):"))
        self.freq_spin = QDoubleSpinBox()
        self.freq_spin.setRange(1, 50000000)  # 1Hz - 50MHz
        self.freq_spin.setValue(1000)
        self.freq_spin.setDecimals(1)
        self.freq_spin.setSingleStep(100)
        self.freq_spin.setMinimumWidth(120)
        freq_label_layout.addWidget(self.freq_spin)
        freq_label_layout.addStretch()
        freq_layout.addLayout(freq_label_layout)

        # 频率滑块（对数刻度，1Hz - 50MHz）
        self.freq_slider = QSlider(Qt.Horizontal)
        self.freq_slider.setRange(0, 1000)  # 0-1000映射到1Hz-50MHz
        self.freq_slider.setValue(self.freq_to_slider(1000))
        self.freq_slider.setTickPosition(QSlider.TicksBelow)
        self.freq_slider.setTickInterval(100)
        self.freq_slider.setFixedHeight(30)  # 固定滑块高度
        freq_layout.addWidget(self.freq_slider)

        # 连接频率滑块和数字框
        self.freq_slider.valueChanged.connect(self.on_freq_slider_changed)
        self.freq_spin.valueChanged.connect(self.on_freq_spin_changed)

        # 频率预设按钮（紧凑横向布局）
        freq_preset_layout = QHBoxLayout()
        freq_preset_layout.setSpacing(8)  # 适中间距
        freq_preset_layout.setContentsMargins(20, 0, 20, 0)  # 左右边距各20px
        freq_presets = [
            ("100", 100),
            ("1k", 1000),
            ("10k", 10000),
            ("100k", 100000),
            ("1M", 1000000),
            ("10M", 10000000),
        ]
        for text, value in freq_presets:
            btn = QPushButton(text)
            btn.setMinimumWidth(65)  # 加宽
            btn.setMaximumWidth(75)
            btn.setFixedHeight(24)  # 固定更小的高度
            btn.clicked.connect(lambda checked, v=value: self.set_freq_and_apply(v))
            freq_preset_layout.addWidget(btn)
            freq_preset_layout.addStretch(1)  # 每个按钮后添加弹性空间，实现均匀分布
        freq_layout.addLayout(freq_preset_layout)
        layout.addLayout(freq_layout)

        # 相位设置（隐藏，由相位差控制管理）
        # 保留phase_spin用于内部计算，但不显示UI
        self.phase_spin = QSpinBox()
        self.phase_spin.setRange(0, 359)
        self.phase_spin.setValue(0)  # 初始相位为0
        self.phase_spin.setVisible(False)  # 隐藏

        self.phase_slider = QSlider(Qt.Horizontal)
        self.phase_slider.setRange(0, 359)
        self.phase_slider.setValue(0)
        self.phase_slider.setVisible(False)  # 隐藏

        # 连接滑块和数字框（内部使用）
        self.phase_slider.valueChanged.connect(self.phase_spin.setValue)
        self.phase_spin.valueChanged.connect(self.phase_slider.setValue)

        # 占空比设置（仅脉冲波形时显示）- 16位精度升级 - 紧凑布局
        duty_layout = QVBoxLayout()
        duty_layout.setSpacing(2)  # 最小间距
        duty_layout.setContentsMargins(0, 4, 0, 0)  # 上边距4px，其他0
        duty_label_layout = QHBoxLayout()
        duty_label_layout.addWidget(QLabel("占空比:"))
        self.duty_spin = QDoubleSpinBox()
        self.duty_spin.setRange(0, 100)  # 0%-100%范围
        self.duty_spin.setValue(50)
        self.duty_spin.setSuffix("%")
        self.duty_spin.setDecimals(3)  # 3位小数，精度0.001%（接近16位精度0.0015%）
        self.duty_spin.setSingleStep(0.01)  # 0.01%步进
        self.duty_spin.setMinimumWidth(90)
        self.duty_spin.setFixedHeight(24)  # 固定高度
        duty_label_layout.addWidget(self.duty_spin)
        duty_label_layout.addStretch()
        duty_layout.addLayout(duty_label_layout)

        self.duty_slider = QSlider(Qt.Horizontal)
        self.duty_slider.setRange(0, 10000)  # 0-10000映射到0-100%，精度0.01%
        self.duty_slider.setValue(5000)  # 默认50%
        self.duty_slider.setTickPosition(QSlider.TicksBelow)
        self.duty_slider.setTickInterval(1000)  # 每10%一个刻度
        self.duty_slider.setFixedHeight(22)  # 更小的滑块高度
        duty_layout.addWidget(self.duty_slider)

        # 连接滑块和数字框（16位精度）
        self.duty_slider.valueChanged.connect(self.on_duty_slider_changed)
        self.duty_spin.valueChanged.connect(self.on_duty_spin_changed)

        # 占空比布局容器（用于显示/隐藏）
        self.duty_group = QWidget()
        self.duty_group.setLayout(duty_layout)
        self.duty_group.setVisible(False)  # 默认隐藏
        layout.addWidget(self.duty_group)

        # 幅度设置（滑块 + 数字框 + 电压 + 百分比）
        amp_layout = QVBoxLayout()
        amp_label_layout = QHBoxLayout()
        amp_label_layout.addWidget(QLabel("幅度:"))

        # DAC数值（0-255）
        self.amp_spin = QSpinBox()
        self.amp_spin.setRange(0, 255)
        self.amp_spin.setValue(255)
        self.amp_spin.setMinimumWidth(60)
        amp_label_layout.addWidget(self.amp_spin)

        # 电压显示（峰峰值）
        self.amp_voltage_label = QLabel("(8.80Vpp)")
        self.amp_voltage_label.setMinimumWidth(80)
        amp_label_layout.addWidget(self.amp_voltage_label)

        amp_label_layout.addStretch()
        amp_layout.addLayout(amp_label_layout)

        self.amp_slider = QSlider(Qt.Horizontal)
        self.amp_slider.setRange(0, 255)
        self.amp_slider.setValue(255)
        self.amp_slider.setTickPosition(QSlider.TicksBelow)
        self.amp_slider.setTickInterval(32)
        self.amp_slider.setFixedHeight(30)  # 固定滑块高度
        amp_layout.addWidget(self.amp_slider)

        # 连接滑块和数字框
        self.amp_slider.valueChanged.connect(self.amp_spin.setValue)
        self.amp_spin.valueChanged.connect(self.amp_slider.setValue)
        self.amp_spin.valueChanged.connect(self.update_amp_display)

        layout.addLayout(amp_layout)

        # 使能开关
        control_layout = QHBoxLayout()
        self.enable_check = QCheckBox("使能输出")
        self.enable_check.setChecked(True)
        control_layout.addWidget(self.enable_check)
        control_layout.addStretch()

        layout.addLayout(control_layout)

        self.setLayout(layout)

        # 初始化显示
        self.update_amp_display(255)

        # 连接信号以实现实时更新
        self.setup_auto_apply()

    def freq_to_slider(self, freq):
        """频率转滑块位置（对数映射）"""
        # 1Hz -> 0, 50MHz -> 1000
        if freq < 1:
            freq = 1
        if freq > 50000000:
            freq = 50000000
        # 对数映射: slider = log10(freq) / log10(50000000) * 1000
        slider_val = int(math.log10(freq) / math.log10(50000000) * 1000)
        return max(0, min(1000, slider_val))

    def slider_to_freq(self, slider):
        """滑块位置转频率（对数映射）"""
        # slider: 0-1000 -> freq: 1Hz-50MHz
        # freq = 10^(slider/1000 * log10(50000000))
        freq = 10 ** (slider / 1000.0 * math.log10(50000000))
        return max(1, min(50000000, freq))

    def on_duty_slider_changed(self, value):
        """占空比滑块改变（16位精度）"""
        duty_pct = value / 100.0  # 0-10000 映射到 0-100%
        self.duty_spin.blockSignals(True)
        self.duty_spin.setValue(duty_pct)
        self.duty_spin.blockSignals(False)

    def on_duty_spin_changed(self, value):
        """占空比数字框改变（16位精度）"""
        slider_val = int(value * 100)  # 0-100% 映射到 0-10000
        self.duty_slider.blockSignals(True)
        self.duty_slider.setValue(slider_val)
        self.duty_slider.blockSignals(False)

    def on_freq_slider_changed(self, value):
        """频率滑块改变"""
        freq = self.slider_to_freq(value)
        self.freq_spin.blockSignals(True)
        self.freq_spin.setValue(freq)
        self.freq_spin.blockSignals(False)

    def on_freq_spin_changed(self, value):
        """频率数字框改变"""
        slider_val = self.freq_to_slider(value)
        self.freq_slider.blockSignals(True)
        self.freq_slider.setValue(slider_val)
        self.freq_slider.blockSignals(False)

    def set_freq_and_apply(self, freq):
        """设置频率并立即应用（用于快捷按钮）"""
        current_freq = self.freq_spin.value()
        if abs(current_freq - freq) > 0.01:  # 允许0.01Hz误差
            self.freq_spin.setValue(freq)
            # 频率改变时，需要同时更新两个通道保持同步
            self.auto_apply_frequency()

    def update_amp_display(self, value):
        """更新幅度显示（电压）"""
        # DAC映射：0→+4.4V, 128→0V, 255→-4.4V (峰峰值8.8V)
        dac_max_voltage = 8.8

        # 电压（峰峰值）
        voltage = value * dac_max_voltage / 255
        self.amp_voltage_label.setText(f"({voltage:.2f}Vpp)")

    def setup_auto_apply(self):
        """设置自动应用：参数改变时自动发送（只发送改变的参数，避免波形飘移）"""
        # 波形改变时自动应用并更新电压显示
        self.wave_combo.currentIndexChanged.connect(self.on_wave_changed)
        # 频率改变时只发送频率（不发送相位，避免飘移）
        self.freq_spin.editingFinished.connect(self.auto_apply_frequency)
        self.freq_slider.sliderReleased.connect(self.auto_apply_frequency)
        # 相位改变时只发送相位
        self.phase_slider.sliderReleased.connect(self.auto_apply_phase)
        self.phase_spin.editingFinished.connect(self.auto_apply_phase)
        # 占空比改变时只发送占空比
        self.duty_slider.sliderReleased.connect(self.auto_apply_duty)
        self.duty_spin.editingFinished.connect(self.auto_apply_duty)
        # 幅度改变时只发送幅度（不发送相位，避免飘移）
        self.amp_slider.sliderReleased.connect(self.auto_apply_amplitude)
        self.amp_spin.editingFinished.connect(self.auto_apply_amplitude)
        # 使能改变时自动应用
        self.enable_check.stateChanged.connect(self.auto_apply_enable)

    def on_wave_changed(self):
        """波形类型改变时的处理"""
        # 显示/隐藏占空比控制（仅脉冲波形显示）
        wave_type = self.wave_combo.currentIndex()
        self.duty_group.setVisible(wave_type == 5)  # 5 = 脉冲波

        # 🔧 修复：切换到脉冲波时，设置合理的占空比初始值
        if wave_type == 5:  # 脉冲波
            current_duty = self.duty_spin.value()
            # 如果占空比太小（<0.1%）或太大（>99.9%），重置为50%
            if current_duty < 0.1 or current_duty > 99.9:
                self.duty_spin.setValue(50.0)
                self.duty_slider.setValue(5000)

        # 如果选择任意波形，提示打开编辑器
        if wave_type == 6:  # 6 = 任意波形
            parent = self.parent()
            while parent and not isinstance(parent, DDSController):
                parent = parent.parent()

            if parent:
                QMessageBox.information(
                    self,
                    "任意波形",
                    "已选择任意波形。\n请点击下方的【打开任意波形编辑器 (AWG)】按钮来编辑波形。",
                )

        self.update_amp_display(self.amp_spin.value())  # 更新电压显示

        # 波形改变时，同时更新两个通道保持同步
        parent = self.parent()
        while parent and not isinstance(parent, DDSController):
            parent = parent.parent()

        if parent and hasattr(parent, "serial_manager") and parent.serial_manager:
            # 同时发送两个通道的完整参数，保持相位同步
            parent.send_both_channels_params()

    def auto_apply_frequency(self):
        """自动应用频率参数（同时更新两个通道，保持同步）"""
        parent = self.parent()
        while parent and not isinstance(parent, DDSController):
            parent = parent.parent()

        if parent and hasattr(parent, "serial_manager") and parent.serial_manager:
            # 关键修复：同时发送两个通道的完整参数，保持相位同步
            parent.send_both_channels_params()

    def auto_apply_phase(self):
        """自动应用相位参数（只发送相位）"""
        parent = self.parent()
        while parent and not isinstance(parent, DDSController):
            parent = parent.parent()

        if parent and hasattr(parent, "serial_manager") and parent.serial_manager:
            parent.send_single_param(self.channel_id, "phase", self.phase_spin.value())

    def auto_apply_amplitude(self):
        """自动应用幅度参数（只发送幅度，不影响相位）"""
        parent = self.parent()
        while parent and not isinstance(parent, DDSController):
            parent = parent.parent()

        if parent and hasattr(parent, "serial_manager") and parent.serial_manager:
            parent.send_single_param(
                self.channel_id, "amplitude", self.amp_spin.value()
            )

    def auto_apply_duty(self):
        """自动应用占空比参数（只发送占空比）"""
        parent = self.parent()
        while parent and not isinstance(parent, DDSController):
            parent = parent.parent()

        if parent and hasattr(parent, "serial_manager") and parent.serial_manager:
            parent.send_single_param(
                self.channel_id, "duty_cycle", self.duty_spin.value()
            )

    def auto_apply_enable(self):
        """自动应用使能状态"""
        parent = self.parent()
        while parent and not isinstance(parent, DDSController):
            parent = parent.parent()

        if parent and hasattr(parent, "serial_manager") and parent.serial_manager:
            parent.update_enable()

    def get_params(self):
        """获取当前参数"""
        # 波形类型映射：界面索引 -> FPGA索引（交换锯齿波和反锯齿波）
        # 界面: 0=正弦 1=方波 2=三角 3=锯齿 4=反锯齿 5=脉冲 6=任意
        # FPGA: 0=正弦 1=方波 2=三角 3=锯齿 4=反锯齿 5=脉冲 6=任意
        # 映射: 3<->4 交换
        wave_type_ui = self.wave_combo.currentIndex()
        if wave_type_ui == 3:  # 界面的锯齿波 -> FPGA的反锯齿波
            wave_type_fpga = 4
        elif wave_type_ui == 4:  # 界面的反锯齿波 -> FPGA的锯齿波
            wave_type_fpga = 3
        else:
            wave_type_fpga = wave_type_ui

        return {
            "wave_type": wave_type_fpga,
            "freq_hz": int(self.freq_spin.value()),
            "phase_deg": self.phase_spin.value(),
            "amplitude": self.amp_spin.value(),
            "offset": 0,  # 直流偏置固定为0（已移除）
            "duty_cycle": self.duty_spin.value(),  # 占空比（0-100%，3位小数精度）
            "enable": self.enable_check.isChecked(),
        }

    def set_params(self, params):
        """设置参数"""
        # 波形类型反向映射：FPGA索引 -> 界面索引（交换锯齿波和反锯齿波）
        wave_type_fpga = params["wave_type"]
        if wave_type_fpga == 3:  # FPGA的锯齿波 -> 界面的反锯齿波
            wave_type_ui = 4
        elif wave_type_fpga == 4:  # FPGA的反锯齿波 -> 界面的锯齿波
            wave_type_ui = 3
        else:
            wave_type_ui = wave_type_fpga

        self.wave_combo.setCurrentIndex(wave_type_ui)
        self.freq_spin.setValue(params["freq_hz"])
        self.phase_spin.setValue(params["phase_deg"])
        self.amp_spin.setValue(params["amplitude"])
        self.duty_spin.setValue(params.get("duty_cycle", 50.0))  # 默认50.0%
        self.enable_check.setChecked(params["enable"])


# ============================================================================
# 主控制器（优化版 - 使用串口管理器）
# ============================================================================


class DDSController(QWidget):
    """DDS双通道控制器（优化版 - 使用统一串口管理器）"""

    def __init__(self, serial_manager=None):
        super().__init__()
        self.serial_manager = serial_manager  # 使用主程序提供的串口管理器
        self.awg_editor = None  # 任意波形编辑器窗口

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 移除固定高度限制，让组件可以自适应
        # 设置整体大小策略为可扩展
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 通道控制区 - 左右并排布局
        channels_layout = QHBoxLayout()
        channels_layout.setSpacing(5)
        self.channel_a = ChannelPanel("通道 A", "A")
        self.channel_a.setMinimumWidth(380)  # 减小最小宽度，允许更灵活布局
        self.channel_a.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        channels_layout.addWidget(self.channel_a)

        self.channel_b = ChannelPanel("通道 B", "B")
        self.channel_b.setMinimumWidth(380)
        self.channel_b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        channels_layout.addWidget(self.channel_b)
        main_layout.addLayout(channels_layout)

        # 任意波形编辑按钮（紧凑居中）
        awg_btn_layout = QHBoxLayout()
        awg_btn_layout.setContentsMargins(0, 2, 0, 2)  # 减小上下边距
        awg_btn_layout.setSpacing(0)
        awg_btn = QPushButton("打开任意波形编辑器 (AWG)")
        awg_btn.setMaximumWidth(200)
        awg_btn.setFixedHeight(28)  # 固定按钮高度
        awg_btn.clicked.connect(self.open_awg_editor)
        awg_btn_layout.addWidget(awg_btn, 0, Qt.AlignCenter)
        main_layout.addLayout(awg_btn_layout)

        # 相位差控制区（极致紧凑）
        phase_diff_group = QGroupBox("相位差设置（同频时有效）（B - A） ")
        phase_diff_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        phase_diff_group.setFixedHeight(105)  # 固定高度，减少留白
        phase_diff_layout = QGridLayout()
        phase_diff_layout.setContentsMargins(5, 3, 5, 3)  # 减小上下边距
        phase_diff_layout.setHorizontalSpacing(4)
        phase_diff_layout.setVerticalSpacing(2)  # 进一步减小垂直间距

        # 当前相位差显示
        phase_diff_layout.addWidget(QLabel("当前:"), 0, 0)
        self.phase_diff_label = QLabel("--")
        self.phase_diff_label.setStyleSheet("font-size: 10pt; font-weight: bold;")
        self.phase_diff_label.setFixedWidth(50)
        phase_diff_layout.addWidget(self.phase_diff_label, 0, 1)

        self.freq_match_label = QLabel("⚠ 频率不同")
        self.freq_match_label.setStyleSheet(
            "color: orange; font-weight: bold; font-size: 9pt;"
        )
        phase_diff_layout.addWidget(self.freq_match_label, 0, 2, 1, 4)

        # 直接设置相位差
        phase_diff_layout.addWidget(QLabel("设置:"), 1, 0)
        self.phase_diff_spin = QSpinBox()
        self.phase_diff_spin.setRange(-180, 180)
        self.phase_diff_spin.setValue(0)
        self.phase_diff_spin.setSuffix("°")
        self.phase_diff_spin.setMinimumWidth(75)  # 确保显示完整
        self.phase_diff_spin.setToolTip(
            "B通道相对A通道的相位差 (A通道固定为0°)\n正值：B超前A，负值：B滞后A"
        )
        phase_diff_layout.addWidget(self.phase_diff_spin, 1, 1)

        apply_phase_diff_btn = QPushButton("应用")
        apply_phase_diff_btn.setFixedWidth(55)
        apply_phase_diff_btn.clicked.connect(self.apply_phase_difference)
        phase_diff_layout.addWidget(apply_phase_diff_btn, 1, 2)

        # 快捷相位差按钮（与设置在同一行）
        phase_preset_layout = QHBoxLayout()
        phase_preset_layout.setSpacing(3)
        for phase in [0, -45, -90, 180, 90]:
            btn = QPushButton(f"{phase:+d}°" if phase != 0 else "0°")
            btn.setMinimumWidth(50)  # 确保文字显示完整
            btn.clicked.connect(lambda checked, p=phase: self.set_phase_diff_quick(p))
            phase_preset_layout.addWidget(btn)
        phase_diff_layout.addLayout(phase_preset_layout, 1, 3, 1, 3)

        # 相位差滑条（第二行）
        phase_diff_layout.addWidget(QLabel("滑条:"), 2, 0)
        self.phase_diff_slider = QSlider(Qt.Horizontal)
        self.phase_diff_slider.setRange(-180, 180)
        self.phase_diff_slider.setValue(0)
        self.phase_diff_slider.setTickPosition(QSlider.TicksBelow)
        self.phase_diff_slider.setTickInterval(45)
        self.phase_diff_slider.setFixedHeight(25)  # 减小滑块高度
        self.phase_diff_slider.setToolTip("拖动滑条实时调节相位差")
        phase_diff_layout.addWidget(self.phase_diff_slider, 2, 1, 1, 5)

        phase_diff_group.setLayout(phase_diff_layout)
        main_layout.addWidget(phase_diff_group)

        # 通信日志区（自适应高度，窗口缩放时由它来补偿）
        log_group = QGroupBox("通信日志")
        log_group.setMinimumHeight(150)  # 设置最小高度，防止被压缩太小
        log_group.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )  # 水平和垂直都可以扩展
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(5, 5, 5, 5)
        log_layout.setSpacing(3)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout.addWidget(self.log_text)

        clear_btn_layout = QHBoxLayout()
        clear_btn_layout.addStretch()
        clear_log_btn = QPushButton("清除日志")
        clear_log_btn.setMaximumWidth(80)
        clear_log_btn.setFixedHeight(24)
        clear_log_btn.clicked.connect(self.log_text.clear)
        clear_btn_layout.addWidget(clear_log_btn)
        log_layout.addLayout(clear_btn_layout)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group, 1)  # 拉伸因子为1，窗口变化时优先调整日志区大小

        # 连接滑条和数字框
        self.phase_diff_slider.valueChanged.connect(self.on_phase_diff_slider_changed)
        self.phase_diff_spin.valueChanged.connect(self.on_phase_diff_spin_changed)
        self.phase_diff_slider.sliderReleased.connect(self.apply_phase_difference)

        # 连接频率/相位改变信号到相位差更新
        self.channel_a.freq_spin.valueChanged.connect(self.update_phase_diff_display)
        self.channel_b.freq_spin.valueChanged.connect(self.update_phase_diff_display)
        self.channel_a.phase_spin.valueChanged.connect(self.update_phase_diff_display)
        self.channel_b.phase_spin.valueChanged.connect(self.update_phase_diff_display)

        # 初始化相位差显示
        self.update_phase_diff_display()

    def on_phase_diff_slider_changed(self, value):
        """相位差滑条改变时更新数字框"""
        self.phase_diff_spin.blockSignals(True)
        self.phase_diff_spin.setValue(value)
        self.phase_diff_spin.blockSignals(False)

    def on_phase_diff_spin_changed(self, value):
        """相位差数字框改变时更新滑条"""
        self.phase_diff_slider.blockSignals(True)
        self.phase_diff_slider.setValue(value)
        self.phase_diff_slider.blockSignals(False)

    def open_awg_editor(self):
        """打开任意波形编辑器"""
        if self.awg_editor is None:
            from dds.awg_editor import AWGEditor

            self.awg_editor = AWGEditor()
            self.awg_editor.send_waveform.connect(self.on_awg_send_waveform)

        self.awg_editor.show()
        self.awg_editor.raise_()
        self.awg_editor.activateWindow()

    def on_awg_send_waveform(self, channel, waveform_data):
        """接收AWG编辑器发送的波形数据"""
        if not self.serial_manager:
            QMessageBox.warning(self, "错误", "串口未连接！")
            return

        channel_panel = self.channel_a if channel == "A" else self.channel_b

        # 先切换到任意波形模式（wave_type = 6）
        if channel_panel.wave_combo.currentIndex() != 6:
            channel_panel.wave_combo.blockSignals(True)
            channel_panel.wave_combo.setCurrentIndex(6)
            channel_panel.wave_combo.blockSignals(False)
            # 延迟发送波形数据
            QTimer.singleShot(
                100, lambda: self._send_awg_data_step2(channel, waveform_data)
            )
        else:
            self._send_awg_data_step2(channel, waveform_data)

    def _send_awg_data_step2(self, channel, waveform_data):
        """步骤2：发送任意波形数据"""
        # 发送256字节波形数据
        cmd = 0x1E if channel == "A" else 0x1F
        payload = bytes(waveform_data)

        if len(payload) != 256:
            QMessageBox.warning(
                self, "错误", f"波形数据长度错误：{len(payload)}字节（应为256字节）"
            )
            return

        # 使用串口管理器发送命令
        if self.serial_manager:
            self.serial_manager.send_command(cmd, payload)

            # 🔧 修复：发送波形数据后，需要发送通道参数来激活任意波形模式
            # 延迟200ms后发送通道完整参数，确保FPGA已接收波形数据
            QTimer.singleShot(200, lambda: self._activate_awg_mode(channel))

        # 提示用户
        QMessageBox.information(
            self,
            "发送完成",
            f"已发送任意波形数据到通道{channel}\n通道{channel}已切换到任意波形模式",
        )

    def _activate_awg_mode(self, channel):
        """激活任意波形模式 - 同时发送两个通道参数保持相位同步"""
        if not self.serial_manager:
            return

        # 🔧 修复相位飘移bug：同时发送两个通道的完整参数，保持相位同步
        # 关键：无论哪个通道切换到任意波形，都要同时更新两个通道

        # 获取当前通道的参数并确保波形类型是任意波形（6）
        channel_panel = self.channel_a if channel == "A" else self.channel_b
        params = channel_panel.get_params()
        params["wave_type"] = 6

        # 先发送当前通道（任意波形）
        freq_word = calc_freq_word(params["freq_hz"])
        payload = struct.pack(
            ">BIHBbB",
            params["wave_type"],
            freq_word,
            int(params["phase_deg"]),
            int(params["amplitude"]),
            0,  # 偏置固定为0
            int(params["duty_cycle"]),
        )
        cmd = 0x19 if channel == "A" else 0x1A
        self.serial_manager.send_command(cmd, payload)

        if self.serial_manager and hasattr(self.serial_manager, "log_message"):
            self.serial_manager.log_message.emit(f"✅ 通道{channel}已激活任意波形模式")

        # 🔧 关键修复：延迟50ms后发送另一个通道的参数，保持相位同步
        other_channel = "B" if channel == "A" else "A"
        QTimer.singleShot(50, lambda: self._sync_other_channel_phase(other_channel))

    def _sync_other_channel_phase(self, channel):
        """同步另一个通道的相位（在一个通道切换到任意波形时调用）"""
        if not self.serial_manager:
            return

        # 获取另一个通道的当前参数
        channel_panel = self.channel_a if channel == "A" else self.channel_b
        params = channel_panel.get_params()

        # 发送完整参数（保持当前波形类型，不强制改为任意波形）
        freq_word = calc_freq_word(params["freq_hz"])
        payload = struct.pack(
            ">BIHBbB",
            params["wave_type"],
            freq_word,
            int(params["phase_deg"]),
            int(params["amplitude"]),
            0,  # 偏置固定为0
            int(params["duty_cycle"]),
        )
        cmd = 0x19 if channel == "A" else 0x1A
        self.serial_manager.send_command(cmd, payload)

        if self.serial_manager and hasattr(self.serial_manager, "log_message"):
            self.serial_manager.log_message.emit(
                f"🔄 同步通道{channel}相位，保持双通道同步"
            )

    def send_channel_all_params(self, channel_id, params):
        """发送通道全部参数"""
        if not self.serial_manager:
            return

        # 使用协议层函数计算频率字
        freq_word = calc_freq_word(params["freq_hz"])

        # 16位占空比转换：0-100% 映射到 0-65535（参考PWM模块）
        duty_pct = params["duty_cycle"]
        # 🔧 修复占空比反向问题：硬件逻辑与预期相反，需要取补
        duty_word = int((100.0 - duty_pct) * 655.35)  # 反向映射
        duty_word = max(0, min(65535, duty_word))  # 限制范围

        payload = struct.pack(
            ">BIHBBH",  # 16位占空比格式
            params["wave_type"],
            freq_word,
            int(params["phase_deg"]),
            int(params["amplitude"]),
            0,  # 偏置固定为0
            duty_word,  # 16位占空比
        )

        # 🐛 调试日志：输出详细参数
        wave_names = [
            "正弦波",
            "方波",
            "三角波",
            "锯齿波",
            "反锯齿波",
            "脉冲波",
            "任意波形",
        ]
        wave_name = (
            wave_names[params["wave_type"]]
            if params["wave_type"] < len(wave_names)
            else "未知"
        )

        debug_info = (
            f"[通道{channel_id}] 波形={wave_name}, "
            f"频率={params['freq_hz']}Hz, "
            f"相位={params['phase_deg']}°, "
            f"幅度={params['amplitude']}, "
            f"占空比={params['duty_cycle']:.3f}% (硬件值=0x{duty_word:04X}, 反向映射)"
        )

        if self.serial_manager and hasattr(self.serial_manager, "log_message"):
            self.serial_manager.log_message.emit(debug_info)

        cmd = 0x19 if channel_id == "A" else 0x1A
        # 使用 SerialManager 的 send_command 方法
        self.serial_manager.send_command(cmd, payload)

        # 🔧 WORKAROUND: 脉冲波形需要额外刷新占空比参数
        # 原因：FPGA端在第一次设置脉冲波时可能没有正确应用占空比
        if params["wave_type"] == 5:  # 5 = 脉冲波
            # 延迟100ms后单独发送占空比参数，强制FPGA刷新
            QTimer.singleShot(
                100, lambda: self._refresh_duty_cycle(channel_id, params["duty_cycle"])
            )

        # 更新使能状态
        QTimer.singleShot(150, lambda: self.update_enable())

    def _refresh_duty_cycle(self, channel_id, duty_cycle):
        """刷新占空比参数（16位精度版本）"""
        if not self.serial_manager:
            return

        cmd = 0x1C if channel_id == "A" else 0x1D
        # 16位占空比转换
        # 🔧 修复占空比反向问题：硬件逻辑与预期相反，需要取补
        duty_word = int((100.0 - duty_cycle) * 655.35)  # 反向映射
        duty_word = max(0, min(65535, duty_word))
        payload = struct.pack(">H", duty_word)  # 大端序16位
        self.serial_manager.send_command(cmd, payload)

        if hasattr(self.serial_manager, "log_message"):
            self.serial_manager.log_message.emit(
                f"🔄 刷新通道{channel_id}占空比: {duty_cycle:.3f}% (硬件值=0x{duty_word:04X}, 反向映射)"
            )

    def send_both_channels_params(self):
        """同时发送两个通道的完整参数（用于频率或波形改变时保持同步）"""
        # 先发送通道A
        params_a = self.channel_a.get_params()
        self.send_channel_all_params("A", params_a)

        # 延迟50ms后发送通道B，确保FPGA处理完通道A
        QTimer.singleShot(50, lambda: self._send_channel_b_delayed())

    def _send_channel_b_delayed(self):
        """延迟发送通道B参数"""
        params_b = self.channel_b.get_params()
        self.send_channel_all_params("B", params_b)

    def send_single_param(self, channel_id, param_type, value):
        """发送单个参数（避免波形飘移）"""
        if not self.serial_manager:
            return

        if param_type == "wave_type":
            cmd = 0x10 if channel_id == "A" else 0x11
            payload = struct.pack("B", value)
            self.serial_manager.send_command(cmd, payload)
        elif param_type == "frequency":
            cmd = 0x12 if channel_id == "A" else 0x13
            freq_word = calc_freq_word(value)
            payload = struct.pack(">I", freq_word)
            self.serial_manager.send_command(cmd, payload)
        elif param_type == "phase":
            cmd = 0x14 if channel_id == "A" else 0x15
            payload = struct.pack(">H", value)
            self.serial_manager.send_command(cmd, payload)
        elif param_type == "amplitude":
            cmd = 0x16 if channel_id == "A" else 0x17
            payload = struct.pack("B", value)
            self.serial_manager.send_command(cmd, payload)
        elif param_type == "duty_cycle":
            cmd = 0x1C if channel_id == "A" else 0x1D
            # 16位占空比：0-100% 映射到 0-65535
            # 🔧 修复占空比反向问题：硬件逻辑与预期相反，需要取补
            duty_word = int((100.0 - value) * 655.35)  # 反向映射
            duty_word = max(0, min(65535, duty_word))
            payload = struct.pack(">H", duty_word)  # 大端序16位
            self.serial_manager.send_command(cmd, payload)

    def update_enable(self):
        """更新通道使能状态"""
        if not self.serial_manager:
            return

        enable_flags = 0
        if self.channel_a.enable_check.isChecked():
            enable_flags |= 0x01
        if self.channel_b.enable_check.isChecked():
            enable_flags |= 0x02

        payload = struct.pack("B", enable_flags)
        self.serial_manager.send_command(0x18, payload)

    def update_phase_diff_display(self):
        """更新相位差显示"""
        freq_a = self.channel_a.freq_spin.value()
        freq_b = self.channel_b.freq_spin.value()
        phase_a = self.channel_a.phase_spin.value()
        phase_b = self.channel_b.phase_spin.value()

        # 检查频率是否相同（允许0.1Hz误差）
        freq_match = abs(freq_a - freq_b) < 0.1

        if freq_match:
            # 计算相位差（B相对于A）
            phase_diff = phase_b - phase_a
            # 归一化到-180到180度
            if phase_diff > 180:
                phase_diff -= 360
            elif phase_diff < -180:
                phase_diff += 360

            self.phase_diff_label.setText(f"{phase_diff:+d}°")
            self.phase_diff_label.setStyleSheet(
                "font-size: 12pt; font-weight: bold; color: green;"
            )
            self.freq_match_label.setText("✓ 同频")
            self.freq_match_label.setStyleSheet("color: green; font-weight: bold;")

            # 更新滑条和数字框（阻止信号避免循环）
            self.phase_diff_spin.blockSignals(True)
            self.phase_diff_slider.blockSignals(True)
            self.phase_diff_spin.setValue(phase_diff)
            self.phase_diff_slider.setValue(phase_diff)
            self.phase_diff_spin.blockSignals(False)
            self.phase_diff_slider.blockSignals(False)
        else:
            self.phase_diff_label.setText("--")
            self.phase_diff_label.setStyleSheet(
                "font-size: 12pt; font-weight: bold; color: gray;"
            )
            self.freq_match_label.setText("⚠ 频率不同")
            self.freq_match_label.setStyleSheet("color: orange; font-weight: bold;")

    def apply_phase_difference(self):
        """应用相位差设置"""
        freq_a = self.channel_a.freq_spin.value()
        freq_b = self.channel_b.freq_spin.value()

        # 检查是否同频
        if abs(freq_a - freq_b) >= 0.1:
            QMessageBox.warning(
                self,
                "频率不匹配",
                "相位差设置仅在两个通道频率相同时有效！\n"
                f"当前频率：\n通道A: {freq_a} Hz\n通道B: {freq_b} Hz",
            )
            return

        # 获取相位差
        phase_diff = self.phase_diff_spin.value()

        # A通道保持0度，B通道相位 = 0 + 相位差
        phase_a = 0
        phase_b = phase_diff % 360

        # 只有相位真正改变时才设置，避免重复发送造成波形飘移
        current_phase_a = self.channel_a.phase_spin.value()
        current_phase_b = self.channel_b.phase_spin.value()

        phase_changed = False

        # 设置A通道为0度
        if abs(current_phase_a - phase_a) > 0.5:
            self.channel_a.phase_spin.setValue(phase_a)
            phase_changed = True

        # 设置B通道相位
        if abs(current_phase_b - phase_b) > 0.5:
            self.channel_b.phase_spin.setValue(phase_b)
            phase_changed = True

        if phase_changed:
            # 同时发送两个通道的完整参数，保持相位同步
            QTimer.singleShot(50, self.send_both_channels_params)

        # 延迟更新显示
        QTimer.singleShot(200, self.update_phase_diff_display)

    def set_phase_diff_quick(self, phase_diff):
        """快速设置相位差"""
        self.phase_diff_spin.setValue(phase_diff)
        self.apply_phase_difference()


def main():
    """主程序入口（用于独立测试）"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = QWidget()
    window.setWindowTitle("DDS双通道控制器测试")
    window.setGeometry(100, 100, 1400, 900)

    layout = QVBoxLayout(window)
    dds_controller = DDSController()
    layout.addWidget(dds_controller)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
