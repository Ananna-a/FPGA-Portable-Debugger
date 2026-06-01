#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设备中心模块 - 外设控制与调试
日期: 2025-10-30

功能模块：
  ✅ 4路电机控制 (复用PWM0/2/4/6)
  ✅ I2C设备控制 (OLED SSD1306)
  ✅ SPI设备控制 (W25Q128 Flash)
  ✅ UART设备控制 (蓝牙模块 HC-06)
  ✅ CAN总线控制 (TJA1050/SN65HVD230)
  ✅ 1-Wire设备控制 (DS18B20温度传感器)

通信架构：
  - 控制命令: USB CDC (上位机 → FPGA)
  - 应答数据: CH340 UART (FPGA → 上位机)

命令码分配 (0x60-0x9F):
  0x60-0x6F: 电机控制
  0x70-0x7F: I2C设备
  0x80-0x8F: SPI设备
  0x90-0x9F: UART设备
  0xA0-0xAF: CAN设备
  0xB0-0xBF: 1-Wire设备
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QTextEdit,
    QLineEdit,
    QCheckBox,
    QSlider,
    QMessageBox,
    QTabWidget,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
import struct


# ============================================================================
# 命令码定义
# ============================================================================

# 电机控制命令 (0x60-0x6F)
CMD_MOTOR_CONFIG = 0x60  # 电机配置 [motor_id][freq_word(32)][duty(16)]
CMD_MOTOR_START = 0x61  # 启动电机 [motor_mask]
CMD_MOTOR_STOP = 0x62  # 停止电机 [motor_mask]
CMD_MOTOR_EMERGENCY = 0x63  # 急停 []
CMD_MOTOR_DIRECTION = 0x64  # 方向控制 [motor_id][direction(0/1)]

# I2C设备命令 (0x70-0x7F) - V3.0简化版本
CMD_I2C_WRITE = 0x70  # I2C写入 [dev_addr(7bit)][byte_count][data...]
CMD_I2C_READ = 0x71  # I2C读取 [dev_addr(7bit)][byte_count]
CMD_I2C_SCAN = 0x72  # I2C总线扫描 []
CMD_OLED_INIT = 0x73  # OLED初始化
CMD_OLED_CLEAR = 0x74  # OLED清屏
CMD_OLED_ALLON = 0x75  # OLED全亮
CMD_OLED_TEXT = 0x76  # OLED显示文本

# SPI设备命令 (0x80-0x8F)
CMD_SPI_FLASH_ID = 0x80  # 读取Flash ID []
CMD_SPI_FLASH_READ = 0x81  # 读取数据 [addr(24)][len]
CMD_SPI_FLASH_WRITE = 0x82  # 写入数据 [addr(24)][data...]
CMD_SPI_FLASH_ERASE = 0x83  # 擦除扇区 [addr(24)]

# UART设备命令 (0x90-0x9F)
CMD_UART_CONFIG = 0x90  # 配置蓝牙波特率 [baud_rate(4字节)]
CMD_UART_SEND = 0x91  # 发送数据 [data...]
CMD_UART_AT = 0x92  # 发送AT命令 [cmd_str...] (预留，未实现)

# CAN设备命令 (0xC0-0xCF) - 已移至core/serial_protocol.py统一管理
# 注意：旧命令码0xA0-0xAF已废弃，现在使用0xC0-0xCF避免冲突
# CMD_CAN_CONFIG = 0xC0  # 配置波特率
# CMD_CAN_SEND = 0xC1     # 发送CAN帧
# CMD_CAN_FILTER = 0xC2   # 设置过滤器
# CMD_CAN_STATUS = 0xC3   # 读取状态
# CMD_CAN_RX_DATA = 0xC4  # CAN接收数据上报

# 1-Wire设备命令 (0xB0-0xBF)
CMD_ONEWIRE_RESET = 0xB0  # 总线复位 []
CMD_ONEWIRE_READ_TEMP = 0xB1  # 读取温度 []
CMD_ONEWIRE_SEARCH = 0xB2  # 搜索设备 []


# ============================================================================
# 电机控制子模块
# ============================================================================


