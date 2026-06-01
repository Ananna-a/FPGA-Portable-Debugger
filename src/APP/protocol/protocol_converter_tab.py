#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
协议转换器模块 - 整合序列发生器、PWM控制器、设备中心
版本: V2.0
日期: 2025-10-30

更新:
  ✅ 集成全新设备中心UI (6大外设控制)
  ✅ 电机控制复用PWM0/2/4/6通道
  ✅ 双向通信协议增加交互日志区
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTabWidget,
)
from PySide6.QtCore import Qt


class ProtocolConverterTab(QWidget):
    """协议转换器主标签页 - 包含3个子功能"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # 子标签页
        sub_tabs = QTabWidget()

        # 导入各个模块
        from .logic_analyzer_tab import LogicAnalyzerTab
        from .pwm_controller_tab import PWMControllerTab
        from .device_center_tab import DeviceCenterTab  # 新模块

        # 创建logic_analyzer_tab实例（包含原始的序列发生器）
        logic_tab = LogicAnalyzerTab(self.serial_manager)

        # Tab 1: 序列发生器（使用原始的create_sequence_output_page方法）
        sequence_page = logic_tab.create_sequence_output_page()
        sub_tabs.addTab(sequence_page, "📊 序列发生器")

        # Tab 2: PWM控制器
        pwm_page = PWMControllerTab(self.serial_manager)
        sub_tabs.addTab(pwm_page, "⚡ PWM控制器")

        # Tab 3: 设备中心（独立运行，不传递pwm_controller引用）
        device_page = DeviceCenterTab(self.serial_manager, pwm_controller=None)
        sub_tabs.addTab(device_page, "🔌 设备中心")

        # 移除双向引用设置，让PWM和电机完全独立
        # pwm_page.set_motor_controller(device_page.motor_panel)
        # device_page.motor_panel.pwm_controller = pwm_page

        # 保存各模块引用，以便访问其方法和属性
        self.logic_tab = logic_tab
        self.pwm_tab = pwm_page
        self.device_tab = device_page

        main_layout.addWidget(sub_tabs)
