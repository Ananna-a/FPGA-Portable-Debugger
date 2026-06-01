#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多功能协议调试器 - 主程序
版本: V1.4 (5大功能板块)
日期: 2025-01-29

5大功能板块：
  📊 示波器           - 双通道示波器显示
  📡 函数发生器       - DDS双通道信号发生器
  🔧 协议转换器       - 序列发生器 + PWM控制器 + 设备中心
  🔬 逻辑分析仪       - 8通道逻辑分析（独立窗口，模仿Saleae Logic）
  📈 波特图           - 频率响应分析

架构特点：
  ✅ 5大板块清晰分工
  ✅ 逻辑分析仪独立大窗口（1400x900）
  ✅ 串口管理统一
  ✅ 模块完全解耦
"""

import sys
import warnings

# 🔥 V8.7.61: 抑制PyQtGraph的overflow警告（不影响功能）
warnings.filterwarnings(
    "ignore", category=RuntimeWarning, message="overflow encountered in cast"
)
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pyqtgraph")

import serial.tools.list_ports
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QStatusBar,
    QComboBox,
    QGroupBox,
    QGridLayout,
    QTextEdit,
    QSizePolicy,
    QScrollArea,
    QCheckBox,
    QFileDialog,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QFont, QPixmap

# 导入串口管理器和各模块界面
from core.serial_manager import SerialManager
from dds.dds_gui_dual import DDSController
from scope.oscilloscope_tab import OscilloscopeTab
from protocol.protocol_converter_tab import ProtocolConverterTab
from bode.bode_plotter_tab import BodePlotterTab
from logic_analyzer.logic_analyzer_pulseview_tab import LogicAnalyzerPulseViewTab


class MainApplication(QMainWindow):
    """主应用程序窗口（优化版）"""

    def __init__(self):
        super().__init__()

        # 创建串口管理器（核心）
        self.serial_manager = SerialManager()

        # 连接日志信号
        self.serial_manager.log_message.connect(self.on_serial_log)
        self.serial_manager.connected.connect(self.on_serial_connected)
        self.serial_manager.disconnected.connect(self.on_serial_disconnected)

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("多功能协议调试器 V1.3")
        self.setGeometry(100, 50, 1150, 820)  # 初始窗口大小
        self.setMinimumSize(1000, 750)  # 最小尺寸：确保所有固定控件完整显示

        # 创建主控件和滚动区域
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(5)

        # 串口连接面板（固定不滚动）
        serial_panel = self.create_serial_panel()
        main_layout.addWidget(serial_panel)

        # 创建标签页控件（可滚动）
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setMovable(False)
        self.tabs.currentChanged.connect(self.on_tab_changed)

        # 设置标签页的大小策略，使其可以扩展
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ============ 5大功能板块 ============

        # Tab 0: 示波器（传递serial_manager）- 使用重构版V2
        self.oscilloscope_widget = self.wrap_in_scroll_area(
            OscilloscopeTab(self.serial_manager)
        )
        self.tabs.addTab(self.oscilloscope_widget, "📊 示波器")

        # Tab 1: 函数发生器
        self.dds_widget = DDSController(self.serial_manager)
        self.tabs.addTab(self.dds_widget, "📡 函数发生器")

        # Tab 2: 协议转换器（组合：序列发生器 + PWM + 设备中心）
        self.protocol_converter_widget = self.wrap_in_scroll_area(
            ProtocolConverterTab(self.serial_manager)
        )
        self.tabs.addTab(self.protocol_converter_widget, "� 协议转换器")

        # Tab 3: 逻辑分析仪（新版：PulseView兼容）
        self.logic_analyzer_widget = self.wrap_in_scroll_area(
            LogicAnalyzerPulseViewTab(self.serial_manager)
        )
        self.tabs.addTab(self.logic_analyzer_widget, "🔬 逻辑分析仪")

        # Tab 4: 波特图分析
        self.bode_plotter_widget = self.wrap_in_scroll_area(
            BodePlotterTab(self.serial_manager)
        )
        self.tabs.addTab(self.bode_plotter_widget, "📈 波特仪")

        # Tab 5: 调试日志（全局应答帧/系统消息）
        self.debug_log_widget = self.create_debug_log_tab()
        self.tabs.addTab(self.debug_log_widget, "📋 调试日志")

        main_layout.addWidget(self.tabs)

        # 状态栏
        self.statusBar().showMessage("就绪 | 请先连接CDC串口")

        # 设置全局样式
        self.apply_global_style()

    def wrap_in_scroll_area(self, widget):
        """将组件包装在滚动区域中，解决堆叠和缩放问题"""
        scroll_area = QScrollArea()
        scroll_area.setWidget(widget)
        scroll_area.setWidgetResizable(True)  # 关键：让内部widget可以自适应
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QScrollArea.NoFrame)  # 无边框，更美观

        # 设置滚动区域的大小策略
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        return scroll_area

    # ===== 以下函数已废弃，使用新的集成式逻辑分析仪标签页 =====
    # def create_logic_analyzer_launcher(self):
    #     """创建逻辑分析仪启动器页面"""
    #     ...已移除，改用 LogicAnalyzerPulseViewTab

    # def launch_logic_analyzer_window(self):
    #     """启动独立的逻辑分析仪窗口"""
    #     ...已移除，改用 LogicAnalyzerPulseViewTab

    def create_debug_log_tab(self):
        """创建全局调试日志标签页"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 顶部工具栏
        toolbar = QGroupBox("日志选项")
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)

        # 时间戳开关
        self.log_timestamp_checkbox = QCheckBox("显示时间戳")
        self.log_timestamp_checkbox.setChecked(True)
        toolbar_layout.addWidget(self.log_timestamp_checkbox)

        # 清除按钮
        clear_btn = QPushButton("清除日志")
        clear_btn.clicked.connect(self.clear_debug_log)
        toolbar_layout.addWidget(clear_btn)

        # 导出按钮
        export_btn = QPushButton("导出日志")
        export_btn.clicked.connect(self.export_debug_log)
        toolbar_layout.addWidget(export_btn)

        toolbar_layout.addStretch()
        toolbar.setLayout(toolbar_layout)
        layout.addWidget(toolbar)

        # 日志显示区
        log_group = QGroupBox("📋 系统日志 (应答帧/调试信息)")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)

        self.debug_log_text = QTextEdit()
        self.debug_log_text.setReadOnly(True)
        self.debug_log_text.setFont(QFont("Consolas", 9))
        self.debug_log_text.setPlaceholderText(
            "系统调试日志区域\n\n"
            "• 应答帧会自动显示在这里\n"
            "• 带时间戳，方便反向解析执行的命令\n"
            "• 其他设备的调试信息也会显示在这里\n"
        )
        log_layout.addWidget(self.debug_log_text)

        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        return page

    # ===== 已废弃：独立逻辑分析仪窗口功能（改用集成式标签页） =====
    # def launch_logic_analyzer_window(self):
    #     """启动独立的逻辑分析仪窗口"""
    #     已移除，改用 LogicAnalyzerPulseViewTab 集成式标签页

    def create_serial_panel(self):
        """创建串口连接面板（统一管理）- 优化布局"""
        group = QGroupBox("串口配置 (CDC双串口模式)")
        group.setFixedHeight(90)  # 固定高度
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # 使用HBoxLayout实现紧凑布局
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(8)  # 减小整体间距

        # 左侧：CDC发送串口
        cdc_label = QLabel("CDC发送:")
        cdc_label.setFixedWidth(70)
        cdc_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        main_layout.addWidget(cdc_label)

        self.port_tx_combo = QComboBox()
        self.port_tx_combo.setMinimumWidth(200)  # 稍微缩短
        main_layout.addWidget(self.port_tx_combo)

        main_layout.addSpacing(12)  # 区域间距

        # 中间：CH340接收串口
        ch340_label = QLabel("CH340接收:")
        ch340_label.setFixedWidth(80)
        ch340_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        main_layout.addWidget(ch340_label)

        self.port_rx_combo = QComboBox()
        self.port_rx_combo.setMinimumWidth(260)  # 加长显示完整端口号
        main_layout.addWidget(self.port_rx_combo)

        main_layout.addSpacing(12)  # 区域间距

        # 波特率（固定115200，禁用下拉框更美观）
        baud_label = QLabel("波特率:")
        baud_label.setFixedWidth(55)
        baud_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        main_layout.addWidget(baud_label)

        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200"])
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setEnabled(False)  # 禁用下拉
        self.baud_combo.setMinimumWidth(90)  # 使用最小宽度，与其他下拉框一致
        self.baud_combo.setStyleSheet(
            "QComboBox:disabled {"
            "  background-color: #f5f5f5;"
            "  color: #666;"
            "  border: 1px solid #d0d0d0;"
            "}"
        )
        main_layout.addWidget(self.baud_combo)

        main_layout.addSpacing(8)  # 区域间距

        # 按钮
        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.setFixedWidth(70)  # 稍微缩短
        self.refresh_btn.clicked.connect(self.load_serial_ports)
        main_layout.addWidget(self.refresh_btn)

        self.connect_btn = QPushButton("🔌 连接")
        self.connect_btn.setFixedWidth(70)  # 稍微缩短
        self.connect_btn.clicked.connect(self.toggle_connection)
        main_layout.addWidget(self.connect_btn)

        main_layout.addSpacing(8)  # 区域间距

        # 右侧：连接状态指示
        self.connection_status_label = QLabel("● 未连接")
        self.connection_status_label.setStyleSheet(
            "color: gray; font-weight: bold; font-size: 10pt; padding: 3px 8px;"
        )
        self.connection_status_label.setFixedWidth(75)  # 稍微缩短
        self.connection_status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.connection_status_label)

        main_layout.addStretch()

        group.setLayout(main_layout)

        # 初始加载串口列表
        self.load_serial_ports()

        return group

    def load_serial_ports(self):
        """加载可用串口列表"""
        self.port_tx_combo.clear()
        self.port_rx_combo.clear()

        ports = self.serial_manager.get_available_ports()
        if len(ports) == 0:
            self.port_tx_combo.addItem("无可用串口")
            self.port_rx_combo.addItem("无可用串口")
            return

        for device, desc in ports:
            display_text = f"{device} - {desc}"
            self.port_tx_combo.addItem(display_text)
            self.port_rx_combo.addItem(display_text)

        # 智能选择默认端口
        for i in range(self.port_tx_combo.count()):
            text = self.port_tx_combo.itemText(i)
            if "COM15" in text or "CDC" in text.upper():
                self.port_tx_combo.setCurrentIndex(i)
            if "COM24" in text or "CH340" in text.upper():
                self.port_rx_combo.setCurrentIndex(i)

    def toggle_connection(self):
        """切换串口连接状态"""
        if self.serial_manager.is_connected():
            # 断开连接
            self.serial_manager.disconnect()
        else:
            # 建立连接
            port_tx_text = self.port_tx_combo.currentText()
            port_rx_text = self.port_rx_combo.currentText()

            if "无可用串口" in port_tx_text or "无可用串口" in port_rx_text:
                QMessageBox.warning(self, "错误", "没有可用的串口")
                return

            port_tx = port_tx_text.split(" - ")[0]
            port_rx = port_rx_text.split(" - ")[0]
            baud_rate = 115200  # 固定115200波特率

            # 尝试连接
            if not self.serial_manager.connect(port_tx, port_rx, baud_rate):
                QMessageBox.critical(self, "连接失败", "无法打开串口，请检查设备连接")

    def on_serial_connected(self, tx_port, rx_port):
        """串口连接成功回调"""
        self.connect_btn.setText("🔌 断开")
        self.connection_status_label.setText("● 已连接")
        self.connection_status_label.setStyleSheet("color: green; font-weight: bold;")
        self.statusBar().showMessage(f"已连接: TX={tx_port}, RX={rx_port}")

    def on_serial_disconnected(self):
        """串口断开回调"""
        self.connect_btn.setText("🔌 连接")
        self.connection_status_label.setText("● 未连接")
        self.connection_status_label.setStyleSheet("color: gray; font-weight: bold;")
        self.statusBar().showMessage("串口已断开")

    def on_serial_log(self, message):
        """串口日志回调 - 智能分流：应答帧/以太网日志去调试日志，其他去当前标签页"""

        # 🔥 V8.7.14.7: 判断是否为需要转发到调试日志的消息
        # 1. CH340应答帧
        # 2. 以太网相关日志
        # 3. 系统状态信息
        # 4. Buffer模式调试信息
        is_debug_log_message = (
            "[RX←CH340]" in message
            or "状态:" in message
            or "[以太网]" in message
            or "[TX→CDC]" in message
            or "📡" in message
            or "✅" in message
            or "❌" in message
            or "CH1显示" in message  # Buffer模式调试
            or "CH2显示" in message  # Buffer模式调试
            or "位置1024" in message  # Buffer模式调试
            or "[DEBUG]" in message  # 通用DEBUG日志
            or "🔍" in message  # DEBUG标记
        )

        if is_debug_log_message:
            # 应答帧/以太网日志/系统消息 → 转发到调试日志标签页
            if "[RX←CH340]" in message:
                msg_type = "RESPONSE"
            elif "[以太网]" in message:
                msg_type = "INFO"
            elif "❌" in message or "失败" in message:
                msg_type = "ERROR"
            elif "✅" in message or "成功" in message:
                msg_type = "SUCCESS"
            elif "⚠️" in message:
                msg_type = "WARNING"
            else:
                msg_type = "INFO"

            self.append_debug_log(message, msg_type)
        else:
            # 其他交互信息（发送命令等）→ 只转发到当前标签页
            current_widget = self.tabs.currentWidget()

            # 函数发生器标签页不使用滚动区域，其他标签页使用
            if isinstance(current_widget, QScrollArea):
                current_widget = current_widget.widget()

            if hasattr(current_widget, "log_text"):
                current_widget.log_text.append(message)
                scrollbar = current_widget.log_text.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())

    def append_debug_log(self, message, msg_type="INFO"):
        """追加调试日志到全局日志窗口
        Args:
            message: 日志消息
            msg_type: 消息类型 (INFO/RESPONSE/ERROR/WARNING)
        """
        from datetime import datetime

        # 时间戳
        timestamp = ""
        # 🔥 修复：检查控件是否存在
        if (
            hasattr(self, "log_timestamp_checkbox")
            and self.log_timestamp_checkbox.isChecked()
        ):
            timestamp = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "

        # 类型图标
        icon_map = {
            "INFO": "ℹ️",
            "RESPONSE": "🔍",
            "ERROR": "❌",
            "WARNING": "⚠️",
            "SUCCESS": "✅",
        }
        icon = icon_map.get(msg_type, "📝")

        # 格式化消息
        formatted_msg = f"{timestamp}{icon} {message}"

        # 🔥 修复：检查控件是否存在
        if not hasattr(self, "debug_log_text"):
            # UI还未初始化完成，先打印到控制台
            print(formatted_msg)
            return

        # 追加到日志
        self.debug_log_text.append(formatted_msg)

        # 自动滚动到底部
        scrollbar = self.debug_log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_debug_log(self):
        """清除调试日志"""
        self.debug_log_text.clear()
        self.append_debug_log("日志已清除", "INFO")

    def export_debug_log(self):
        """导出调试日志到文件"""
        from datetime import datetime
        from PySide6.QtWidgets import QFileDialog

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "导出调试日志",
            f"debug_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;All Files (*)",
        )

        if filename:
            try:
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(self.debug_log_text.toPlainText())
                self.append_debug_log(f"日志已导出到: {filename}", "SUCCESS")
                QMessageBox.information(self, "导出成功", f"日志已保存到:\n{filename}")
            except Exception as e:
                self.append_debug_log(f"导出失败: {str(e)}", "ERROR")
                QMessageBox.critical(self, "导出失败", f"无法保存日志文件:\n{str(e)}")

    def create_title_bar(self):
        """创建顶部标题栏 - 🎨 简洁风格"""
        title_widget = QWidget()
        title_layout = QHBoxLayout(title_widget)
        title_layout.setContentsMargins(8, 3, 8, 3)  # 🎨 更紧凑的边距
        title_layout.setSpacing(8)
        # 🎨 限制标题栏高度
        title_widget.setMaximumHeight(45)

        # Logo图片
        import os

        logo_path = os.path.join(os.path.dirname(__file__), "picture", "logo.png")
        if os.path.exists(logo_path):
            logo_label = QLabel()
            logo_pixmap = QPixmap(logo_path)
            logo_pixmap = logo_pixmap.scaledToHeight(
                32, Qt.SmoothTransformation
            )  # 🎨 稍小图标
            logo_label.setPixmap(logo_pixmap)
            title_layout.addWidget(logo_label)

        # 标题
        title_label = QLabel("多功能协议调试器")
        title_label.setFont(QFont("微软雅黑", 12, QFont.Bold))  # 🎨 稍小字体
        title_layout.addWidget(title_label)

        title_layout.addStretch()

        # 版本信息
        version_label = QLabel("V1.3 (优化版)")
        version_label.setStyleSheet("color: #666; font-size: 10pt;")  # 🎨 调整字号
        title_layout.addWidget(version_label)

        # 关于按钮
        about_btn = QPushButton("关于")
        about_btn.setMaximumWidth(60)
        about_btn.clicked.connect(self.show_about)
        title_layout.addWidget(about_btn)

        return title_widget

    def on_tab_changed(self, index):
        """标签页切换事件"""
        tab_names = [
            "示波器",
            "函数发生器",
            "协议转换器",
            "逻辑分析仪",
            "波特图",
            "调试日志",
        ]
        if index < len(tab_names):
            self.statusBar().showMessage(f"当前模块: {tab_names[index]}")

            # 如果切换到未完成的模块，显示提示
            if index in [4]:  # 波特图未完成
                QTimer.singleShot(
                    500,
                    lambda: self.statusBar().showMessage(
                        f"提示: {tab_names[index]}模块尚未实现",
                        5000,
                    ),
                )

    def show_about(self):
        """显示关于对话框"""
        about_text = """
        <h2>多功能协议调试器 V1.3 (优化版)</h2>
        <p><b>平台:</b> 高云 ACX720 + FX2 + DDR3 + ADC + DAC</p>
        <p><b>通信:</b> USB CDC（控制）+ 以太网UDP（数据）</p>
        
        <h3>V1.3 架构优化:</h3>
        <ul>
            <li>✅ 串口管理统一到主程序</li>
            <li>✅ 公共协议层独立 (serial_protocol.py)</li>
            <li>✅ 串口管理器模块化 (serial_manager.py)</li>
            <li>✅ 模块解耦，风格统一</li>
        </ul>
        
        <h3>功能模块状态:</h3>
        <ul>
            <li>✅ <b>函数发生器</b>: 完全实现
                <ul>
                    <li>双通道DDS波形发生器（7种波形）</li>
                    <li>频率范围: 1Hz - 50MHz</li>
                    <li>任意波形编辑器（手绘、表达式、CSV）</li>
                    <li>相位差精确控制</li>
                </ul>
            </li>
            <li>🚧 <b>示波器</b>: 待实现
                <ul>
                    <li>双通道ADC采集</li>
                    <li>时域/频域分析</li>
                </ul>
            </li>
            <li>✅ <b>序列发生器</b>: 部分完成
                <ul>
                    <li>8通道并行/串行序列输出</li>
                    <li>协议分析待完成</li>
                </ul>
            </li>
            <li>🚧 <b>波特图分析</b>: 待实现</li>
        </ul>
        
        <p><b>开发日期:</b> 2025年10月28日</p>
        <p><b>协议版本:</b> V2.2（CDC命令帧 55 AA，应答帧 AA 55）</p>
        """

        QMessageBox.about(self, "关于", about_text)

    def apply_global_style(self):
        """应用全局样式（简洁风格）"""
        style = """
        QMainWindow {
            background-color: #f5f5f5;
        }
        
        QTabWidget::pane {
            border: 1px solid #cccccc;
            background-color: white;
        }
        
        QTabBar::tab {
            background-color: #e0e0e0;
            color: black;
            padding: 8px 16px;
            margin-right: 2px;
            font-size: 10pt;
        }
        
        QTabBar::tab:selected {
            background-color: white;
            color: #333;
            font-weight: bold;
            border-bottom: 2px solid #2196F3;
        }
        
        QTabBar::tab:hover {
            background-color: #f0f0f0;
        }
        
        QPushButton {
            background-color: #f0f0f0;
            color: black;
            border: 1px solid #ccc;
            padding: 6px 12px;
            border-radius: 3px;
            font-size: 9pt;
        }
        
        QPushButton:hover {
            background-color: #e0e0e0;
            border: 1px solid #aaa;
        }
        
        QPushButton:pressed {
            background-color: #d0d0d0;
        }
        
        QPushButton:disabled {
            background-color: #f5f5f5;
            color: #999;
        }
        
        QGroupBox {
            border: 1px solid #ddd;
            border-radius: 3px;
            margin-top: 8px;
            font-weight: bold;
            padding-top: 8px;
        }
        
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 3px 8px;
            color: #555;
        }
        
        QStatusBar {
            background-color: #e0e0e0;
            color: #333;
            font-size: 9pt;
        }
        
        /* 滚动条美化 */
        QScrollBar:vertical {
            border: none;
            background: #f0f0f0;
            width: 12px;
            margin: 0px;
        }
        
        QScrollBar::handle:vertical {
            background: #c0c0c0;
            min-height: 20px;
            border-radius: 6px;
        }
        
        QScrollBar::handle:vertical:hover {
            background: #a0a0a0;
        }
        
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        
        QScrollBar:horizontal {
            border: none;
            background: #f0f0f0;
            height: 12px;
            margin: 0px;
        }
        
        QScrollBar::handle:horizontal {
            background: #c0c0c0;
            min-width: 20px;
            border-radius: 6px;
        }
        
        QScrollBar::handle:horizontal:hover {
            background: #a0a0a0;
        }
        
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        """
        self.setStyleSheet(style)

    def closeEvent(self, event):
        """关闭窗口事件"""
        reply = QMessageBox.question(
            self,
            "确认退出",
            "确定要退出多功能协议调试器吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # 关闭所有子模块的资源
            if hasattr(self, "dds_widget"):
                inner_widget = (
                    self.dds_widget.widget()
                    if isinstance(self.dds_widget, QScrollArea)
                    else self.dds_widget
                )
                if hasattr(inner_widget, "closeEvent"):
                    inner_widget.close()

            # 断开串口连接
            if self.serial_manager.is_connected():
                self.serial_manager.disconnect()

            event.accept()
        else:
            event.ignore()


def main():
    """主程序入口"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 设置应用程序信息
    app.setApplicationName("多功能协议调试器")
    app.setApplicationVersion("1.3")
    app.setOrganizationName("GW_FPGA")

    # 创建主窗口
    window = MainApplication()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