class MotorControlPanel(QWidget):
    """4路电机控制面板（复用PWM0/2/4/6）"""

    def __init__(self, serial_manager, pwm_controller=None):
        super().__init__()
        self.serial_manager = serial_manager
        self.pwm_controller = pwm_controller  # PWM控制器引用，用于同步状态
        self.motors = []  # 存储4个电机的控件引用
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # 4路电机控制组（紧凑布局）
        motors_group = QGroupBox("电机控制 (复用PWM0/2/4/6)")
        motors_layout = QVBoxLayout()
        motors_layout.setSpacing(3)
        motors_layout.setContentsMargins(4, 4, 4, 4)

        # 创建4个电机控制
        pwm_channels = [0, 2, 4, 6]
        for i, pwm_ch in enumerate(pwm_channels):
            motor_widget = self.create_motor_widget(i, pwm_ch)
            motors_layout.addWidget(motor_widget)

        motors_group.setLayout(motors_layout)
        main_layout.addWidget(motors_group)

        # 底部控制按钮（参考PWM控制器的布局）
        control_layout = QHBoxLayout()

        self.emergency_btn = QPushButton("🛑 急停")
        self.emergency_btn.setMaximumWidth(80)
        self.emergency_btn.setStyleSheet(
            "background-color: #f44336; color: white; font-weight: bold;"
        )
        self.emergency_btn.clicked.connect(self.emergency_stop_all)
        control_layout.addWidget(self.emergency_btn)

        # 全部使能按钮
        self.enable_all_btn = QPushButton("全部使能")
        self.enable_all_btn.setMaximumWidth(100)
        self.enable_all_btn.clicked.connect(self.enable_all_motors)
        control_layout.addWidget(self.enable_all_btn)

        # 全部禁用按钮
        self.disable_all_btn = QPushButton("全部禁用")
        self.disable_all_btn.setMaximumWidth(100)
        self.disable_all_btn.clicked.connect(self.disable_all_motors)
        control_layout.addWidget(self.disable_all_btn)

        control_layout.addStretch()

        # 应用配置并启动按钮
        self.apply_btn = QPushButton("应用配置并启动")
        self.apply_btn.setMaximumWidth(120)
        self.apply_btn.clicked.connect(self.start_all_motors)
        control_layout.addWidget(self.apply_btn)

        self.stop_all_btn = QPushButton("停止输出")
        self.stop_all_btn.setMaximumWidth(90)
        self.stop_all_btn.clicked.connect(self.stop_all_motors)
        control_layout.addWidget(self.stop_all_btn)

        main_layout.addLayout(control_layout)

        # 日志区（紧凑）
        log_group = QGroupBox("输出日志")
        log_group.setMaximumHeight(100)
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(4, 2, 4, 2)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(75)
        self.log_text.setFont(QFont("Consolas", 8))
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def create_motor_widget(self, motor_id, pwm_channel):
        """创建单个电机控制组件（横向整齐对齐）"""
        from PySide6.QtWidgets import QHBoxLayout

        group = QGroupBox(f"电机 {motor_id + 1} (PWM{pwm_channel})")
        group.setMaximumHeight(70)
        layout = QHBoxLayout()
        layout.setSpacing(5)  # 控件间距
        layout.setContentsMargins(8, 8, 8, 8)  # 统一边距

        # 使能复选框
        enable_check = QCheckBox("使能")
        enable_check.setChecked(False)
        enable_check.setFixedWidth(50)

        # 注释掉自动同步到PWM的逻辑，让电机控制和PWM控制独立
        # 这样可以单独测试8路PWM，不会互相干扰
        # enable_check.stateChanged.connect(
        #     lambda state, ch=pwm_channel: self.sync_motor_to_pwm(
        #         ch, state == Qt.Checked
        #     )
        # )

        layout.addWidget(enable_check)

        # 频率设置区域
        freq_label = QLabel("频率:")
        freq_label.setFixedWidth(38)
        layout.addWidget(freq_label)

        freq_spin = QDoubleSpinBox()
        freq_spin.setRange(1, 100000)
        freq_spin.setValue(10000)
        freq_spin.setSuffix(" Hz")
        freq_spin.setFixedWidth(105)
        layout.addWidget(freq_spin)

        # 频率快捷按钮
        freq_1k_btn = QPushButton("1k")
        freq_1k_btn.setFixedWidth(45)
        freq_1k_btn.clicked.connect(lambda: freq_spin.setValue(1000))
        layout.addWidget(freq_1k_btn)

        freq_10k_btn = QPushButton("10k")
        freq_10k_btn.setFixedWidth(45)
        freq_10k_btn.clicked.connect(lambda: freq_spin.setValue(10000))
        layout.addWidget(freq_10k_btn)

        freq_50k_btn = QPushButton("50k")
        freq_50k_btn.setFixedWidth(45)
        freq_50k_btn.clicked.connect(lambda: freq_spin.setValue(50000))
        layout.addWidget(freq_50k_btn)

        layout.addSpacing(12)  # 频率和速度之间适当间距

        # 速度设置区域
        speed_label = QLabel("速度:")
        speed_label.setFixedWidth(38)
        layout.addWidget(speed_label)

        speed_slider = QSlider(Qt.Horizontal)
        speed_slider.setRange(0, 10000)
        speed_slider.setValue(5000)
        speed_slider.setFixedWidth(220)
        layout.addWidget(speed_slider)

        speed_spin = QDoubleSpinBox()
        speed_spin.setRange(0, 100)
        speed_spin.setValue(50.00)
        speed_spin.setSuffix(" %")
        speed_spin.setDecimals(2)
        speed_spin.setSingleStep(0.01)
        speed_spin.setFixedWidth(85)
        layout.addWidget(speed_spin)

        # 速度快捷按钮 - 加宽到58px确保100%完整显示
        speed_25_btn = QPushButton("25%")
        speed_25_btn.setFixedWidth(58)
        speed_25_btn.clicked.connect(lambda: speed_slider.setValue(2500))
        layout.addWidget(speed_25_btn)

        speed_50_btn = QPushButton("50%")
        speed_50_btn.setFixedWidth(58)
        speed_50_btn.clicked.connect(lambda: speed_slider.setValue(5000))
        layout.addWidget(speed_50_btn)

        speed_75_btn = QPushButton("75%")
        speed_75_btn.setFixedWidth(58)
        speed_75_btn.clicked.connect(lambda: speed_slider.setValue(7500))
        layout.addWidget(speed_75_btn)

        speed_100_btn = QPushButton("100%")
        speed_100_btn.setFixedWidth(58)
        speed_100_btn.clicked.connect(lambda: speed_slider.setValue(10000))
        layout.addWidget(speed_100_btn)

        # 不添加右侧弹簧，紧凑布局
        # layout.addStretch()  # 移除这个，避免右侧留白过多

        # 连接信号
        speed_slider.valueChanged.connect(lambda v: speed_spin.setValue(v / 100.0))
        speed_spin.valueChanged.connect(lambda v: speed_slider.setValue(int(v * 100)))

        group.setLayout(layout)  # 保存控件引用
        motor_data = {
            "group": group,
            "enable": enable_check,
            "freq": freq_spin,
            "speed": speed_spin,
            "pwm_channel": pwm_channel,
        }
        self.motors.append(motor_data)

        return group

    def start_all_motors(self):
        """启动所有电机"""
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_text.append("❌ 错误: CDC串口未连接")
            QMessageBox.warning(self, "串口未连接", "请先连接CDC串口后再配置电机")
            return

        self.log_text.append("=" * 50)
        self.log_text.append("开始配置电机...")

        import time

        for i in range(4):
            motor = self.motors[i]
            if motor["enable"].isChecked():
                freq = motor["freq"].value()
                speed = motor["speed"].value()

                # 发送配置命令
                if self.send_motor_config(i, int(freq), speed):
                    self.log_text.append(f"✓ 电机{i + 1}: {freq:.0f}Hz, {speed:.2f}%")
                    time.sleep(0.01)
                else:
                    self.log_text.append(f"✗ 电机{i + 1}: 配置失败")

        # 启动使能的电机
        enable_mask = 0
        for i in range(4):
            if self.motors[i]["enable"].isChecked():
                enable_mask |= 1 << i

        if enable_mask > 0:
            self.send_motor_start(enable_mask)
            self.log_text.append(f"✅ 配置完成，已启动电机 (掩码: 0x{enable_mask:02X})")
        else:
            self.log_text.append("⚠️ 没有使能的电机")

    def stop_all_motors(self, show_log=True):
        """停止所有电机"""
        if self.serial_manager is None or not self.serial_manager.is_connected():
            self.log_text.append("❌ 错误: CDC串口未连接")
            QMessageBox.warning(self, "串口未连接", "请先连接CDC串口")
            return

        if show_log:
            self.log_text.append("⏹ 停止所有电机")
        self.send_motor_stop(0x0F)

    def emergency_stop_all(self):
        """紧急停止所有电机 - 与停止输出功能相同"""
        self.log_text.append("🛑 急停所有电机!")
        # 调用停止输出功能（不显示重复日志）
        self.stop_all_motors(show_log=False)

    def enable_all_motors(self):
        """全部使能快捷按钮"""
        for motor in self.motors:
            motor["enable"].setChecked(True)
        self.log_text.append("✅ 已全部使能所有电机")

    def disable_all_motors(self):
        """全部禁用快捷按钮"""
        for motor in self.motors:
            motor["enable"].setChecked(False)
        self.log_text.append("⏹ 已禁用所有电机")

    def sync_motor_to_pwm(self, pwm_channel, enabled):
        """同步电机使能状态到PWM通道"""
        if self.pwm_controller is None:
            return

        # PWM通道是0,2,4,6，对应电机控制器的pwm_channels列表索引
        try:
            pwm_ch_data = self.pwm_controller.pwm_channels[pwm_channel]

            # 暂时阻止信号，避免循环触发
            pwm_ch_data["enable"].blockSignals(True)
            pwm_ch_data["enable"].setChecked(enabled)
            pwm_ch_data["enable"].blockSignals(False)
        except (IndexError, AttributeError):
            pass

    def send_motor_config(self, motor_id, freq_hz, speed_pct):
        """发送电机配置命令（频率+速度）"""
        """电机复用PWM通道：motor_id 0->PWM0, 1->PWM2, 2->PWM4, 3->PWM6"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            return False

        # 映射电机ID到PWM通道
        pwm_channel_map = {0: 0, 1: 2, 2: 4, 3: 6}
        pwm_channel = pwm_channel_map[motor_id]

        # 计算频率字和占空比字（参考PWM协议）
        freq_word = int((freq_hz * 4294967296) / 50000000)
        duty_word = int(speed_pct * 655.35)  # 0-100% -> 0-65535

        # 使用PWM配置命令（0x50），而不是电机命令（0x60）
        payload = struct.pack(">BIH", pwm_channel, freq_word, duty_word)

        # 调试输出
        hex_str = " ".join(f"{b:02X}" for b in payload)
        self.log_text.append(
            f"  📤 电机{motor_id+1}(PWM{pwm_channel}) Payload: {hex_str}"
        )

        # 导入PWM命令码
        from .pwm_controller_tab import CMD_PWM_CONFIG

        return self.serial_manager.send_command(CMD_PWM_CONFIG, payload)

    def send_motor_start(self, motor_mask):
        """发送启动命令"""
        """将电机掩码转换为PWM通道掩码"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            return False

        # 将电机掩码（bit0-3）转换为PWM掩码（bit0,2,4,6）
        # 电机0->PWM0, 电机1->PWM2, 电机2->PWM4, 电机3->PWM6
        pwm_mask = 0
        if motor_mask & 0x01:  # 电机0
            pwm_mask |= 1 << 0  # PWM0
        if motor_mask & 0x02:  # 电机1
            pwm_mask |= 1 << 2  # PWM2
        if motor_mask & 0x04:  # 电机2
            pwm_mask |= 1 << 4  # PWM4
        if motor_mask & 0x08:  # 电机3
            pwm_mask |= 1 << 6  # PWM6

        self.log_text.append(
            f"  电机掩码: 0x{motor_mask:02X} -> PWM掩码: 0x{pwm_mask:02X}"
        )

        payload = struct.pack("B", pwm_mask)

        # 使用PWM使能命令（0x51）
        from .pwm_controller_tab import CMD_PWM_ENABLE

        return self.serial_manager.send_command(CMD_PWM_ENABLE, payload)

    def send_motor_stop(self, motor_mask):
        """发送停止命令"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            return False

        # 使用PWM停止命令（0x52）
        from .pwm_controller_tab import CMD_PWM_STOP

        return self.serial_manager.send_command(CMD_PWM_STOP, b"")


# ============================================================================
# I2C设备控制子模块（OLED显示）
# ============================================================================


class I2CDevicePanel(QWidget):
    """I2C设备控制面板（参考UART设备面板设计）

    引脚定义（参考图片）:
      - L18: IIC_SDA
      - M20: IIC_SCLK

    支持设备:
      - OLED SSD1306 (I2C 0x3C)
      - 通用I2C设备
    """

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.show_timestamp = True  # 显示时间戳
        self.hex_mode = True  # HEX显示模式

        # 频闪模式变量
        self.oled_blink_state = False
        self.oled_blink_timer = None

        self.init_ui()

        # 连接串口接收信号（用于接收应答）
        if self.serial_manager:
            # 注意：这里假设serial_manager有data_received信号
            # 如果没有，需要通过DeviceCenterTab传递
            pass

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ========== I2C设备配置组 ==========
        device_group = QGroupBox("I2C设备配置")
        device_layout = QVBoxLayout()
        device_layout.setSpacing(10)

        # 第一行：设备类型选择
        config_layout = QGridLayout()
        config_layout.setColumnStretch(1, 2)
        config_layout.setColumnStretch(3, 1)
        config_layout.setHorizontalSpacing(10)
        config_layout.setVerticalSpacing(8)

        # 设备类型
        device_type_label = QLabel("设备类型:")
        device_type_label.setMinimumWidth(70)
        config_layout.addWidget(device_type_label, 0, 0)

        self.device_type_combo = QComboBox()
        self.device_type_combo.addItems(["OLED显示屏 (SSD1306)", "通用I2C设备"])
        self.device_type_combo.currentIndexChanged.connect(self.on_device_type_changed)
        config_layout.addWidget(self.device_type_combo, 0, 1)

        # I2C地址
        addr_label = QLabel("I2C地址:")
        addr_label.setMinimumWidth(70)
        config_layout.addWidget(addr_label, 0, 2)

        self.i2c_addr_input = QLineEdit()
        self.i2c_addr_input.setText("0x3C")
        self.i2c_addr_input.setMaximumWidth(80)
        config_layout.addWidget(self.i2c_addr_input, 0, 3)

        self.i2c_scan_btn = QPushButton("扫描I2C总线")
        self.i2c_scan_btn.setMinimumWidth(100)
        self.i2c_scan_btn.clicked.connect(self.i2c_scan)
        config_layout.addWidget(self.i2c_scan_btn, 0, 4)

        device_layout.addLayout(config_layout)

        # ========== OLED控制区（可切换显示） ==========
        self.oled_widget = QWidget()
        oled_layout = QGridLayout(self.oled_widget)
        oled_layout.setContentsMargins(0, 0, 0, 0)
        oled_layout.setColumnStretch(1, 1)
        oled_layout.setHorizontalSpacing(10)

        # 第一行：OLED控制按钮（初始化 + 快捷功能）
        oled_init_label = QLabel("OLED控制:")
        oled_init_label.setMinimumWidth(70)
        oled_layout.addWidget(oled_init_label, 0, 0)

        oled_btn_layout = QHBoxLayout()
        oled_btn_layout.setSpacing(5)

        self.oled_init_btn = QPushButton("初始化")
        self.oled_init_btn.setToolTip("执行OLED初始化序列（必须先执行）")
        self.oled_init_btn.clicked.connect(self.oled_init)
        oled_btn_layout.addWidget(self.oled_init_btn)

        self.oled_clear_btn = QPushButton("清屏")
        self.oled_clear_btn.setToolTip("清除OLED屏幕所有内容")
        self.oled_clear_btn.clicked.connect(self.oled_clear)
        oled_btn_layout.addWidget(self.oled_clear_btn)

        # 快捷功能按钮
        self.oled_quick_hello_btn = QPushButton("显示Hello+FPGA")
        self.oled_quick_hello_btn.setToolTip("第1行显示'Hello'，第2行显示'FPGA'")
        self.oled_quick_hello_btn.clicked.connect(self.oled_quick_hello)
        oled_btn_layout.addWidget(self.oled_quick_hello_btn)

        self.oled_blink_btn = QPushButton("频闪")
        self.oled_blink_btn.setToolTip("开/关显示（频闪效果）")
        self.oled_blink_btn.setCheckable(True)
        self.oled_blink_btn.clicked.connect(self.oled_blink_toggle)
        oled_btn_layout.addWidget(self.oled_blink_btn)

        oled_btn_layout.addStretch()

        oled_layout.addLayout(oled_btn_layout, 0, 1, 1, 4)

        # 第二行：行号和列号
        oled_layout.addWidget(QLabel("行号:"), 1, 0)
        self.oled_line_combo = QComboBox()
        self.oled_line_combo.addItems(["第1行", "第2行", "第3行", "第4行"])
        self.oled_line_combo.setMaximumWidth(100)
        oled_layout.addWidget(self.oled_line_combo, 1, 1)

        oled_layout.addWidget(QLabel("列号:"), 1, 2)
        self.oled_column_spin = QSpinBox()
        self.oled_column_spin.setRange(0, 15)
        self.oled_column_spin.setValue(0)
        self.oled_column_spin.setMaximumWidth(80)
        oled_layout.addWidget(self.oled_column_spin, 1, 3)

        # 第三行：文本输入
        oled_layout.addWidget(QLabel("文本:"), 2, 0)
        self.oled_text_input = QLineEdit()
        self.oled_text_input.setPlaceholderText("输入要显示的文本（支持ASCII）...")
        self.oled_text_input.returnPressed.connect(self.oled_send_text)
        oled_layout.addWidget(self.oled_text_input, 2, 1, 1, 3)

        self.oled_send_btn = QPushButton("发送")
        self.oled_send_btn.setMinimumWidth(80)
        self.oled_send_btn.clicked.connect(self.oled_send_text)
        oled_layout.addWidget(self.oled_send_btn, 2, 4)

        device_layout.addWidget(self.oled_widget)

        # ========== 通用I2C操作区（可切换显示） ==========
        self.i2c_widget = QWidget()
        i2c_layout = QGridLayout(self.i2c_widget)
        i2c_layout.setContentsMargins(0, 0, 0, 0)
        i2c_layout.setColumnStretch(2, 1)
        i2c_layout.setHorizontalSpacing(10)

        # 读操作
        i2c_layout.addWidget(QLabel("寄存器地址:"), 0, 0)
        self.i2c_reg_addr_input = QLineEdit()
        self.i2c_reg_addr_input.setPlaceholderText("0x00")
        self.i2c_reg_addr_input.setMaximumWidth(80)
        i2c_layout.addWidget(self.i2c_reg_addr_input, 0, 1)

        i2c_layout.addWidget(QLabel("读取长度:"), 0, 2)
        self.i2c_read_len_spin = QSpinBox()
        self.i2c_read_len_spin.setRange(1, 255)
        self.i2c_read_len_spin.setValue(1)
        self.i2c_read_len_spin.setMaximumWidth(80)
        i2c_layout.addWidget(self.i2c_read_len_spin, 0, 3)

        self.i2c_read_btn = QPushButton("读取")
        self.i2c_read_btn.setMinimumWidth(80)
        self.i2c_read_btn.clicked.connect(self.i2c_read)
        i2c_layout.addWidget(self.i2c_read_btn, 0, 4)

        # 写操作
        i2c_layout.addWidget(QLabel("写入数据(HEX):"), 1, 0)
        self.i2c_write_data_input = QLineEdit()
        self.i2c_write_data_input.setPlaceholderText("例: 00 01 02 FF")
        i2c_layout.addWidget(self.i2c_write_data_input, 1, 1, 1, 3)

        self.i2c_write_btn = QPushButton("写入")
        self.i2c_write_btn.setMinimumWidth(80)
        self.i2c_write_btn.clicked.connect(self.i2c_write)
        i2c_layout.addWidget(self.i2c_write_btn, 1, 4)

        device_layout.addWidget(self.i2c_widget)

        device_group.setLayout(device_layout)
        main_layout.addWidget(device_group)

        # ========== 显示选项 ==========
        display_group = QGroupBox("显示选项")
        display_layout = QHBoxLayout()
        display_layout.setSpacing(15)

        self.hex_display_checkbox = QCheckBox("HEX显示")
        self.hex_display_checkbox.setChecked(self.hex_mode)
        self.hex_display_checkbox.stateChanged.connect(self.toggle_hex_display)
        display_layout.addWidget(self.hex_display_checkbox)

        self.timestamp_checkbox = QCheckBox("时间戳")
        self.timestamp_checkbox.setChecked(self.show_timestamp)
        self.timestamp_checkbox.stateChanged.connect(self.toggle_timestamp)
        display_layout.addWidget(self.timestamp_checkbox)

        self.clear_log_btn = QPushButton("清除日志")
        self.clear_log_btn.clicked.connect(self.clear_log)
        display_layout.addWidget(self.clear_log_btn)

        display_layout.addStretch()

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group)

        # ========== 交互日志 ==========
        log_group = QGroupBox("💬 I2C通信数据")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setPlaceholderText(
            "I2C通信数据区域\n\n"
            "• 命令发送和应答信息会显示在这里\n"
            "• 发送的数据和接收的应答都会显示在这里\n"
            "• 可通过设备类型下拉框切换OLED和通用I2C模式\n"
        )
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # 初始化设备类型（默认显示OLED控制）
        self.on_device_type_changed(0)

    # ========== 设备类型切换方法 ==========

    def on_device_type_changed(self, index):
        """设备类型切换"""
        device_type = self.device_type_combo.currentText()

        # 根据设备类型显示/隐藏对应控件
        if "OLED" in device_type:
            self.oled_widget.setVisible(True)
            self.i2c_widget.setVisible(False)
            self.i2c_addr_input.setText("0x3C")
            self.log_text.append(f"[切换] 设备类型: {device_type}")
        else:
            self.oled_widget.setVisible(False)
            self.i2c_widget.setVisible(True)
            self.log_text.append(f"[切换] 设备类型: {device_type}")

    # ========== OLED控制方法 ==========

    def oled_init(self):
        """初始化OLED - 执行完整的初始化序列"""
        self.append_log("📤 CDC → FPGA: OLED初始化命令 (0x73)", "SEND")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_OLED_INIT, b"")
            self.append_log("⏳ 等待CH340应答（初始化完成）...", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def oled_clear(self):
        """清空OLED屏幕"""
        self.append_log("📤 CDC → FPGA: OLED清屏命令 (0x74)", "SEND")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_OLED_CLEAR, b"")
            self.append_log("⏳ 等待CH340应答（清屏完成）...", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def oled_send_text(self):
        """发送文本到OLED - 支持指定行列"""
        text = self.oled_text_input.text()
        if not text:
            QMessageBox.warning(self, "输入错误", "请输入要显示的文本")
            return

        # 行号：0-3（对应OLED的4行，每行2页）
        line = self.oled_line_combo.currentIndex()
        # 列号：0-15（每行16个字符）
        column = self.oled_column_spin.value()

        self.append_log(
            f'📤 CDC → FPGA: OLED显示文本 (0x75) - 行{line+1}列{column+1}: "{text}"',
            "SEND",
        )

        if self.serial_manager and self.serial_manager.is_connected():
            # Payload: [line][column][text_bytes...]
            payload = struct.pack("BB", line, column) + text.encode(
                "ascii", errors="ignore"
            )
            self.serial_manager.send_command(CMD_OLED_TEXT, payload)
            self.append_log("⏳ 等待CH340应答（显示完成）...", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def oled_quick_hello(self):
        """快捷功能：显示Hello和FPGA"""
        self.append_log("📝 快捷显示: 第1行'Hello'，第2行'FPGA'", "SEND")

        if self.serial_manager and self.serial_manager.is_connected():
            # 第1行显示"Hello"
            payload1 = struct.pack("BB", 0, 0) + b"Hello"
            self.serial_manager.send_command(CMD_OLED_TEXT, payload1)

            # 延迟一下，然后第2行显示"FPGA"
            import time

            time.sleep(0.05)

            payload2 = struct.pack("BB", 1, 0) + b"FPGA"
            self.serial_manager.send_command(CMD_OLED_TEXT, payload2)

            self.append_log("⏳ 等待CH340应答...", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def oled_blink_toggle(self):
        """频闪功能：切换显示开关"""
        if self.oled_blink_btn.isChecked():
            # 开启频闪模式
            self.append_log("💡 启动频闪模式...", "SEND")
            self.oled_blink_timer = QTimer()
            self.oled_blink_timer.timeout.connect(self.oled_blink_step)
            self.oled_blink_state = True
            self.oled_blink_timer.start(500)  # 500ms切换一次
            self.oled_blink_btn.setText("💡 停止频闪")
        else:
            # 停止频闪，恢复显示
            self.append_log("💡 停止频闪模式", "INFO")
            if hasattr(self, "oled_blink_timer"):
                self.oled_blink_timer.stop()
            # 确保显示开启
            self.oled_display_control(True)
            self.oled_blink_btn.setText("💡 频闪")

    def oled_blink_step(self):
        """频闪步进：切换显示状态"""
        self.oled_blink_state = not self.oled_blink_state
        self.oled_display_control(self.oled_blink_state)

    def oled_display_control(self, enable):
        """控制OLED显示开关
        Args:
            enable: True=开启显示，False=关闭显示
        """
        if self.serial_manager and self.serial_manager.is_connected():
            # 使用通用I2C写操作发送显示控制命令
            # 0xAF = 开启显示, 0xAE = 关闭显示
            dev_addr = 0x3C
            reg_addr = 0x00  # 命令寄存器
            cmd_byte = 0xAF if enable else 0xAE

            payload = struct.pack("BB", dev_addr, reg_addr) + bytes([cmd_byte])
            self.serial_manager.send_command(CMD_I2C_WRITE, payload)

    # ========== 通用I2C操作方法 ==========

    def i2c_scan(self):
        """扫描I2C总线 - 查找所有可用设备"""
        self.append_log("📤 CDC → FPGA: I2C总线扫描命令 (0x70)", "SEND")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_I2C_SCAN, b"")
            self.append_log("⏳ 等待CH340应答（设备地址列表）...", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def i2c_read(self):
        """通用I2C读操作"""
        try:
            dev_addr = int(self.i2c_addr_input.text(), 16)
            reg_addr = int(self.i2c_reg_addr_input.text(), 16)
            read_len = self.i2c_read_len_spin.value()
        except ValueError:
            QMessageBox.warning(self, "地址错误", "请输入有效的十六进制地址")
            return

        self.append_log(
            f"📤 CDC → FPGA: I2C读取 (0x71) - 设备:0x{dev_addr:02X} 寄存器:0x{reg_addr:02X} 长度:{read_len}",
            "SEND",
        )

        if self.serial_manager and self.serial_manager.is_connected():
            payload = struct.pack("BBB", dev_addr, reg_addr, read_len)
            self.serial_manager.send_command(CMD_I2C_READ, payload)
            self.append_log("⏳ 等待CH340应答（读取数据）...", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    def i2c_write(self):
        """通用I2C写操作"""
        try:
            dev_addr = int(self.i2c_addr_input.text(), 16)
            reg_addr = int(self.i2c_reg_addr_input.text(), 16)
            data_str = self.i2c_write_data_input.text()
            write_data = bytes.fromhex(data_str.replace(" ", ""))
        except ValueError:
            QMessageBox.warning(self, "输入错误", "请输入有效的地址和HEX数据")
            return

        self.append_log(
            f"📤 CDC → FPGA: I2C写入 (0x72) - 设备:0x{dev_addr:02X} 寄存器:0x{reg_addr:02X} 数据:{write_data.hex().upper()}",
            "SEND",
        )

        if self.serial_manager and self.serial_manager.is_connected():
            payload = struct.pack("BB", dev_addr, reg_addr) + write_data
            self.serial_manager.send_command(CMD_I2C_WRITE, payload)
            self.append_log("⏳ 等待CH340应答（写入完成）...", "INFO")
        else:
            self.append_log("❌ CDC串口未连接", "ERROR")

    # ========== 辅助方法 ==========

    def toggle_hex_display(self, state):
        """切换HEX显示模式"""
        self.hex_mode = state == Qt.CheckState.Checked.value

    def toggle_timestamp(self, state):
        """切换时间戳显示"""
        self.show_timestamp = state == Qt.CheckState.Checked.value

    def clear_log(self):
        """清除日志"""
        self.log_text.clear()

    def append_log(self, message, msg_type="INFO"):
        """追加日志到日志区
        Args:
            message: 日志消息
            msg_type: 消息类型 ("SEND", "RECV", "INFO", "ERROR")
        """
        from datetime import datetime

        timestamp = ""
        if self.show_timestamp:
            timestamp = f"[{datetime.now().strftime('%H:%M:%S')}] "

        # 根据消息类型设置颜色
        color_map = {
            "SEND": "#2196F3",  # 蓝色
            "RECV": "#4CAF50",  # 绿色
            "INFO": "#9E9E9E",  # 灰色
            "ERROR": "#F44336",  # 红色
        }
        color = color_map.get(msg_type, "#000000")

        # 使用HTML格式化
        formatted = f'<span style="color:{color}">{timestamp}{message}</span>'
        self.log_text.append(formatted)

    def handle_rx_response(self, data):
        """处理CH340接收到的应答数据（从DeviceCenterTab或主窗口调用）

        应答帧格式: AA 55 MOD_ID FUNC_ID STATUS RESERVED CS
        """
        if len(data) < 7:
            self.append_log(f"📥 应答帧长度错误: {len(data)}字节", "ERROR")
            return

        # 解析应答帧
        if data[0] == 0xAA and data[1] == 0x55:
            mod_id = data[2]
            func_id = data[3]
            status = data[4]

            status_msg = {
                0x00: "✓ 成功",
                0x01: "✗ 校验错误",
                0x02: "✗ 无效命令",
                0x03: "✗ 参数错误",
            }.get(status, f"✗ 未知状态(0x{status:02X})")

            self.append_log(
                f"📥 CH340 ← FPGA: 模块ID=0x{mod_id:02X} 功能ID=0x{func_id:02X} 状态={status_msg}",
                "RECV" if status == 0x00 else "ERROR",
            )

            # 如果是扫描命令，可能有额外数据
            if func_id == CMD_I2C_SCAN and len(data) > 7:
                devices = data[7:-1]  # 排除校验和
                if devices:
                    dev_list = ", ".join(f"0x{addr:02X}" for addr in devices)
                    self.append_log(f"  🔍 发现设备: {dev_list}", "INFO")
                else:
                    self.append_log("  🔍 未发现任何I2C设备", "INFO")
        else:
            # 可能是数据帧（不是应答帧）
            if self.hex_mode:
                hex_str = " ".join(f"{b:02X}" for b in data)
                self.append_log(f"📥 CH340 ← FPGA: {hex_str}", "RECV")
            else:
                try:
                    text = data.decode("utf-8", errors="replace")
                    self.append_log(f"📥 CH340 ← FPGA: {text}", "RECV")
                except:
                    self.append_log(f"📥 CH340 ← FPGA: {data.hex().upper()}", "RECV")


# ============================================================================
# SPI设备控制子模块（W25Q128 Flash）
# ============================================================================


class SPIDevicePanel(QWidget):
    """SPI设备控制面板（W25Q128 Flash）"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Flash控制组（简化）
        flash_group = QGroupBox("W25Q128 Flash (16MB)")
        flash_layout = QVBoxLayout()
        flash_layout.setSpacing(6)

        # 第一行：读取ID和按钮
        row1 = QHBoxLayout()
        self.flash_id_btn = QPushButton("读取ID")
        self.flash_id_btn.clicked.connect(self.flash_read_id)
        row1.addWidget(self.flash_id_btn)

        self.flash_id_label = QLabel("ID: --")
        self.flash_id_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        row1.addWidget(self.flash_id_label)

        row1.addStretch()

        self.flash_read_btn = QPushButton("读取")
        self.flash_read_btn.clicked.connect(self.flash_read_data)
        row1.addWidget(self.flash_read_btn)

        self.flash_write_btn = QPushButton("写入")
        self.flash_write_btn.clicked.connect(self.flash_write_data)
        row1.addWidget(self.flash_write_btn)

        self.flash_erase_btn = QPushButton("擦除")
        self.flash_erase_btn.setStyleSheet("background-color: #ff9800;")
        self.flash_erase_btn.clicked.connect(self.flash_erase_sector)
        row1.addWidget(self.flash_erase_btn)

        flash_layout.addLayout(row1)

        # 第二行：地址和长度
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("地址:"))
        self.flash_addr_input = QLineEdit()
        self.flash_addr_input.setPlaceholderText("0x000000")
        self.flash_addr_input.setMaximumWidth(100)
        row2.addWidget(self.flash_addr_input)

        row2.addWidget(QLabel("长度:"))
        self.flash_len_spin = QSpinBox()
        self.flash_len_spin.setRange(1, 256)
        self.flash_len_spin.setValue(16)
        self.flash_len_spin.setSuffix(" B")
        self.flash_len_spin.setMaximumWidth(80)
        row2.addWidget(self.flash_len_spin)

        row2.addWidget(QLabel("数据(HEX):"))
        self.flash_data_input = QLineEdit()
        self.flash_data_input.setPlaceholderText("48656C6C6F")
        row2.addWidget(self.flash_data_input)

        flash_layout.addLayout(row2)

        flash_group.setLayout(flash_layout)
        main_layout.addWidget(flash_group)

        # 交互日志（简化）
        log_group = QGroupBox("SPI交互日志")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def flash_read_id(self):
        """读取Flash芯片ID"""
        self.log_text.append("📤 CDC → FPGA: 读取Flash ID (0x80)")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_SPI_FLASH_ID, b"")
            self.log_text.append("⏳ 等待CH340应答（芯片ID）...")
        else:
            self.log_text.append("❌ CDC串口未连接")

    def flash_read_data(self):
        """读取Flash数据"""
        addr_str = self.flash_addr_input.text()
        length = self.flash_len_spin.value()

        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
        except ValueError:
            QMessageBox.warning(self, "地址错误", "请输入有效的十六进制地址")
            return

        self.log_text.append(
            f"📤 CDC → FPGA: 读取Flash (0x81) - 地址: 0x{addr:06X}, 长度: {length}"
        )

        if self.serial_manager and self.serial_manager.is_connected():
            payload = struct.pack(">IB", addr, length)
            self.serial_manager.send_command(CMD_SPI_FLASH_READ, payload)
            self.log_text.append("⏳ 等待CH340应答（读取数据）...")
        else:
            self.log_text.append("❌ CDC串口未连接")

    def flash_write_data(self):
        """写入Flash数据"""
        addr_str = self.flash_addr_input.text()
        data_str = self.flash_data_input.text()

        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
            data = bytes.fromhex(data_str.replace(" ", ""))
        except ValueError:
            QMessageBox.warning(self, "输入错误", "请输入有效的地址和十六进制数据")
            return

        self.log_text.append(
            f"📤 CDC → FPGA: 写入Flash (0x82) - 地址: 0x{addr:06X}, 数据: {data.hex().upper()}"
        )

        if self.serial_manager and self.serial_manager.is_connected():
            payload = struct.pack(">I", addr) + data
            self.serial_manager.send_command(CMD_SPI_FLASH_WRITE, payload)
            self.log_text.append("⏳ 等待CH340应答...")
        else:
            self.log_text.append("❌ CDC串口未连接")

    def flash_erase_sector(self):
        """擦除Flash扇区"""
        addr_str = self.flash_addr_input.text()

        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
        except ValueError:
            QMessageBox.warning(self, "地址错误", "请输入有效的十六进制地址")
            return

        reply = QMessageBox.question(
            self,
            "确认擦除",
            f"确定要擦除地址 0x{addr:06X} 的扇区吗？\n此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.log_text.append(
                f"📤 CDC → FPGA: 擦除Flash扇区 (0x83) - 地址: 0x{addr:06X}"
            )
            if self.serial_manager and self.serial_manager.is_connected():
                payload = struct.pack(">I", addr)
                self.serial_manager.send_command(CMD_SPI_FLASH_ERASE, payload)
                self.log_text.append("⏳ 等待CH340应答...")
            else:
                self.log_text.append("❌ CDC串口未连接")


