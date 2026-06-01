#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
8路PWM控制器模块
日期: 2025-10-30 (更新)

功能:
  - 8路独立PWM控制
  - 频率范围: 1Hz - 1MHz
  - 占空比精度: 0.0015% (16位精度)
  - UI风格参考序列发生器

命令码:
  0x50: PWM配置 - [通道ID][频率(32bit)][占空比(16bit)]
  0x51: PWM使能 - [使能掩码]
  0x52: PWM停止 - []
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QDoubleSpinBox,
    QCheckBox,
    QTextEdit,
    QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
import struct


# 命令码定义
CMD_PWM_CONFIG = 0x50  # PWM配置
CMD_PWM_ENABLE = 0x51  # PWM使能控制
CMD_PWM_STOP = 0x52  # PWM停止


class PWMControllerTab(QWidget):
    """8路PWM控制器"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.pwm_channels = []  # 存储每个通道的控件引用
        self.motor_controller = None  # 电机控制器引用，用于同步状态
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # PWM通道配置区域 - 使用滚动区域
        channels_group = QGroupBox("通道配置 (8路独立PWM)")
        channels_main_layout = QVBoxLayout()
        channels_main_layout.setContentsMargins(4, 4, 4, 4)
        channels_main_layout.setSpacing(4)

        # 滚动区域（参考序列发生器串行模式）
        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(4)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        # 创建8路PWM控制
        for ch in range(8):
            channel_widget = self.create_pwm_channel_widget(ch)
            scroll_layout.addWidget(channel_widget)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        channels_main_layout.addWidget(scroll)

        channels_group.setLayout(channels_main_layout)
        main_layout.addWidget(channels_group)

        # 底部控制按钮
        control_layout = QHBoxLayout()

        # 左侧快捷操作
        self.enable_all_btn = QPushButton("全部使能")
        self.enable_all_btn.setMaximumWidth(100)
        self.enable_all_btn.clicked.connect(self.enable_all_channels)
        control_layout.addWidget(self.enable_all_btn)

        self.disable_all_btn = QPushButton("全部禁用")
        self.disable_all_btn.setMaximumWidth(100)
        self.disable_all_btn.clicked.connect(self.disable_all_channels)
        control_layout.addWidget(self.disable_all_btn)

        control_layout.addStretch()

        self.apply_btn = QPushButton("应用配置并启动")
        self.apply_btn.setMaximumWidth(140)
        self.apply_btn.clicked.connect(self.apply_all_channels)
        control_layout.addWidget(self.apply_btn)

        self.stop_btn = QPushButton("停止输出")
        self.stop_btn.setMaximumWidth(100)
        self.stop_btn.clicked.connect(self.stop_all_channels)
        control_layout.addWidget(self.stop_btn)

        main_layout.addLayout(control_layout)

        # 日志区
        log_group = QGroupBox("输出日志")
        log_group.setMaximumHeight(110)
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(80)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def create_pwm_channel_widget(self, channel_id):
        """创建单个PWM通道控制组件（优化布局，确保所有内容显示）"""
        from PySide6.QtWidgets import QGridLayout

        group = QGroupBox(f"通道 {channel_id} (PWM{channel_id})")
        layout = QGridLayout()
        layout.setSpacing(6)
        layout.setContentsMargins(8, 6, 8, 6)

        # 使能复选框
        enable_check = QCheckBox("使能")
        enable_check.setChecked(False)

        # 注释掉自动同步到电机的逻辑，让PWM可以独立测试
        # 如果需要同步，用户可以在电机控制页面手动操作
        # if channel_id in [0, 2, 4, 6]:
        #     enable_check.stateChanged.connect(
        #         lambda state, ch=channel_id: self.sync_pwm_to_motor(
        #             ch, state == Qt.Checked
        #         )
        #     )

        layout.addWidget(enable_check, 0, 0)

        # 频率设置
        layout.addWidget(QLabel("频率:"), 0, 1)
        freq_spin = QDoubleSpinBox()
        freq_spin.setRange(1, 1000000)  # 1Hz - 1MHz
        freq_spin.setValue(1000)
        freq_spin.setSuffix(" Hz")
        freq_spin.setMinimumWidth(120)
        layout.addWidget(freq_spin, 0, 2)

        # 快捷频率按钮 - 优化宽度确保文字显示完整
        freq_50hz_btn = QPushButton("50Hz")
        freq_50hz_btn.setMinimumWidth(55)
        freq_50hz_btn.setMaximumWidth(65)
        freq_50hz_btn.clicked.connect(lambda: freq_spin.setValue(50))
        layout.addWidget(freq_50hz_btn, 0, 3)

        freq_1k_btn = QPushButton("1kHz")
        freq_1k_btn.setMinimumWidth(55)
        freq_1k_btn.setMaximumWidth(65)
        freq_1k_btn.clicked.connect(lambda: freq_spin.setValue(1000))
        layout.addWidget(freq_1k_btn, 0, 4)

        freq_10k_btn = QPushButton("10kHz")
        freq_10k_btn.setMinimumWidth(55)
        freq_10k_btn.setMaximumWidth(65)
        freq_10k_btn.clicked.connect(lambda: freq_spin.setValue(10000))
        layout.addWidget(freq_10k_btn, 0, 5)

        # 周期显示（紧跟快捷按钮）
        layout.addWidget(QLabel("周期:"), 0, 6)
        period_label = QLabel("1.000 ms")
        period_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        period_label.setMinimumWidth(85)
        layout.addWidget(period_label, 0, 7)

        # 占空比设置
        layout.addWidget(QLabel("占空比:"), 1, 1)
        duty_spin = QDoubleSpinBox()
        duty_spin.setRange(0, 100)
        duty_spin.setValue(50)
        duty_spin.setSuffix(" %")
        duty_spin.setDecimals(2)  # 2位小数，精度0.01%
        duty_spin.setSingleStep(0.01)
        duty_spin.setMinimumWidth(120)
        layout.addWidget(duty_spin, 1, 2)

        # 占空比快捷按钮 - 优化宽度确保文字显示完整
        duty_25_btn = QPushButton("25%")
        duty_25_btn.setMinimumWidth(55)
        duty_25_btn.setMaximumWidth(65)
        duty_25_btn.clicked.connect(lambda: duty_spin.setValue(25))
        layout.addWidget(duty_25_btn, 1, 3)

        duty_50_btn = QPushButton("50%")
        duty_50_btn.setMinimumWidth(55)
        duty_50_btn.setMaximumWidth(65)
        duty_50_btn.clicked.connect(lambda: duty_spin.setValue(50))
        layout.addWidget(duty_50_btn, 1, 4)

        duty_75_btn = QPushButton("75%")
        duty_75_btn.setMinimumWidth(55)
        duty_75_btn.setMaximumWidth(65)
        duty_75_btn.clicked.connect(lambda: duty_spin.setValue(75))
        layout.addWidget(duty_75_btn, 1, 5)

        # 高电平时间显示
        layout.addWidget(QLabel("高电平:"), 1, 6)
        high_time_label = QLabel("0.500 ms")
        high_time_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        high_time_label.setMinimumWidth(85)
        layout.addWidget(high_time_label, 1, 7)

        # 连接信号更新显示
        def update_display():
            freq = freq_spin.value()
            duty = duty_spin.value()
            if freq > 0:
                period_ms = 1000.0 / freq
                high_time_ms = period_ms * (duty / 100.0)

                if period_ms >= 1:
                    period_label.setText(f"{period_ms:.3f} ms")
                else:
                    period_label.setText(f"{period_ms * 1000:.3f} μs")

                if high_time_ms >= 1:
                    high_time_label.setText(f"{high_time_ms:.3f} ms")
                else:
                    high_time_label.setText(f"{high_time_ms * 1000:.3f} μs")

        freq_spin.valueChanged.connect(update_display)
        duty_spin.valueChanged.connect(update_display)

        # 初始化显示
        update_display()

        group.setLayout(layout)

        # 保存控件引用
        channel_data = {
            "group": group,
            "enable": enable_check,
            "freq": freq_spin,
            "duty": duty_spin,
            "period_label": period_label,
            "high_time_label": high_time_label,
        }
        self.pwm_channels.append(channel_data)

        return group

    def enable_all_channels(self):
        """全部使能快捷按钮"""
        for ch_data in self.pwm_channels:
            ch_data["enable"].setChecked(True)
        self.log_text.append("✅ 已全部使能所有通道")

    def disable_all_channels(self):
        """全部禁用快捷按钮"""
        for ch_data in self.pwm_channels:
            ch_data["enable"].setChecked(False)
        self.log_text.append("⏹ 已禁用所有通道")

    def set_motor_controller(self, motor_controller):
        """设置电机控制器引用（用于PWM到电机的状态同步）"""
        self.motor_controller = motor_controller

    def sync_pwm_to_motor(self, pwm_channel, enabled):
        """同步PWM通道使能状态到电机控制"""
        if self.motor_controller is None:
            return

        # PWM通道0,2,4,6对应电机1,2,3,4
        motor_map = {0: 0, 2: 1, 4: 2, 6: 3}
        if pwm_channel not in motor_map:
            return

        motor_id = motor_map[pwm_channel]
        try:
            motor_data = self.motor_controller.motors[motor_id]

            # 暂时阻止信号，避免循环触发
            motor_data["enable"].blockSignals(True)
            motor_data["enable"].setChecked(enabled)
            motor_data["enable"].blockSignals(False)
        except (IndexError, AttributeError, KeyError):
            pass

    def apply_all_channels(self):
        """应用所有通道配置"""
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_text.append("❌ 错误: CDC串口未连接")
            QMessageBox.warning(self, "串口未连接", "请先连接CDC串口")
            return

        self.log_text.append("=" * 50)
        self.log_text.append("🚀 开始配置PWM...")

        enable_mask = 0x00
        success_count = 0

        # 导入time模块用于延时
        import time

        # 配置每个使能的通道
        for ch in range(8):
            ch_data = self.pwm_channels[ch]
            if ch_data["enable"].isChecked():
                freq_hz = int(ch_data["freq"].value())
                duty_pct = ch_data["duty"].value()

                # 发送配置命令
                if self.send_pwm_config(ch, freq_hz, duty_pct):
                    enable_mask |= 1 << ch
                    success_count += 1
                    self.log_text.append(f"✓ PWM{ch}: {freq_hz}Hz, {duty_pct:.2f}%")
                    # ⏱️ 添加小延时，确保FPGA处理完成
                    time.sleep(0.01)  # 10ms延时
                else:
                    self.log_text.append(f"✗ PWM{ch}: 配置失败")

        # 发送使能命令
        if enable_mask != 0:
            if self.send_pwm_enable(enable_mask):
                self.log_text.append(
                    f"✅ 配置完成! 已使能 {success_count} 个通道 (掩码: 0x{enable_mask:02X})"
                )
            else:
                self.log_text.append("❌ 使能命令发送失败")
        else:
            self.log_text.append("⚠️ 没有使能的通道")

    def send_pwm_config(self, channel_id, freq_hz, duty_pct):
        """发送PWM配置命令"""
        # Payload: [通道ID][频率字(32bit)][占空比(16bit)]
        # ⚠️ 改进：在上位机精确计算频率字，避免FPGA内部近似计算导致的精度损失
        # 频率字 = (freq_hz * 2^32) / 50MHz（参考DDS模块的精确计算）
        freq_word = int((freq_hz * 4294967296) / 50000000)  # 精确计算

        # 占空比: 0-100% 映射到 0-65535
        duty_word = int(duty_pct * 655.35)  # 0-100% -> 0-65535

        payload = struct.pack(">BIH", channel_id, freq_word, duty_word)  # 大端序

        # 调试输出：打印实际发送的数据
        hex_str = " ".join(f"{b:02X}" for b in payload)
        self.log_text.append(f"  📤 CH{channel_id} Payload: {hex_str}")
        self.log_text.append(
            f"     频率={freq_hz}Hz (freq_word=0x{freq_word:08X}), 占空比={duty_pct:.2f}% (0x{duty_word:04X})"
        )

        return self.serial_manager.send_command(CMD_PWM_CONFIG, payload)

    def send_pwm_enable(self, enable_mask):
        """发送PWM使能命令"""
        payload = struct.pack("B", enable_mask)
        return self.serial_manager.send_command(CMD_PWM_ENABLE, payload)

    def stop_all_channels(self):
        """停止所有PWM输出（不改变使能复选框状态）"""
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_text.append("❌ 错误: CDC串口未连接")
            return

        # 发送停止命令(空payload)
        if self.serial_manager.send_command(CMD_PWM_STOP, b""):
            self.log_text.append("⏹ 已停止所有PWM通道输出（配置保持）")
            # 不取消使能复选框，下次启动时仍然保持配置
        else:
            self.log_text.append("❌ 停止命令发送失败")
