#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任意波形编辑器 (Arbitrary Waveform Generator Editor)
功能：
  - 手绘256点波形（横坐标0-255，纵坐标0-255）
  - 样条插值平滑曲线
  - 预设波形（正弦、三角、方波、锯齿等）
  - 导出/导入CSV
  - 发送到FPGA
"""

import sys
import struct
import numpy as np
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QSpinBox,
    QDoubleSpinBox,
    QLineEdit,
    QTextEdit,
)
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QFont, QPainter, QPen, QColor, QBrush
import pyqtgraph as pg


class WaveformCanvas(pg.PlotWidget):
    """波形绘制画布"""

    waveform_changed = Signal(np.ndarray)  # 波形数据改变信号

    def __init__(self):
        super().__init__()

        # 设置画布属性
        self.setBackground("w")
        self.setTitle(
            "任意波形编辑器 (左键点击开始→移动鼠标→再次点击结束 | 右键自动缩放)",
            color="k",
            size="10pt",
        )
        self.setLabel("left", "DAC值 (0-255)", units="", color="k")
        self.setLabel("bottom", "采样点", units="", color="k")
        self.setXRange(0, 255)
        self.setYRange(0, 255)
        # 🔧 翻转Y轴：顶部=DAC 0(+4.4V)，底部=DAC 255(-4.4V)，更符合电压直觉
        self.invertY(True)  # 反转Y轴：顶部=0（+4.4V），底部=255（-4.4V）
        self.showGrid(x=True, y=True, alpha=0.3)

        # 添加右侧Y轴显示电压值
        self.getPlotItem().getAxis("right").setLabel("电压", units="V", color="green")
        self.getPlotItem().showAxis("right")
        # 设置右侧Y轴刻度：DAC 0→+4.4V, 128→0V, 255→-4.4V (反向映射)
        # 自定义刻度显示
        right_axis = self.getPlotItem().getAxis("right")

        # 创建刻度映射：DAC值 → 电压（反向）
        # voltage = 4.4 - (dac_value / 255.0) * 8.8

        # 🔧 正确显示：Y轴上方(DAC=0)显示+4.4V，下方(DAC=255)显示-4.4V
        # 右侧刻度按DAC位置标注电压
        voltage_ticks = [
            (0, "+4.4"),  # DAC=0 位置显示 +4.4V
            (32, "+3.0"),  # DAC=32 位置显示约 +3V
            (64, "+1.8"),  # DAC=64 位置
            (96, "+0.5"),  # DAC=96 位置
            (128, "0.0"),  # DAC=128 位置显示 0V
            (160, "-0.5"),  # DAC=160 位置
            (192, "-1.8"),  # DAC=192 位置
            (224, "-3.0"),  # DAC=224 位置
            (255, "-4.4"),  # DAC=255 位置显示 -4.4V
        ]
        right_axis.setTicks([voltage_ticks])

        # 波形数据：256个点，初始值为128（中值）
        self.waveform_data = np.full(256, 128, dtype=np.uint8)

        # 控制点列表（用于某些编辑模式）
        self.control_points = []

        # 🎨 绘图模式相关
        self.drawing = False  # 是否正在绘制（两次点击模式）
        self.last_draw_x = None  # 上一次绘制的x坐标

        # 绘制初始波形
        self.curve = self.plot(pen=pg.mkPen(color="b", width=2))
        self.scatter = self.plot(pen=None, symbol="o", symbolSize=8, symbolBrush="r")

        # 启用鼠标交互
        self.scene().sigMouseClicked.connect(self.on_mouse_clicked)

        # 鼠标追踪（用于自由绘制）
        self.setMouseTracking(True)
        self.proxy = pg.SignalProxy(
            self.scene().sigMouseMoved, rateLimit=60, slot=self.on_mouse_moved
        )

        # 初始化波形显示
        self.curve.setData(np.arange(256), self.waveform_data)
        self.scatter.setData([], [])  # 自由绘制模式不显示控制点

    def on_mouse_clicked(self, event):
        """鼠标点击事件 - 两次点击模式：第一次开始，第二次结束"""
        if event.button() == Qt.LeftButton:
            pos = self.plotItem.vb.mapSceneToView(event.scenePos())
            x, y = int(pos.x()), int(pos.y())

            # 限制范围
            x = np.clip(x, 0, 255)
            y = np.clip(y, 0, 255)

            # 🔧 调试：显示当前状态和对象ID
            print(
                f"🖱️ 鼠标点击：x={x}, y={y}, 当前状态 drawing={self.drawing}, id(self)={id(self)}"
            )

            # 🔧 强制检查状态类型
            if not hasattr(self, "drawing"):
                print("⚠️ 警告：drawing属性丢失，重新初始化")
                self.drawing = False
                self.last_draw_x = None

            if not self.drawing:
                # 🎯 第一次点击：开始绘制
                self.drawing = True
                self.last_draw_x = x
                self.waveform_data[x] = y
                self.update_display()
                print(f"🎨 开始绘制：点击位置 x={x}, y={y}, drawing={self.drawing}")
                # 🔧 设置标记，防止状态被意外重置
                self._last_drawing_state = True
            else:
                # 🎯 第二次点击：结束绘制，填充剩余点
                print(f"✅ 准备结束绘制：last_draw_x={self.last_draw_x}")

                # 填充从当前位置到最后一个点（使用最后的Y值）
                if self.last_draw_x is not None and self.last_draw_x < 255:
                    last_y = self.waveform_data[self.last_draw_x]
                    for xi in range(self.last_draw_x + 1, 256):
                        self.waveform_data[xi] = last_y

                self.drawing = False
                self.last_draw_x = None
                self.update_display()
                print(f"✅ 结束绘制：点击位置 x={x}, y={y}, drawing={self.drawing}")
                # 🔧 设置标记
                self._last_drawing_state = False

        elif event.button() == Qt.RightButton:
            # 🔍 右键：自动缩放视图
            self.autoRange()

    def on_mouse_moved(self, evt):
        """鼠标移动事件 - 只在绘制模式下（两次点击之间）响应"""
        # 🔧 调试：检测状态异常变化
        if hasattr(self, "_last_drawing_state"):
            if self._last_drawing_state != self.drawing:
                print(
                    f"⚠️ 警告：drawing状态意外改变！{self._last_drawing_state} → {self.drawing}"
                )
        self._last_drawing_state = self.drawing

        if self.drawing:
            # evt是SignalProxy传来的，需要解包
            pos = evt[0]

            # 转换坐标
            view_pos = self.plotItem.vb.mapSceneToView(pos)
            x, y = int(view_pos.x()), int(view_pos.y())

            # 限制范围
            x = np.clip(x, 0, 255)
            y = np.clip(y, 0, 255)

            # 🎨 只向右绘制：只处理x >= last_draw_x的情况
            if self.last_draw_x is not None and x > self.last_draw_x:
                x_start = self.last_draw_x
                x_end = x

                # 线性插值填充中间点
                y_start = self.waveform_data[self.last_draw_x]
                for xi in range(x_start, x_end + 1):
                    # 线性插值
                    t = (xi - x_start) / (x_end - x_start) if x_end > x_start else 0
                    yi = int(y_start + t * (y - y_start))
                    self.waveform_data[xi] = np.clip(yi, 0, 255)

                self.last_draw_x = x
                self.update_display()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件 - 不需要特殊处理"""
        super().mouseReleaseEvent(event)

    def update_display(self):
        """更新波形显示（自由绘制模式）"""
        # 直接显示波形数据
        self.curve.setData(np.arange(256), self.waveform_data)

        # 不显示控制点（自由绘制模式）
        self.scatter.setData([], [])

        # 发射信号
        self.waveform_changed.emit(self.waveform_data)

    def update_waveform(self):
        """根据控制点更新波形（兼容旧模式，但主要用于预设波形）"""
        if len(self.control_points) < 2:
            # 如果没有控制点，直接显示当前数据
            self.update_display()
            return

        # 提取控制点坐标
        xs = np.array([p[0] for p in self.control_points])
        ys = np.array([p[1] for p in self.control_points])

        # 三次样条插值
        try:
            from scipy.interpolate import CubicSpline, interp1d

            # 确保起点和终点
            if xs[0] != 0:
                xs = np.insert(xs, 0, 0)
                ys = np.insert(ys, 0, ys[0])
            if xs[-1] != 255:
                xs = np.append(xs, 255)
                ys = np.append(ys, ys[-1])

            # 三次样条插值
            cs = CubicSpline(xs, ys, bc_type="clamped")
            x_new = np.arange(256)
            y_new = cs(x_new)

            # 限制范围并转换为uint8
            y_new = np.clip(y_new, 0, 255).astype(np.uint8)
            self.waveform_data = y_new

        except Exception as e:
            print(f"插值错误（需安装scipy获得更好效果）: {e}")
            # 回退到线性插值（纯numpy实现）
            x_new = np.arange(256)
            if len(xs) > 1:
                y_new = np.interp(x_new, xs, ys)
            else:
                y_new = np.full(256, ys[0] if len(ys) > 0 else 128)
            y_new = np.clip(y_new, 0, 255).astype(np.uint8)
            self.waveform_data = y_new

        # 更新显示
        self.update_display()

    def set_waveform(self, data):
        """设置波形数据（256个点）"""
        if len(data) != 256:
            raise ValueError("波形数据必须是256个点")

        self.waveform_data = np.array(data, dtype=np.uint8)

        # 自动生成控制点（每16个点采样一个）
        self.control_points = [(i, int(data[i])) for i in range(0, 256, 16)]

        # 重置绘制状态（修复清除画布后无法绘制的bug）
        self.drawing = False
        self.last_draw_x = None

        self.update_waveform()

    def get_waveform(self):
        """获取当前波形数据"""
        return self.waveform_data.copy()

    def clear(self):
        """清空波形（设为中值）- 恢复到初始状态 - 覆盖父类的clear()方法"""
        print("=" * 60)
        print("🧹 自定义清空画布函数被调用！")
        print("=" * 60)

        # 🔧 不调用父类的 clear()，因为那会删除所有绘图项（包括curve和scatter）
        # super().clear()  # ❌ 不要调用这个！会删除 self.curve 和 self.scatter

        # 🔧 强制结束当前绘制（如果正在绘制中）
        if self.drawing:
            print("⚠️ 检测到正在绘制，强制结束绘制状态")

        # 🔧 完全重置状态
        self.drawing = False
        self.last_draw_x = None

        # 使用reset_drawing_state确保完全重置
        self.reset_drawing_state()

        # 🔧 修复：确保显示更新
        self.update_display()

        # 🔧 强制处理待处理的事件
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

        # 可以添加额外的用户反馈
        print("✓ 画布已清空，恢复到初始状态（128中值线）")
        # 🔧 额外确认状态
        print(f"✓ 当前绘制状态: drawing={self.drawing}, last_draw_x={self.last_draw_x}")
        print(f"✓ 事件已刷新，可以重新绘制了")
        print("=" * 60)

    def reset_drawing_state(self):
        """重置绘制状态（修复重复打开时无法绘制的bug）- 完全恢复到初始状态"""
        # 🔧 强制结束当前绘制状态
        self.drawing = False
        self.last_draw_x = None
        self.control_points = []

        # 恢复初始波形（128中值线作为引导）
        self.waveform_data = np.full(256, 128, dtype=np.uint8)

        # 确保画布正确显示初始状态
        self.curve.setData(np.arange(256), self.waveform_data)
        self.scatter.setData([], [])

        # 发射信号通知波形已重置
        self.waveform_changed.emit(self.waveform_data)

    def clear_canvas(self):
        """清空画布的公共方法 - 供按钮调用"""
        print("=" * 60)
        print("🧹 清空画布按钮被点击！")
        print("=" * 60)
        self.reset_drawing_state()
        print("✅ 画布已清空！现在可以重新绘制了！")
        print("=" * 60)


