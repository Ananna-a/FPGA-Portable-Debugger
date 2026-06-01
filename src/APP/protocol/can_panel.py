#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAN总线面板 - V2.3 (仅CH340 UART接收通道)
日期: 2025-11-29

功能:
  - CAN快捷配置（波特率选择）
  - 标准帧/扩展帧发送（支持DLC 0-8字节）✅
  - CAN帧接收与解析（仅通过CH340 UART）✅
  - 交互数据显示

硬件:
  - FPGA侧收发器: SIT1042AQT/3 (兼容SJA1000协议)
  - 终端电阻: 120Ω（拨码开关ON）
  - 引脚: CAN_TX=G2, CAN_RX=H2
  - 测试设备: PCAN-USB (SJA1000)

命令码定义 (0xC0-0xCF):
  0xC0: CAN配置（波特率）
  0xC1: 发送CAN帧（总是发送11字节：3字节头+8字节数据）✅
  0xC2: 设置过滤器
  0xC3: 读取状态
  0xC4: CAN接收数据上报（FPGA主动发送）

V2.3更新:
  - 删除UDP以太网接收通道
  - 仅保留CH340 UART接收（复用SPI/DSA模式）
  - CAN接收数据通过uart_tx_mux通道上报
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
    QRadioButton,
    QButtonGroup,
    QFileDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from datetime import datetime

# 导入命令码定义
import sys
import os
from core.serial_protocol import (
    CMD_CAN_CONFIG,
    CMD_CAN_SEND,
    CMD_CAN_FILTER,
    CMD_CAN_STATUS,
    CMD_CAN_RX_DATA,
)

# ✅ V2.3: 删除UDP接收器导入
# from core.can_udp_receiver import CANUDPReceiver

# 模块ID（用于应答帧识别）
MOD_ID_CAN = 0x30