# ============================================================================
# UART设备控制子模块（蓝牙模块 HC-06）
# 说明: 蓝牙模块波特率可通过CMD_UART_CONFIG命令配置
# ============================================================================


class UARTDevicePanel(QWidget):
    """UART设备控制面板（蓝牙模块 HC-06，波特率可配置）"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.hex_display = False  # HEX显示模式
        self.show_timestamp = True  # 显示时间戳
        self.hex_send = False  # HEX发送模式

        # UTF-8增量解码器，用于处理可能的多字节字符分包问题
        import codecs

        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

        # 数据缓冲区，用于处理应答帧和蓝牙数据混合的情况
        self.data_buffer = bytearray()

        # 蓝牙数据累积缓冲区，用于批量显示减少刷新
        self.bt_data_accumulator = bytearray()

        # SPI数据流跟踪：记录等待的SPI数据长度
        self.waiting_spi_data_length = 0

        # 定时器：用于批量显示累积的蓝牙数据
        self.bt_display_timer = QTimer()
        self.bt_display_timer.timeout.connect(self.flush_bluetooth_data)
        self.bt_display_timer.setSingleShot(True)

        self.init_ui()

        # 连接串口接收信号
        if self.serial_manager:
            self.serial_manager.data_received.connect(self.handle_uart_data)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # 串口设备配置组
        device_group = QGroupBox("串口设备配置")
        device_layout = QVBoxLayout()
        device_layout.setSpacing(10)

        # 第一行：设备类型和波特率（使用网格布局）
        config_layout = QGridLayout()
        config_layout.setColumnStretch(1, 2)  # 设备类型下拉框占2份
        config_layout.setColumnStretch(3, 1)  # 波特率下拉框占1份
        config_layout.setHorizontalSpacing(10)
        config_layout.setVerticalSpacing(8)

        # 设备类型
        device_type_label = QLabel("设备类型:")
        device_type_label.setMinimumWidth(70)
        config_layout.addWidget(device_type_label, 0, 0)

        self.device_type_combo = QComboBox()
        self.device_type_combo.addItems(["蓝牙模块 (HC-06)", "通用串口设备"])
        self.device_type_combo.currentIndexChanged.connect(self.on_device_type_changed)
        config_layout.addWidget(self.device_type_combo, 0, 1)

        # 波特率
        baudrate_label = QLabel("波特率:")
        baudrate_label.setMinimumWidth(60)
        config_layout.addWidget(baudrate_label, 0, 2)

        self.baudrate_combo = QComboBox()
        self.baudrate_combo.addItems(
            ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
        )
        self.baudrate_combo.setCurrentText("115200")
        self.baudrate_combo.currentTextChanged.connect(self.on_baudrate_changed)
        config_layout.addWidget(self.baudrate_combo, 0, 3)

        device_layout.addLayout(config_layout)

        # 第二行：AT命令（仅蓝牙模块显示）
        self.at_widget = QWidget()
        at_layout = QGridLayout(self.at_widget)
        at_layout.setContentsMargins(0, 0, 0, 0)
        at_layout.setColumnStretch(1, 1)
        at_layout.setHorizontalSpacing(10)

        at_label = QLabel("AT命令:")
        at_label.setMinimumWidth(70)
        at_layout.addWidget(at_label, 0, 0)

        self.bt_at_input = QLineEdit()
        self.bt_at_input.setPlaceholderText("输入AT命令（如: AT+NAME=MyBT）...")
        at_layout.addWidget(self.bt_at_input, 0, 1)

        self.bt_at_btn = QPushButton("发送AT")
        self.bt_at_btn.setMinimumWidth(80)
        self.bt_at_btn.clicked.connect(self.bt_send_at)
        at_layout.addWidget(self.bt_at_btn, 0, 2)

        device_layout.addWidget(self.at_widget)

        # 第三行：数据发送
        data_layout = QGridLayout()
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setColumnStretch(1, 1)
        data_layout.setHorizontalSpacing(10)

        data_label = QLabel("数据:")
        data_label.setMinimumWidth(70)
        data_layout.addWidget(data_label, 0, 0)

        self.bt_data_input = QLineEdit()
        self.bt_data_input.setPlaceholderText("输入要发送的数据...")
        self.bt_data_input.returnPressed.connect(self.bt_send_data)  # 回车发送
        data_layout.addWidget(self.bt_data_input, 0, 1)

        self.bt_send_btn = QPushButton("发送")
        self.bt_send_btn.setMinimumWidth(80)
        self.bt_send_btn.clicked.connect(self.bt_send_data)
        data_layout.addWidget(self.bt_send_btn, 0, 2)

        device_layout.addLayout(data_layout)

        device_group.setLayout(device_layout)
        main_layout.addWidget(device_group)

        # 显示选项组
        display_group = QGroupBox("显示选项")
        display_layout = QHBoxLayout()
        display_layout.setSpacing(15)

        # HEX显示复选框
        self.hex_display_checkbox = QCheckBox("16进制显示")
        self.hex_display_checkbox.setChecked(self.hex_display)
        self.hex_display_checkbox.stateChanged.connect(self.toggle_hex_display)
        display_layout.addWidget(self.hex_display_checkbox)

        # 时间戳复选框
        self.timestamp_checkbox = QCheckBox("时间戳")
        self.timestamp_checkbox.setChecked(self.show_timestamp)
        self.timestamp_checkbox.stateChanged.connect(self.toggle_timestamp)
        display_layout.addWidget(self.timestamp_checkbox)

        # HEX发送复选框
        self.hex_send_checkbox = QCheckBox("HEX发送")
        self.hex_send_checkbox.setChecked(self.hex_send)
        self.hex_send_checkbox.stateChanged.connect(self.toggle_hex_send)
        display_layout.addWidget(self.hex_send_checkbox)

        # 清除接收按钮
        self.clear_btn = QPushButton("清除接收")
        self.clear_btn.clicked.connect(self.clear_log)
        display_layout.addWidget(self.clear_btn)

        display_layout.addStretch()

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group)

        # 串口数据显示区
        log_group = QGroupBox("� 串口通信数据")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setPlaceholderText(
            "串口通信数据区域\n\n"
            "• 接收到的串口数据会显示在这里\n"
            "• 发送的数据也会显示在这里\n"
            "• 应答帧等调试信息显示在顶部【调试日志】标签页\n"
            "• 可通过波特率下拉框配置蓝牙模块波特率\n"
        )
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # 初始化设备类型（默认显示AT命令）
        self.on_device_type_changed(0)

    def on_device_type_changed(self, index):
        """设备类型切换"""
        device_type = self.device_type_combo.currentText()

        # 根据设备类型显示/隐藏AT命令行
        if "蓝牙" in device_type:
            self.at_widget.setVisible(True)
            self.bt_data_input.setPlaceholderText("输入要发送给手机的数据...")
        else:
            self.at_widget.setVisible(False)
            self.bt_data_input.setPlaceholderText("输入要发送的数据...")

        # 记录日志
        try:
            main_window = self.window()
            if hasattr(main_window, "append_debug_log"):
                main_window.append_debug_log(f"切换设备类型: {device_type}", "INFO")
        except:
            pass

    def on_baudrate_changed(self, baudrate_str):
        """波特率更改 - 发送配置命令到FPGA"""
        try:
            baudrate = int(baudrate_str)

            # 构造4字节波特率数据（小端序）
            payload = baudrate.to_bytes(4, byteorder="little")

            # 发送配置命令
            if self.serial_manager:
                self.serial_manager.send_command(CMD_UART_CONFIG, payload)

                # 记录日志到主窗口
                main_window = self.window()
                if hasattr(main_window, "append_debug_log"):
                    main_window.append_debug_log(
                        f"配置蓝牙波特率: {baudrate_str} bps",
                        "INFO",
                    )

                # 本地日志
                self.log_text.append(f"[配置] 设置波特率为 {baudrate_str} bps")
        except Exception as e:
            print(f"波特率配置失败: {e}")

    def toggle_hex_display(self, state):
        """切换HEX显示模式"""
        self.hex_display = state == Qt.CheckState.Checked.value

    def toggle_timestamp(self, state):
        """切换时间戳显示"""
        self.show_timestamp = state == Qt.CheckState.Checked.value

    def toggle_hex_send(self, state):
        """切换HEX发送模式"""
        self.hex_send = state == Qt.CheckState.Checked.value
        # 更新输入框提示文字
        if self.hex_send:
            self.bt_data_input.setPlaceholderText(
                "输入十六进制数据 (例: 48 65 6C 6C 6F)"
            )
        else:
            self.bt_data_input.setPlaceholderText("输入要发送的数据...")

    def clear_log(self):
        """清除日志"""
        self.log_text.clear()
        # 重置解码器状态和数据缓冲区
        self.decoder.reset()
        self.data_buffer.clear()
        self.bt_data_accumulator.clear()
        self.bt_display_timer.stop()

    def format_data_display(self, data, is_send=True, force_hex=False):
        """格式化数据显示
        Args:
            data: 要显示的数据（字符串或字节）
            is_send: True表示发送，False表示接收
            force_hex: 强制使用HEX显示（用于HEX发送模式）
        """
        from datetime import datetime

        # 时间戳
        timestamp = ""
        if self.show_timestamp:
            timestamp = f"[{datetime.now().strftime('%H:%M:%S')}] "

        # 方向标识
        direction = "📤 发送" if is_send else "📥 接收"

        # 数据格式化
        if self.hex_display or force_hex:
            # HEX显示模式：统一转换为字节后显示为十六进制
            if isinstance(data, str):
                data_bytes = data.encode("utf-8")
            else:
                data_bytes = data
            hex_str = " ".join(f"{b:02X}" for b in data_bytes)
            return f"{timestamp}{direction}: {hex_str}"
        else:
            # 文本显示模式
            if isinstance(data, bytes):
                # 使用增量解码器处理可能的多字节字符分包
                try:
                    # 对于接收数据，使用增量解码器（累积不完整字符）
                    # 对于发送数据，直接解码（发送时是完整的）
                    if not is_send:
                        data = self.decoder.decode(
                            data, False
                        )  # False表示可能还有后续数据
                    else:
                        data = data.decode("utf-8", errors="replace")
                except Exception as e:
                    # 解码失败时尝试直接解码
                    try:
                        data = data.decode("utf-8", errors="replace")
                    except:
                        data = str(data)
            return f"{timestamp}{direction}: {data}"

    def bt_send_data(self):
        """发送数据到蓝牙"""
        data = self.bt_data_input.text()
        if not data:
            QMessageBox.warning(self, "输入错误", "请输入要发送的数据")
            return

        try:
            # 根据HEX发送模式处理数据
            if self.hex_send:
                # HEX模式：解析十六进制字符串
                hex_str = data.replace(" ", "").replace(",", "")
                if len(hex_str) % 2 != 0:
                    QMessageBox.warning(self, "格式错误", "HEX数据长度必须是偶数")
                    return
                payload = bytes.fromhex(hex_str)
            else:
                # 文本模式：直接编码
                payload = data.encode("utf-8")

            # 显示发送的数据（HEX发送时强制使用HEX显示）
            if self.hex_send:
                # HEX发送模式：始终以HEX格式显示
                from datetime import datetime

                hex_display_str = " ".join(f"{b:02X}" for b in payload)
                timestamp = (
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    if self.show_timestamp
                    else ""
                )
                self.log_text.append(f"{timestamp}📤 发送: {hex_display_str}")
            else:
                # 普通文本发送：根据hex_display设置显示
                self.log_text.append(
                    self.format_data_display(
                        payload, is_send=True, force_hex=self.hex_display
                    )
                )

            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_UART_SEND, payload)
            else:
                self.log_text.append("❌ CDC串口未连接")

        except ValueError as e:
            QMessageBox.warning(self, "格式错误", f"HEX数据格式错误: {str(e)}")
            return
        except Exception as e:
            self.log_text.append(f"❌ 发送错误: {str(e)}")
            return

    def bt_send_at(self):
        """发送AT命令"""
        at_cmd = self.bt_at_input.text()
        if not at_cmd:
            QMessageBox.warning(self, "输入错误", "请输入AT命令")
            return

        payload = at_cmd.encode("utf-8")

        # 显示发送的命令
        self.log_text.append(self.format_data_display(payload, is_send=True))

        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_UART_AT, payload)
        else:
            self.log_text.append("❌ CDC串口未连接")

    def handle_uart_data(self, data):
        """处理接收到的UART数据，智能分离应答帧和蓝牙数据

        简化策略：
        - 只显示蓝牙数据（非 AA 55 应答帧的裸数据）
        - 跳过所有应答帧（AA 55 ...）
        - 不区分命令类型，让各个面板自己处理数据
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
                # 没有AA标记，全部是蓝牙数据（或其他裸数据，但不是应答帧）
                bt_data = bytes(self.data_buffer)
                # 只显示可打印的ASCII或UTF-8数据，过滤掉二进制数据（如温度值 00 00）
                if self.is_printable_data(bt_data):
                    self.display_bluetooth_data(bt_data)
                # 清空缓冲区（无论是否显示）
                self.data_buffer.clear()
                break

            # 找到AA标记
            if aa_idx > 0:
                # AA前面有数据
                bt_data = bytes(self.data_buffer[:aa_idx])
                # 只显示可打印数据
                if self.is_printable_data(bt_data):
                    self.display_bluetooth_data(bt_data)
                self.data_buffer = self.data_buffer[aa_idx:]

            # 现在buffer开头是AA，检查是否是完整应答帧
            if len(self.data_buffer) < 7:
                # 数据不足7字节，等待更多数据
                break

            if self.data_buffer[1] == 0x55:
                # 这是应答帧，直接跳过（不管是什么命令）
                self.data_buffer = self.data_buffer[7:]
            else:
                # AA后面不是55，这个AA只是普通数据中的一个字节
                bt_data = bytes([self.data_buffer[0]])
                if self.is_printable_data(bt_data):
                    self.display_bluetooth_data(bt_data)
                self.data_buffer = self.data_buffer[1:]

    def is_printable_data(self, data):
        """判断数据是否为可打印数据（过滤二进制数据如温度值、SPI数据等）"""
        if not data:
            return False

        # 如果数据很短（<4字节）且包含很多0x00-0x1F的控制字符，可能是二进制数据
        if len(data) < 4:
            non_printable = sum(
                1 for b in data if b < 0x20 and b not in (0x0A, 0x0D, 0x09)
            )
            if non_printable > len(data) * 0.5:  # 超过50%是控制字符
                return False

        # 尝试解码为UTF-8，如果失败则可能是二进制数据
        try:
            text = data.decode("utf-8", errors="strict")
            # 检查是否包含可打印字符
            printable_chars = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
            return printable_chars > 0
        except:
            return False

    def display_bluetooth_data(self, data):
        """累积蓝牙数据，批量显示以减少刷新"""
        if not data:
            return

        # 累积数据
        self.bt_data_accumulator.extend(data)

        # 重启定时器（50ms内没有新数据就显示）
        self.bt_display_timer.stop()
        self.bt_display_timer.start(50)

    def flush_bluetooth_data(self):
        """刷新显示累积的蓝牙数据"""
        if len(self.bt_data_accumulator) > 0:
            data = bytes(self.bt_data_accumulator)

            # 调试：打印原始字节（可以临时查看数据）
            # print(f"DEBUG: 接收到 {len(data)} 字节: {data.hex(' ')}")
            # print(f"DEBUG: 尝试UTF-8解码: {data.decode('utf-8', errors='replace')}")

            self.log_text.append(self.format_data_display(data, is_send=False))
            self.bt_data_accumulator.clear()

    def display_debug_frame(self, frame):
        """显示调试应答帧到全局日志窗口"""
        # 解析应答帧: AA 55 [CMD] [STATUS] [LEN_L] [LEN_H] [CS]
        cmd = frame[2]
        status = frame[3]
        status_text = "成功" if status == 0x18 else f"失败(0x{status:02X})"
        hex_str = " ".join(f"{b:02X}" for b in frame)

        # 命令名称映射（便于反向解析）
        cmd_names = {
            0x01: "DDS参数设置",
            0x02: "DAC数据输出",
            0x03: "数码管显示",
            0x04: "595扫描控制",
            0x05: "蓝牙使能",
            0x91: "UART发送(蓝牙)",
            0xA0: "PWM配置",
            0xB0: "序列发生器",
        }
        cmd_name = cmd_names.get(cmd, f"未知命令")

        # 发送到全局日志窗口
        message = f"应答帧: {hex_str} | [{cmd_name}(0x{cmd:02X})] {status_text}"

        # 获取主窗口并调用append_debug_log
        try:
            # 通过父窗口链向上查找主窗口
            main_window = self.window()
            if hasattr(main_window, "append_debug_log"):
                main_window.append_debug_log(message, "RESPONSE")
        except Exception as e:
            print(f"无法发送到全局日志: {e}")