class AWGEditor(QMainWindow):
    """任意波形编辑器主窗口"""

    # 信号：发送波形到FPGA
    send_waveform = Signal(str, np.ndarray)  # (channel, waveform_data)

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("任意波形编辑器 (AWG Editor) - 支持自定义t范围")
        # 窗口尺寸设置: setGeometry(x位置, y位置, 宽度, 高度)
        # 原始推荐尺寸: 1100x700 适合编辑器的完整功能显示
        self.setGeometry(100, 100, 1100, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 画布
        self.canvas = WaveformCanvas()
        self.canvas.waveform_changed.connect(self.on_waveform_changed)
        self.canvas.setTitle(
            "波形预览 (点击开始绘制 | 再次点击结束)", color="k", size="10pt"
        )
        main_layout.addWidget(self.canvas)

        # 添加状态栏
        self.statusBar().showMessage(
            "💡 手绘：第一次点击开始，拖动绘制，第二次点击结束 | 🎯 数学表达式：输入公式如 sin(2*pi*t) | 🔧 调节 t 范围截取函数的任意部分"
        )

        # 控制面板
        control_group = QGroupBox("快速预设波形")
        control_layout = QVBoxLayout()

        # 第一行：预设波形选择
        preset_row1 = QHBoxLayout()
        preset_row1.addWidget(QLabel("预设波形:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(
            [
                "正弦波",
                "方波",
                "三角波",
                "锯齿波",
                "反锯齿波",
                "脉冲波",
                "随机噪声",
                "DC(中值)",
            ]
        )
        self.preset_combo.currentIndexChanged.connect(self.load_preset)
        preset_row1.addWidget(self.preset_combo)

        # 采样点数调整（用于生成预设波形的周期数）
        preset_row1.addWidget(QLabel("周期数:"))
        self.period_spin = QSpinBox()
        self.period_spin.setRange(1, 16)
        self.period_spin.setValue(1)
        self.period_spin.valueChanged.connect(
            lambda: self.load_preset(self.preset_combo.currentIndex())
        )
        preset_row1.addWidget(self.period_spin)

        load_preset_btn = QPushButton("加载预设")
        load_preset_btn.clicked.connect(
            lambda: self.load_preset(self.preset_combo.currentIndex())
        )
        preset_row1.addWidget(load_preset_btn)
        preset_row1.addStretch()
        control_layout.addLayout(preset_row1)

        # 第二行：文件操作
        file_row = QHBoxLayout()
        import_btn = QPushButton("导入CSV")
        import_btn.clicked.connect(self.import_csv)
        file_row.addWidget(import_btn)

        export_btn = QPushButton("导出CSV")
        export_btn.clicked.connect(self.export_csv)
        file_row.addWidget(export_btn)

        clear_btn = QPushButton("清空画布")
        clear_btn.clicked.connect(
            self.canvas.clear_canvas
        )  # 🔧 使用自定义方法而不是父类的clear()
        file_row.addWidget(clear_btn)
        file_row.addStretch()
        control_layout.addLayout(file_row)

        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)

        # 数学表达式输入面板（推荐使用，替代手绘）
        math_group = QGroupBox("📐 数学表达式生成（推荐使用）")
        math_layout = QVBoxLayout()

        # t范围调节行（新增）
        t_range_layout = QHBoxLayout()
        t_range_layout.addWidget(QLabel("t 范围:"))

        self.t_start_spin = QDoubleSpinBox()
        self.t_start_spin.setRange(-1000, 1000)
        self.t_start_spin.setValue(0)
        self.t_start_spin.setDecimals(3)
        self.t_start_spin.setSingleStep(0.1)
        self.t_start_spin.setPrefix("从 ")
        self.t_start_spin.setMinimumWidth(100)
        t_range_layout.addWidget(self.t_start_spin)

        t_range_layout.addWidget(QLabel("到"))

        self.t_end_spin = QDoubleSpinBox()
        self.t_end_spin.setRange(-1000, 1000)
        self.t_end_spin.setValue(1)
        self.t_end_spin.setDecimals(3)
        self.t_end_spin.setSingleStep(0.1)
        self.t_end_spin.setMinimumWidth(100)
        t_range_layout.addWidget(self.t_end_spin)

        # 快捷按钮：常用周期范围
        t_range_layout.addWidget(QLabel("  快捷:"))

        period_presets = [
            ("0-1", 0, 1),
            ("0-2π", 0, 2 * np.pi),
            ("0-4π", 0, 4 * np.pi),
            ("-π~π", -np.pi, np.pi),
        ]
        for text, start, end in period_presets:
            btn = QPushButton(text)
            btn.setMaximumWidth(60)
            btn.clicked.connect(lambda checked, s=start, e=end: self.set_t_range(s, e))
            t_range_layout.addWidget(btn)

        t_range_layout.addStretch()
        math_layout.addLayout(t_range_layout)

        # 表达式输入
        expr_input_layout = QHBoxLayout()
        expr_input_layout.addWidget(QLabel("y = "))
        self.expr_input = QLineEdit()
        self.expr_input.setPlaceholderText(
            "例如: 4sin(2*pi*t)  或  3exp(-5*t)sin(8*pi*t)  (自动识别乘法)"
        )
        self.expr_input.setMinimumWidth(450)
        self.expr_input.setFont(QFont("Consolas", 10))
        expr_input_layout.addWidget(self.expr_input)

        generate_btn = QPushButton("生成波形")
        generate_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; font-weight: bold; padding: 8px; font-size: 12pt; }"
        )
        generate_btn.clicked.connect(self.generate_from_expression)
        expr_input_layout.addWidget(generate_btn)
        math_layout.addLayout(expr_input_layout)

        # 快速示例按钮（会自动设置合适的t范围）
        examples_layout = QHBoxLayout()
        examples_layout.addWidget(QLabel("快速示例:"))
        examples = [
            ("正弦波", "4sin(2*pi*t)", 0, 1),  # t=[0,1] 一个周期
            ("斜坡", "4.4-8.8*t", 0, 1),  # t=[0,1] 线性下降
            ("衰减振荡", "3exp(-5*t)sin(8*pi*t)", 0, 1),  # t=[0,1]
            ("调幅波", "3sin(20*pi*t)(1+0.5sin(2*pi*t))", 0, 1),  # t=[0,1]
        ]
        for name, expr, t_start, t_end in examples:
            btn = QPushButton(name)
            btn.clicked.connect(
                lambda checked, e=expr, ts=t_start, te=t_end: self.load_example_with_range(
                    e, ts, te
                )
            )
            examples_layout.addWidget(btn)
        examples_layout.addStretch()
        math_layout.addLayout(examples_layout)

        # 帮助文本（更新说明）
        help_text = QTextEdit()
        help_text.setReadOnly(True)
        help_text.setMaximumHeight(100)
        help_text.setStyleSheet("background-color: #f0f0f0; font-size: 9pt;")
        help_text.setHtml(
            """
        <b>支持的函数:</b> sin, cos, tan, exp, log, sqrt, abs, pow &nbsp;&nbsp;
        <b>变量:</b> t (用户自定义范围), x (0到255索引) &nbsp;&nbsp;<br>
        <b>💡 电压模式:</b> 直接输入幅值表达式（单位: V），范围自动映射到 -4.4V ~ +4.4V<br>
        <b>🎯 t范围说明:</b><br>
        &nbsp;&nbsp;• <b>周期函数</b>（如sin、cos）：推荐使用 t=[0, 2π] 显示1个完整周期，或 t=[0, 4π] 显示2个周期<br>
        &nbsp;&nbsp;• <b>非周期函数</b>（如斜坡、指数）：根据需要调节 t 范围，截取感兴趣的部分<br>
        <b>🔧 示例:</b> <code>4sin(t)</code> + t=[0, 2π] → 一个完整正弦波
        """
        )
        math_layout.addWidget(help_text)

        math_group.setLayout(math_layout)
        main_layout.addWidget(math_group)

        # 发送面板
        send_group = QGroupBox("发送到FPGA")
        send_layout = QHBoxLayout()

        send_layout.addWidget(QLabel("目标通道:"))
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(["通道A", "通道B"])
        send_layout.addWidget(self.channel_combo)

        send_btn = QPushButton("发送波形")
        send_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; }"
        )
        send_btn.clicked.connect(self.send_to_fpga)
        send_layout.addWidget(send_btn)

        send_layout.addStretch()

        # 波形统计
        self.stats_label = QLabel("最小值: 0  最大值: 255  平均值: 128")
        send_layout.addWidget(self.stats_label)

        send_group.setLayout(send_layout)
        main_layout.addWidget(send_group)

    def on_waveform_changed(self, waveform):
        """波形改变时更新统计信息"""
        min_val = np.min(waveform)
        max_val = np.max(waveform)
        avg_val = np.mean(waveform)

        # 转换DAC值到电压（反向映射：DAC 0→+4.4V, 255→-4.4V）
        # voltage = 4.4 - (dac_value / 255.0) * 8.8
        min_voltage = 4.4 - (min_val / 255.0) * 8.8
        max_voltage = 4.4 - (max_val / 255.0) * 8.8
        avg_voltage = 4.4 - (avg_val / 255.0) * 8.8

        # 注意：min_val对应max_voltage，max_val对应min_voltage（因为反向）
        self.stats_label.setText(
            f"DAC范围: [{min_val}, {max_val}] → 电压范围: [{max_voltage:+.2f}V, {min_voltage:+.2f}V]  "
            f"平均: DAC={avg_val:.1f} ({avg_voltage:+.2f}V)"
        )

    def load_preset(self, index):
        """加载预设波形"""
        n_periods = self.period_spin.value()
        x = np.linspace(0, 2 * np.pi * n_periods, 256)

        if index == 0:  # 正弦波
            y = 128 + 127 * np.sin(x)
        elif index == 1:  # 方波
            y = np.where(np.sin(x) >= 0, 255, 0)
        elif index == 2:  # 三角波
            y = 128 + 127 * (2 * np.abs((x % (2 * np.pi)) / (2 * np.pi) - 0.5) - 0.5)
        elif index == 3:  # 锯齿波
            y = 255 * ((x % (2 * np.pi)) / (2 * np.pi))
        elif index == 4:  # 反锯齿波
            y = 255 * (1 - (x % (2 * np.pi)) / (2 * np.pi))
        elif index == 5:  # 脉冲波（10%占空比）
            phase = (x % (2 * np.pi)) / (2 * np.pi)
            y = np.where(phase < 0.1, 255, 0)
        elif index == 6:  # 随机噪声
            y = np.random.randint(0, 256, 256)
        elif index == 7:  # DC中值
            y = np.full(256, 128)
        else:
            return

        y = np.clip(y, 0, 255).astype(np.uint8)
        self.canvas.set_waveform(y)

    def import_csv(self):
        """从CSV导入波形"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入波形数据", "", "CSV文件 (*.csv);;所有文件 (*)"
        )

        if not file_path:
            return

        try:
            data = np.loadtxt(file_path, delimiter=",", dtype=np.uint8)

            if len(data) != 256:
                QMessageBox.warning(
                    self, "错误", f"CSV文件必须包含256个点，当前有{len(data)}个点"
                )
                return

            self.canvas.set_waveform(data)
            QMessageBox.information(self, "成功", "波形导入成功！")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入失败: {e}")

    def export_csv(self):
        """导出波形到CSV"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出波形数据", "waveform.csv", "CSV文件 (*.csv);;所有文件 (*)"
        )

        if not file_path:
            return

        try:
            waveform = self.canvas.get_waveform()
            np.savetxt(file_path, waveform, fmt="%d", delimiter=",")
            QMessageBox.information(self, "成功", "波形导出成功！")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败: {e}")

    def send_to_fpga(self):
        """发送波形到FPGA"""
        channel = "A" if self.channel_combo.currentIndex() == 0 else "B"
        waveform = self.canvas.get_waveform()

        # 发射信号
        self.send_waveform.emit(channel, waveform)

        QMessageBox.information(
            self,
            "发送完成",
            f"已发送256字节波形数据到通道{channel}\n"
            f"请在主界面选择波形类型为'任意波形'以查看效果",
        )

    def set_and_generate_expression(self, expression):
        """设置表达式并生成波形（用于快速示例）"""
        self.expr_input.setText(expression)
        self.generate_from_expression()

    def load_example_with_range(self, expression, t_start, t_end):
        """加载示例并设置t范围"""
        self.expr_input.setText(expression)
        self.t_start_spin.setValue(t_start)
        self.t_end_spin.setValue(t_end)
        self.generate_from_expression()

    def showEvent(self, event):
        """窗口显示时重置画布状态（修复重复打开时无法绘制的bug）"""
        super().showEvent(event)
        # 完全重置画布到初始状态（包括0线引导）
        self.canvas.reset_drawing_state()
        # 确保鼠标追踪启用（修复重复打开后无法绘制的关键）
        self.canvas.setMouseTracking(True)
        # 确保状态栏提示正确
        self.statusBar().showMessage(
            "💡 手绘模式已就绪！第一次点击开始绘制，拖动鼠标，第二次点击结束 | 🎯 或使用数学表达式生成波形",
            5000,  # 显示5秒
        )

    def closeEvent(self, event):
        """关闭时不销毁窗口：隐藏并重置绘制状态，保留对象用于再次 show()."""
        # 隐藏窗口而不是销毁，避免外部持有引用后再次 show() 无效的问题
        self.canvas.reset_drawing_state()
        self.hide()
        # 忽略默认关闭行为，防止对象被删除
        event.ignore()

    def set_t_range(self, start, end):
        """设置t范围（用于快捷按钮）"""
        self.t_start_spin.setValue(start)
        self.t_end_spin.setValue(end)
        # 如果表达式不为空，自动重新生成
        if self.expr_input.text().strip():
            self.generate_from_expression()

    def preprocess_expression(self, expr):
        """
        预处理数学表达式，支持隐式乘法
        例如：2sin(t) → 2*sin(t)
              3t → 3*t
              2pi → 2*pi
              (2+3)(4-1) → (2+3)*(4-1)
        """
        import re

        # 去除空格
        expr = expr.replace(" ", "")

        # 1. 数字后面跟着字母、左括号或函数名 → 插入 *
        # 例如: 2sin(t) → 2*sin(t), 3t → 3*t, 2pi → 2*pi, 4(x+1) → 4*(x+1)
        expr = re.sub(r"(\d)([a-zA-Z(])", r"\1*\2", expr)

        # 2. 右括号后面跟着数字、字母或左括号 → 插入 *
        # 例如: (2+3)t → (2+3)*t, (x+1)(y-1) → (x+1)*(y-1), (2)3 → (2)*3
        expr = re.sub(r"(\))([0-9a-zA-Z(])", r"\1*\2", expr)

        # 3. 字母后面跟着左括号（但不是函数名）→ 插入 *
        # 例如: t(x+1) → t*(x+1)
        # 但要避免: sin(t), cos(t), exp(t) 等函数调用
        # 策略：单个字母后跟左括号 → 插入*
        expr = re.sub(
            r"([a-z])(\()",
            lambda m: (
                m.group(1) + "*("
                if m.group(1) in ["t", "x", "e"] and len(m.group(1)) == 1
                else m.group(0)
            ),
            expr,
        )

        # 4. 处理 pi, e 等常数前的系数
        # 例如: 2pi → 2*pi (已在第1步处理)

        # 5. 处理连续字母（如果不是函数名）
        # 例如: tx → t*x
        # 但保留函数名: sin, cos, tan, exp, log, sqrt, abs, pow
        known_functions = [
            "sin",
            "cos",
            "tan",
            "exp",
            "log",
            "log10",
            "sqrt",
            "abs",
            "pow",
            "pi",
        ]
        # 这个比较复杂，暂时不处理，因为可能误伤函数名

        return expr

    def generate_from_expression(self):
        """从数学表达式生成波形（支持电压值输入和隐式乘法）"""
        expression = self.expr_input.text().strip()

        if not expression:
            QMessageBox.warning(self, "错误", "请输入数学表达式")
            return

        try:
            # 🔧 预处理表达式：支持隐式乘法
            original_expr = expression
            expression = self.preprocess_expression(expression)

            # 如果表达式有变化，显示预处理后的结果
            if expression != original_expr:
                self.statusBar().showMessage(
                    f"📝 表达式预处理: {original_expr} → {expression}", 3000
                )

            # 🎯 获取用户自定义的 t 范围
            t_start = self.t_start_spin.value()
            t_end = self.t_end_spin.value()

            # 验证范围有效性
            if t_start >= t_end:
                QMessageBox.warning(
                    self,
                    "错误",
                    f"t 范围无效：起始值({t_start})必须小于结束值({t_end})",
                )
                return

            # 预处理表达式（替换常用函数）
            # 支持 t (用户自定义范围) 和 x (0 到 255)
            t = np.linspace(t_start, t_end, 256)  # 🔧 使用用户自定义范围
            x = np.arange(256)  # 采样点索引：0到255

            # 安全的命名空间（只允许数学函数）
            safe_dict = {
                "sin": np.sin,
                "cos": np.cos,
                "tan": np.tan,
                "exp": np.exp,
                "log": np.log,
                "log10": np.log10,
                "sqrt": np.sqrt,
                "abs": np.abs,
                "pow": np.power,
                "pi": np.pi,
                "e": np.e,
                "t": t,
                "x": x,
                "np": np,
            }

            # 评估表达式
            y = eval(expression, {"__builtins__": {}}, safe_dict)

            # 确保是numpy数组
            if np.isscalar(y):
                y = np.full(256, y)
            else:
                y = np.array(y)

            # 检查长度
            if len(y) != 256:
                raise ValueError(f"表达式结果长度为{len(y)}，必须是256")

            # 💡 电压模式：直接映射到 -4.4V ~ +4.4V（反向映射）
            # 实际硬件映射关系：
            # DAC 0   → +4.4V (最大值)
            # DAC 128 → 0V (中间值)
            # DAC 255 → -4.4V (最小值)
            # 转换公式: DAC_value = (4.4 - voltage) / 8.8 * 255
            V_MIN = -4.4  # 最小电压
            V_MAX = 4.4  # 最大电压

            # 限制电压范围
            y_clipped = np.clip(y, V_MIN, V_MAX)

            # 转换到 0-255 DAC值（反向映射）
            # voltage = +4.4V → DAC = 0
            # voltage = 0V    → DAC = 127.5 ≈ 128
            # voltage = -4.4V → DAC = 255
            y_normalized = ((V_MAX - y_clipped) / (V_MAX - V_MIN) * 255).astype(
                np.uint8
            )

            # 设置波形（不再使用手绘控制点）
            self.canvas.waveform_data = y_normalized
            # 预览显示原始顺序（和表达式一致）
            self.canvas.curve.setData(np.arange(256), y_normalized)
            # 清空控制点显示（数学生成的波形不需要控制点）
            self.canvas.scatter.setData([], [])
            self.canvas.control_points = []
            self.canvas.waveform_changed.emit(y_normalized)

            # 计算实际电压范围
            y_min = np.min(y)
            y_max = np.max(y)

            # 检查是否超出范围
            clipped_warning = ""
            if y_min < V_MIN or y_max > V_MAX:
                clipped_warning = f" ⚠ 部分值超出±4.4V已被裁剪"

            # 显示详细信息（包括t范围和前后几个点用于调试）
            t_range_info = f"t=[{t_start:.3f}, {t_end:.3f}]"
            debug_info = (
                f"前3点: t=[{t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f}], 电压=[{y[0]:.3f}, {y[1]:.3f}, {y[2]:.3f}]V → DAC=[{y_normalized[0]}, {y_normalized[1]}, {y_normalized[2]}] | "
                f"后3点: t=[{t[-3]:.3f}, {t[-2]:.3f}, {t[-1]:.3f}], 电压=[{y[-3]:.3f}, {y[-2]:.3f}, {y[-1]:.3f}]V → DAC=[{y_normalized[-3]}, {y_normalized[-2]}, {y_normalized[-1]}]"
            )

            # 显示信息（电压模式 + t范围）
            self.statusBar().showMessage(
                f"✓ 生成成功 | 表达式: {original_expr} | {t_range_info} | 电压范围: [{y_min:.3f}V, {y_max:.3f}V] → DAC[{np.min(y_normalized)}, {np.max(y_normalized)}] | {debug_info}{clipped_warning}",
                20000,  # 延长显示时间到20秒
            )

        except SyntaxError as e:
            QMessageBox.critical(self, "语法错误", f"表达式语法错误:\n{e}")
        except NameError as e:
            QMessageBox.critical(
                self,
                "变量错误",
                f"未知的变量或函数:\n{e}\n\n仅支持: sin, cos, tan, exp, log, sqrt, abs, pow, t, x",
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成波形失败:\n{e}")


def main():
    """独立运行测试"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    editor = AWGEditor()
    editor.show()

    # 测试信号连接
    def on_send(channel, waveform):
        print(f"[测试] 发送到通道{channel}:")
        print(f"  数据长度: {len(waveform)}")
        print(f"  前10个点: {waveform[:10]}")
        print(f"  最小值: {np.min(waveform)}, 最大值: {np.max(waveform)}")

    editor.send_waveform.connect(on_send)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