class CANBusPanelSimple(QWidget):
    """CAN总线控制面板 - 紧凑版"""

    def __init__(self, serial_manager):
        super().__init__()
        self.serial_manager = serial_manager
        self.show_timestamp = True
        self.can_initialized = False  # CAN控制器初始化标志

        # 统计计数器
        self.tx_count = 0
        self.rx_count = 0
        self.error_count = 0

        # 调试开关
        self.debug_mode = False  # 关闭调试模式，避免日志刷屏

        # 消息记录（用于导出）
        self.message_log = []

        # ✅ V2.3: 删除UDP接收器，仅使用CH340 UART接收
        # self.udp_receiver = CANUDPReceiver(local_port=6103)
        # self.udp_receiver.can_frame_received.connect(self.handle_udp_can_frame)
        # self.udp_receiver.stats_updated.connect(self.update_udp_stats)

        self.init_ui()

        # ========== 注册串口数据监听器 ==========
        if self.serial_manager:
            # 🔥 V2.5: 连接CAN专用信号（避免其他模块数据混入）
            self.serial_manager.can_data_received.connect(self.handle_can_data_stream)
            # 应答帧仍然走通用信号
            self.serial_manager.data_received.connect(self.handle_rx_response)

        # ✅ V2.3: 删除UDP启动
        # self.udp_receiver.start()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ========== 顶部：快捷配置行 ==========
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        # 波特率选择（对应FPGA例程README.md参数表）
        top_layout.addWidget(QLabel("波特率:"))
        self.baudrate_combo = QComboBox()
        # 索引对应：0=1M, 1=500k, 2=100k, 3=10k, 4=5k（按例程表格顺序）
        self.baudrate_combo.addItems(["1M", "500k", "100k", "10k", "5k"])
        self.baudrate_combo.setCurrentIndex(0)  # 默认1Mbps（索引0）
        self.baudrate_combo.setMinimumWidth(85)
        self.baudrate_combo.setToolTip(
            "CAN波特率（按fpga-can-main例程）\n"
            "1M:   高速40m, division=50   (索引0) ⭐\n"
            "500k: 标准100m, division=100  (索引1)\n"
            "100k: 中距离500m, division=500 (索引2)\n"
            "10k:  超长距, division=5000 (索引3)\n"
            "5k:   极长距, division=10000 (索引4)\n\n"
            "注意：当前FPGA固定1MHz，配置命令仅用于记录"
        )
        top_layout.addWidget(self.baudrate_combo)

        # 应用配置按钮
        self.apply_config_btn = QPushButton("⚙️ 配置")
        self.apply_config_btn.setMinimumWidth(85)
        self.apply_config_btn.clicked.connect(self.apply_can_config)
        top_layout.addWidget(self.apply_config_btn)

        top_layout.addSpacing(15)

        # 帧类型选择
        self.frame_type_group = QButtonGroup()
        self.std_frame_radio = QRadioButton("标准帧")
        self.ext_frame_radio = QRadioButton("扩展帧")
        self.std_frame_radio.setChecked(True)
        self.frame_type_group.addButton(self.std_frame_radio, 0)
        self.frame_type_group.addButton(self.ext_frame_radio, 1)
        top_layout.addWidget(self.std_frame_radio)
        top_layout.addWidget(self.ext_frame_radio)

        top_layout.addSpacing(15)

        # CAN ID输入
        top_layout.addWidget(QLabel("ID:"))
        self.can_id_input = QLineEdit("0x123")
        self.can_id_input.setMinimumWidth(100)
        self.can_id_input.setPlaceholderText("0x123")
        self.can_id_input.setToolTip(
            "标准帧: 11位ID (0x000~0x7FF)\n扩展帧: 29位ID (0x00000000~0x1FFFFFFF)"
        )
        top_layout.addWidget(self.can_id_input)

        # DLC选择
        top_layout.addWidget(QLabel("DLC:"))
        self.dlc_spin = QSpinBox()
        self.dlc_spin.setRange(0, 8)
        self.dlc_spin.setValue(8)
        self.dlc_spin.setMinimumWidth(60)
        self.dlc_spin.valueChanged.connect(self.update_data_fields)
        top_layout.addWidget(self.dlc_spin)

        top_layout.addStretch()

        main_layout.addLayout(top_layout)

        # ========== 中部：8字节数据输入 + 快捷按钮 ==========
        mid_layout = QHBoxLayout()
        mid_layout.setSpacing(8)

        mid_layout.addWidget(QLabel("数据:"))

        # 创建8个数据输入框
        self.data_inputs = []
        for i in range(8):
            data_input = QLineEdit("00")
            data_input.setMinimumWidth(40)
            data_input.setMaximumWidth(50)
            data_input.setAlignment(Qt.AlignCenter)
            data_input.setPlaceholderText("00")
            data_input.setToolTip(f"Byte[{i}]")
            self.data_inputs.append(data_input)
            mid_layout.addWidget(data_input)

        mid_layout.addSpacing(15)

        # 发送按钮
        self.send_btn = QPushButton("发送")
        self.send_btn.setMinimumWidth(80)
        self.send_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 5px;"
        )
        self.send_btn.clicked.connect(self.send_can_frame)
        mid_layout.addWidget(self.send_btn)

        # 快捷预设按钮
        preset1_btn = QPushButton("预设1")
        preset1_btn.setMinimumWidth(70)
        preset1_btn.setToolTip("测试帧: ID=0x123, Data=AA 55 01...")
        preset1_btn.clicked.connect(lambda: self.load_preset(0))
        mid_layout.addWidget(preset1_btn)

        preset2_btn = QPushButton("预设2")
        preset2_btn.setMinimumWidth(70)
        preset2_btn.setToolTip("心跳包: ID=0x100, Data=00 01")
        preset2_btn.clicked.connect(lambda: self.load_preset(1))
        mid_layout.addWidget(preset2_btn)

        mid_layout.addStretch()

        main_layout.addLayout(mid_layout)

        # ========== 分隔线 ==========
        separator = QWidget()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #ccc;")
        main_layout.addWidget(separator)

        # ========== 日志区域头部 ==========
        log_header = QHBoxLayout()
        log_header.setSpacing(8)

        log_title = QLabel("📡 CAN总线交互数据")
        log_title.setStyleSheet("font-weight: bold;")
        log_header.addWidget(log_title)

        log_header.addStretch()

        # 统计信息
        self.stats_label = QLabel("TX:0 | RX:0 | ERR:0")
        self.stats_label.setStyleSheet("color: #666; font-size: 9pt;")
        self.stats_label.setToolTip("发送 | 接收 | 错误")
        log_header.addWidget(self.stats_label)

        # 时间戳开关
        self.timestamp_checkbox = QCheckBox("时间戳")
        self.timestamp_checkbox.setChecked(True)
        self.timestamp_checkbox.stateChanged.connect(self.toggle_timestamp)
        log_header.addWidget(self.timestamp_checkbox)

        # 导出报文按钮
        self.export_btn = QPushButton("导出")
        self.export_btn.setMaximumWidth(60)
        self.export_btn.clicked.connect(self.export_messages)
        self.export_btn.setToolTip("导出CAN报文到文件")
        log_header.addWidget(self.export_btn)

        # 清除日志按钮
        self.clear_log_btn = QPushButton("清除")
        self.clear_log_btn.setMaximumWidth(60)
        self.clear_log_btn.clicked.connect(self.clear_log)
        log_header.addWidget(self.clear_log_btn)

        main_layout.addLayout(log_header)

        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setPlaceholderText(
            "CAN总线交互数据显示区\n\n"
            "硬件配置:\n"
            "  FPGA侧收发器: SIT1042AQT/3 (兼容SJA1000)\n"
            "  终端电阻: 120Ω (拨码开关ON)\n"
            "  引脚: CAN_TX=G2, CAN_RX=H2\n"
            "  测试设备: PCAN-USB (SJA1000)\n\n"
            "⚠️ 注意事项:\n"
            "  1. CAN总线两端必须接120Ω终端电阻\n"
            "  2. 测试时需要至少2个节点（FPGA + PCAN-USB）\n"
            "  3. 波特率必须与PCAN-View设置一致\n"
            "  4. PCAN-USB默认1Mbps，请先统一波特率\n"
            "  5. SIT1042/SJA1000支持标准帧和扩展帧\n\n"
            "快速测试步骤:\n"
            "  1. 连接FPGA与PCAN-USB到CAN总线\n"
            "  2. PCAN-View选择1Mbps，点击Connect\n"
            "  3. 本面板点击'⚙️ 配置'设置1Mbps\n"
            "  4. 从PCAN发送测试帧，观察本面板接收\n"
            "  5. 从本面板发送，观察PCAN-View接收\n"
        )
        main_layout.addWidget(self.log_text, 1)

    # ========== 配置相关方法 ==========

    def apply_can_config(self):
        """应用CAN配置（波特率）"""
        baud_index = self.baudrate_combo.currentIndex()
        baud_text = self.baudrate_combo.currentText()

        try:
            # 构造Payload: [波特率索引]
            # 索引对应（按例程README.md）: 0=1M, 1=500k, 2=100k, 3=10k, 4=5k
            payload = bytes([baud_index])

            # 发送命令（使用标准CDC协议接口）
            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_CAN_CONFIG, payload)
                self.can_initialized = True  # 标记为已初始化
                self.log(f"✅ 配置波特率: {baud_text}", "INFO")
            else:
                self.log("❌ 错误: 串口未连接", "ERROR")

        except Exception as e:
            self.log(f"❌ 配置失败: {str(e)}", "ERROR")

    def send_can_frame(self):
        """发送CAN帧"""
        try:
            # 🔥 自动初始化检查：如果未配置过，先自动配置默认波特率
            if not self.can_initialized:
                self.log("⚠️ CAN未初始化，正在自动配置默认波特率(1Mbps)...", "WARNING")
                default_baud = 0  # 索引0 = 1Mbps（按例程README.md）
                payload_config = bytes([default_baud])
                if self.serial_manager and self.serial_manager.is_connected():
                    self.serial_manager.send_command(CMD_CAN_CONFIG, payload_config)
                    self.can_initialized = True
                    import time

                    time.sleep(0.1)  # 等待初始化完成
                    self.log("✅ 自动配置完成（1Mbps），继续发送...", "INFO")

            # 解析CAN ID
            can_id_str = self.can_id_input.text().strip()
            if can_id_str.startswith("0x") or can_id_str.startswith("0X"):
                can_id = int(can_id_str, 16)
            else:
                can_id = int(can_id_str)

            # 验证ID范围
            is_extended = self.ext_frame_radio.isChecked()
            if is_extended:
                if can_id > 0x1FFFFFFF:
                    self.log(
                        "❌ 错误: 扩展帧ID超出范围 (0x00000000~0x1FFFFFFF)", "ERROR"
                    )
                    return
            else:
                if can_id > 0x7FF:
                    self.log("❌ 错误: 标准帧ID超出范围 (0x000~0x7FF)", "ERROR")
                    return

            # 获取DLC
            dlc = self.dlc_spin.value()

            # 获取数据（总是读取8字节，未使用的补0）
            data_bytes = bytearray()
            for i in range(8):  # ✅ 总是处理8字节
                try:
                    byte_str = self.data_inputs[i].text().strip()
                    if byte_str:
                        data_bytes.append(int(byte_str, 16))
                    else:
                        data_bytes.append(0)
                except ValueError:
                    self.log(f"❌ 错误: Data[{i}] 格式错误", "ERROR")
                    return

            # 构造Payload (匹配FPGA格式 V2.2)
            # ✅ 可变长度机制说明：
            #    - 上位机→FPGA：总是发送11字节（3头+8数据）避免FPGA读取越界
            #    - FPGA内部：根据DLC提取有效数据（can_tx_len=DLC）
            #    - CAN总线：IP核根据DLC物理发送0-8字节（真正的可变长度！）
            #
            # 格式: [frame_type][id_h][id_l_dlc][data0-7]
            # Byte0: frame_type (0=标准帧, 1=扩展帧)
            # 标准帧: Byte1: ID[10:3], Byte2: ID[2:0]<<5 | DLC[3:0]
            # Byte3-10: data[0-7] (8字节数据，DLC指示有效长度)

            payload = bytearray()
            payload.append(0x01 if is_extended else 0x00)  # frame_type

            if is_extended:
                # 扩展帧: 4字节ID (大端序)
                # ⚠️ 29位ID对齐：FPGA只解析[31:3]作为29位ID
                #    所以需要将ID左移3位，存入4字节
                #    发送: (can_id << 3) 作为32位，高29位是有效ID
                id_shifted = can_id << 3  # 左移3位对齐
                payload.extend(id_shifted.to_bytes(4, byteorder="big"))
                payload.append(dlc & 0x0F)
            else:
                # 标准帧: 11位ID拆分
                id_high = (can_id >> 3) & 0xFF  # 高8位
                id_low_dlc = ((can_id & 0x07) << 5) | (dlc & 0x0F)  # 低3位+4位DLC
                payload.append(id_high)
                payload.append(id_low_dlc)

            # ✅ 发送完整8字节数据（简化FPGA协议，避免边界检查）
            # 注意：CAN总线上实际传输的字节数由DLC决定（真正可变！）
            payload.extend(data_bytes)

            # 发送命令（使用标准CDC协议接口）
            if self.serial_manager and self.serial_manager.is_connected():
                self.serial_manager.send_command(CMD_CAN_SEND, bytes(payload))

                # 格式化日志显示（只显示DLC指定的有效字节）
                frame_type_str = "扩展帧" if is_extended else "标准帧"
                id_format = "0x{:08X}" if is_extended else "0x{:03X}"
                data_str = " ".join(
                    [f"{b:02X}" for b in data_bytes[:dlc]]
                )  # ✅ 只显示有效字节

                # 记录消息（用于导出，只保存有效数据）
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self.message_log.append(
                    {
                        "timestamp": timestamp,
                        "direction": "TX",
                        "type": "X" if is_extended else "S",
                        "id": can_id,
                        "dlc": dlc,
                        "data": list(data_bytes[:dlc]),  # ✅ 只保存有效数据
                    }
                )

                self.log(
                    f"📤 发送 [{frame_type_str}] ID={id_format.format(can_id)} "
                    f"DLC={dlc} Data=[{data_str}]",
                    "TX",
                )

                self.tx_count += 1
                self.update_stats()

            else:
                self.log("❌ 错误: 串口未连接", "ERROR")

        except Exception as e:
            self.log(f"❌ 发送失败: {str(e)}", "ERROR")

    # ========== 辅助方法 ==========

    def update_data_fields(self, dlc):
        """根据DLC更新数据输入框的可用状态"""
        for i, input_widget in enumerate(self.data_inputs):
            if i < dlc:
                input_widget.setEnabled(True)
                input_widget.setStyleSheet("")
            else:
                input_widget.setEnabled(False)
                input_widget.setStyleSheet("background-color: #f0f0f0;")

    def load_preset(self, preset_id):
        """加载快捷预设"""
        if preset_id == 0:
            # 预设1: 测试帧
            self.std_frame_radio.setChecked(True)
            self.can_id_input.setText("0x123")
            self.dlc_spin.setValue(8)
            preset_data = ["AA", "55", "01", "02", "03", "04", "05", "06"]
            for i, val in enumerate(preset_data):
                self.data_inputs[i].setText(val)
            self.log("📋 加载预设: 测试帧 (ID=0x123, Data=AA 55 01...)", "INFO")

        elif preset_id == 1:
            # 预设2: 心跳包
            self.std_frame_radio.setChecked(True)
            self.can_id_input.setText("0x100")
            self.dlc_spin.setValue(2)
            self.data_inputs[0].setText("00")
            self.data_inputs[1].setText("01")
            self.log("📋 加载预设: 心跳包 (ID=0x100, Data=00 01)", "INFO")

    def clear_send_fields(self):
        """清空发送区域"""
        self.can_id_input.setText("0x123")
        self.dlc_spin.setValue(8)
        for input_widget in self.data_inputs:
            input_widget.setText("00")
        self.log("🧹 清空发送区域", "INFO")

    def update_stats(self):
        """更新统计信息"""
        self.stats_label.setText(
            f"TX:{self.tx_count} | RX:{self.rx_count} | ERR:{self.error_count}"
        )

    # ========== 数据接收处理 ==========

    def handle_can_data_stream(self, data):
        """处理CAN纯数据流（来自专用信号）"""
        self.parse_received_can_frame(data)

    def handle_rx_response(self, data):
        """处理接收到的应答帧（不处理CAN数据流）"""
        try:
            # 过滤掉非CAN模块的应答帧
            if len(data) < 5:
                return

            # 检查帧头和模块ID
            if data[0] == 0xAA and data[1] == 0x55 and data[2] == MOD_ID_CAN:
                func_id = data[3]

                if func_id == CMD_CAN_CONFIG:
                    # 配置应答
                    status = data[5] if len(data) > 5 else 0xFF
                    if status == 0x00:
                        self.log("✅ CAN配置成功", "SUCCESS")
                    else:
                        self.log(f"❌ CAN配置失败 (错误码: 0x{status:02X})", "ERROR")
                        self.error_count += 1
                        self.update_stats()

                elif func_id == CMD_CAN_SEND:
                    # 发送应答
                    status = data[5] if len(data) > 5 else 0xFF
                    if status == 0x00:
                        pass  # 发送成功，已在send方法中处理
                    else:
                        self.log(f"❌ CAN发送失败 (错误码: 0x{status:02X})", "ERROR")
                        self.error_count += 1
                        self.update_stats()

        except Exception as e:
            self.log(f"❌ 数据处理异常: {str(e)}", "ERROR")

    def parse_received_can_frame(self, data):
        """解析接收到的CAN帧 (纯数据流，无帧头)
        数据格式: [frame_type][ID_bytes][DLC_or_data][data0-7]
        """
        try:
            if len(data) < 3:
                return

            # ✅ V2.4: 纯数据流格式（参考SPI）
            frame_type = data[0]  # 0x00=标准帧, 0x01=扩展帧
            is_extended = frame_type == 0x01

            if is_extended:
                # 扩展帧: [type=0x01][ID3][ID2][ID1][ID0][data0-7]
                # 注意: 扩展帧没有单独的DLC字节，DLC从数据长度计算
                # ⚠️ FPGA发送4字节：[ID3][ID2][ID1][ID0_high5bits_000]
                #    即：ID0只有高5位有效，低3位被0填充
                if len(data) < 6:
                    return

                # 解析ID：29位 = ID3(8) + ID2(8) + ID1(8) + ID0_high5(5)
                id_bytes = data[1:5]
                can_id = (
                    (id_bytes[0] << 21)
                    | (id_bytes[1] << 13)
                    | (id_bytes[2] << 5)
                    | (id_bytes[3] >> 3)  # 🔥 只取高5位
                )
                dlc = len(data) - 5  # DLC从长度计算
                data_bytes = data[5:]  # 剩余的是数据
                id_format = "0x{:08X}"
            else:
                # 标准帧: [type=0x00][ID_H][ID_L_DLC][data0-7]
                # Byte2格式: {ID[2:0], 1'b0, DLC[3:0]}
                if len(data) < 3:
                    return
                id_high = data[1]
                id_low_dlc = data[2]
                can_id = (id_high << 3) | ((id_low_dlc >> 5) & 0x07)
                dlc = id_low_dlc & 0x0F

                # 验证数据长度
                expected_len = 3 + dlc
                if len(data) < expected_len:
                    self.log(
                        f"⚠️ [CAN标准帧] 数据不完整: 期望{expected_len}字节, 实际{len(data)}字节",
                        "WARNING",
                    )
                    return

                data_bytes = data[3 : 3 + dlc]
                id_format = "0x{:03X}"

            # 格式化显示
            frame_type_str = "扩展帧" if is_extended else "标准帧"
            data_str = " ".join([f"{b:02X}" for b in data_bytes])

            # 记录消息（用于导出）
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.message_log.append(
                {
                    "timestamp": timestamp,
                    "direction": "RX",
                    "type": "X" if is_extended else "S",
                    "id": can_id,
                    "dlc": dlc,
                    "data": list(data_bytes),
                }
            )

            self.log(
                f"📥 接收 [{frame_type_str}] ID={id_format.format(can_id)} "
                f"DLC={dlc} Data=[{data_str}]",
                "RX",
            )

            self.rx_count += 1
            self.update_stats()

        except Exception as e:
            self.log(f"❌ CAN帧解析错误: {str(e)}", "ERROR")
            self.error_count += 1
            self.update_stats()

    # ========== 日志相关 ==========

    def log(self, message, msg_type="INFO"):
        """添加日志消息"""
        # ✅ V2.4: 过滤DEBUG日志（仅在调试模式显示）
        if msg_type == "DEBUG" and not self.debug_mode:
            return

        if self.show_timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log_entry = f"{timestamp} | {message}"
        else:
            log_entry = message

        # 根据消息类型设置颜色
        if msg_type == "ERROR":
            log_entry = f'<span style="color: red;">{log_entry}</span>'
        elif msg_type == "SUCCESS":
            log_entry = f'<span style="color: green;">{log_entry}</span>'
        elif msg_type == "TX":
            log_entry = f'<span style="color: blue;">{log_entry}</span>'
        elif msg_type == "RX":
            log_entry = f'<span style="color: purple;">{log_entry}</span>'
        elif msg_type == "WARNING":
            log_entry = f'<span style="color: orange;">{log_entry}</span>'
        elif msg_type == "DEBUG":
            log_entry = f'<span style="color: gray;">{log_entry}</span>'
        else:
            log_entry = f'<span style="color: black;">{log_entry}</span>'

        self.log_text.append(log_entry)

    def toggle_timestamp(self, state):
        """切换时间戳显示"""
        self.show_timestamp = state == Qt.Checked

    def clear_log(self):
        """清除日志"""
        self.log_text.clear()
        self.message_log.clear()  # 同时清除消息记录
        self.log("📋 已清除日志", "INFO")

    def export_messages(self):
        """导出CAN报文到文件"""
        if not self.message_log:
            self.log("⚠️ 无报文可导出", "INFO")
            return

        try:
            # 选择保存文件
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "导出CAN报文",
                f"can_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                "文本文件 (*.txt);;CSV文件 (*.csv);;所有文件 (*.*)",
            )

            if not file_path:
                return

            # 判断导出格式
            is_csv = file_path.lower().endswith(".csv")

            with open(file_path, "w", encoding="utf-8") as f:
                if is_csv:
                    # CSV格式
                    f.write("时间戳,方向,帧类型,CAN_ID,DLC,数据\n")
                    for msg in self.message_log:
                        frame_type = "扩展帧" if msg["type"] == "X" else "标准帧"
                        id_format = (
                            f"0x{msg['id']:08X}"
                            if msg["type"] == "X"
                            else f"0x{msg['id']:03X}"
                        )
                        data_str = " ".join([f"{b:02X}" for b in msg["data"]])
                        f.write(
                            f"{msg['timestamp']},{msg['direction']},{frame_type},{id_format},{msg['dlc']},{data_str}\n"
                        )
                else:
                    # 标准文本格式（类似PCAN-View格式）
                    f.write("=" * 80 + "\n")
                    f.write("CAN总线报文记录\n")
                    f.write(
                        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )
                    f.write(f"总报文数: {len(self.message_log)}\n")
                    f.write("=" * 80 + "\n\n")

                    for msg in self.message_log:
                        direction = "TX →" if msg["direction"] == "TX" else "RX ←"
                        frame_type = "扩展帧(X)" if msg["type"] == "X" else "标准帧(S)"
                        id_format = (
                            f"0x{msg['id']:08X}"
                            if msg["type"] == "X"
                            else f"0x{msg['id']:03X}"
                        )
                        data_str = " ".join([f"{b:02X}" for b in msg["data"]])

                        f.write(
                            f"[{msg['timestamp']}] {direction} {frame_type} ID={id_format} DLC={msg['dlc']} Data=[{data_str}]\n"
                        )

            self.log(f"✅ 报文已导出至: {file_path}", "INFO")

        except Exception as e:
            self.log(f"❌ 导出失败: {e}", "ERROR")

    # ✅ V2.3: 删除UDP接收处理函数
    # def handle_udp_can_frame(self, frame_type, can_id, dlc, data_bytes, timestamp):
    #     """Handle UDP received CAN frames"""
    #     pass
    #
    # def update_udp_stats(self, total_packets, lost_packets):
    #     """Update UDP statistics"""
    #     pass

    def closeEvent(self, event):
        """面板关闭时停止UDP接收器"""
        # ✅ V2.3: 无需停止UDP接收器
        # if hasattr(self, "udp_receiver"):
        #     self.udp_receiver.stop()
        event.accept()