# ============================================================================
# 1-Wire设备控制子模块（DS18B20温度传感器）
# ============================================================================


class OneWireDevicePanel(QWidget):
    """1-Wire设备控制面板（DS18B20温度传感器）"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # DS18B20控制组（简化）
        ds_group = QGroupBox("DS18B20 温度传感器")
        ds_layout = QVBoxLayout()
        ds_layout.setSpacing(6)

        # 第一行：温度显示和控制按钮
        row1 = QHBoxLayout()
        self.ds_temp_label = QLabel("温度: --°C")
        self.ds_temp_label.setStyleSheet(
            "font-size: 18pt; font-weight: bold; color: #FF5722;"
        )
        row1.addWidget(self.ds_temp_label)

        row1.addStretch()

        self.ds_reset_btn = QPushButton("复位")
        self.ds_reset_btn.clicked.connect(self.ds_reset)
        row1.addWidget(self.ds_reset_btn)

        self.ds_search_btn = QPushButton("搜索")
        self.ds_search_btn.clicked.connect(self.ds_search)
        row1.addWidget(self.ds_search_btn)

        self.ds_read_btn = QPushButton("读取温度")
        self.ds_read_btn.clicked.connect(self.ds_read_temp)
        row1.addWidget(self.ds_read_btn)

        ds_layout.addLayout(row1)

        # 第二行：自动刷新
        row2 = QHBoxLayout()
        self.ds_auto_refresh_check = QCheckBox("自动刷新")
        self.ds_auto_refresh_check.toggled.connect(self.toggle_auto_refresh)
        row2.addWidget(self.ds_auto_refresh_check)

        row2.addWidget(QLabel("间隔:"))
        self.ds_refresh_spin = QSpinBox()
        self.ds_refresh_spin.setRange(1, 60)
        self.ds_refresh_spin.setValue(2)
        self.ds_refresh_spin.setSuffix(" 秒")
        self.ds_refresh_spin.setMaximumWidth(80)
        row2.addWidget(self.ds_refresh_spin)

        row2.addStretch()
        ds_layout.addLayout(row2)

        ds_group.setLayout(ds_layout)
        main_layout.addWidget(ds_group)

        # 交互日志（简化）
        log_group = QGroupBox("1-Wire交互日志")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 4, 6, 4)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # 自动刷新定时器
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.ds_read_temp)

    def ds_reset(self):
        """1-Wire总线复位"""
        self.log_text.append("📤 CDC → FPGA: 1-Wire总线复位 (0xB0)")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_ONEWIRE_RESET, b"")
            self.log_text.append("⏳ 等待CH340应答...")
        else:
            self.log_text.append("❌ CDC串口未连接")

    def ds_search(self):
        """搜索1-Wire设备"""
        self.log_text.append("📤 CDC → FPGA: 搜索1-Wire设备 (0xB2)")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_ONEWIRE_SEARCH, b"")
            self.log_text.append("⏳ 等待CH340应答（设备ROM列表）...")
        else:
            self.log_text.append("❌ CDC串口未连接")

    def ds_read_temp(self):
        """读取温度"""
        self.log_text.append("📤 CDC → FPGA: 读取DS18B20温度 (0xB1)")
        if self.serial_manager and self.serial_manager.is_connected():
            self.serial_manager.send_command(CMD_ONEWIRE_READ_TEMP, b"")
            self.log_text.append("⏳ 等待CH340应答（温度值）...")
        else:
            self.log_text.append("❌ CDC串口未连接")

    def toggle_auto_refresh(self, enabled):
        """切换自动刷新"""
        if enabled:
            interval = self.ds_refresh_spin.value() * 1000
            self.refresh_timer.start(interval)
            self.log_text.append(
                f"✅ 自动刷新已启动 (间隔: {self.ds_refresh_spin.value()}秒)"
            )
        else:
            self.refresh_timer.stop()
            self.log_text.append("⏹ 自动刷新已停止")

    def handle_temp_response(self, temp_value):
        """处理温度应答"""
        self.ds_temp_label.setText(f"温度: {temp_value:.2f}°C")
        self.log_text.append(f"📥 CH340 ← FPGA: 温度 = {temp_value:.2f}°C")


# ============================================================================
# 设备中心主Tab
# ============================================================================


class DeviceCenterTab(QWidget):
    """设备中心主标签页"""

    def __init__(self, serial_manager, pwm_controller=None):
        super().__init__()
        self.serial_manager = serial_manager
        self.pwm_controller = pwm_controller
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # 子标签页
        sub_tabs = QTabWidget()

        # Tab 0: 电机控制（传递pwm_controller引用）
        self.motor_panel = MotorControlPanel(self.serial_manager, self.pwm_controller)
        sub_tabs.addTab(self.motor_panel, "⚙️ 电机控制")

        # Tab 1: I2C设备（使用新的简化版）
        from protocol.i2c_panel_simple import I2CDevicePanelSimple

        i2c_panel = I2CDevicePanelSimple(self.serial_manager)
        sub_tabs.addTab(i2c_panel, "🔌 I2C设备")

        # Tab 2: SPI设备（使用新的简化版）
        from protocol.spi_panel_simple import SPIDevicePanelSimple

        spi_panel = SPIDevicePanelSimple(self.serial_manager)
        sub_tabs.addTab(spi_panel, "💾 SPI设备")

        # Tab 3: UART设备
        uart_panel = UARTDevicePanel(self.serial_manager)
        sub_tabs.addTab(uart_panel, "📡 UART设备")

        # Tab 4: CAN总线 - 🔥 V2.0新增：SIT1042兼容SJA1000协议
        from protocol.can_panel import CANBusPanelSimple

        can_panel = CANBusPanelSimple(self.serial_manager)
        sub_tabs.addTab(can_panel, "🚗 CAN总线")

        # Tab 5: 1-Wire设备（DS18B20温度传感器）
        from protocol.ds18b20_panel import DS18B20Panel

        ds18b20_panel = DS18B20Panel(self.serial_manager)
        sub_tabs.addTab(ds18b20_panel, "🌡️ 1-Wire设备")

        main_layout.addWidget(sub_tabs)


# ============================================================================
# 测试入口
# ============================================================================


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 创建测试窗口
    window = DeviceCenterTab(None)
    window.resize(1000, 700)
    window.setWindowTitle("设备中心 - 测试")
    window.show()

    sys.exit(app.exec())
