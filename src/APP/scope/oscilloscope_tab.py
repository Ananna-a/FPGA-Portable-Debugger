#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双通道示波器模块 V3.3 (FFT频谱增强版)
功能：
  - 双通道波形显示(CH1/CH2预留)
  - FPGA硬件频率计自动测量
  - 自适应采样参数计算(根据频率+目标周期数)
  - 实时FFT频谱分析（✅已实现）
  - 参数测量（Vpp、Vrms、最大最小值）

模式说明（V3.3统一命名）：
  - Buffer连续模式：FPGA连续采集，通过CDC实时传输，适合实时监控（✅已实现）
  - Buffer单次模式：FPGA单次采集到DDR3，通过CDC一次性传输（⏳预留接口）
  - Stream触发模式：与Buffer连续模式功能完全相同（✅已实现）

FFT功能特性：
  - 汉宁窗函数处理，减少频谱泄漏
  - 对数刻度X轴，便于观察宽频信号
  - 自动调整Y轴动态范围（最大值-80dB）
  - 实时更新，无需停止采集

作者：AI辅助开发
日期：2025-11-05
"""

import struct
import numpy as np
import pyqtgraph as pg
from core.ethernet_receiver import EthernetReceiver
from utils.ring_buffer import RingBuffer  # 🔥 V7.1: 高性能环形缓冲区
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QTextEdit,
    QTabWidget,
    QSizePolicy,
    QFrame,
    QMenu,
)
from PySide6.QtCore import Slot, QTimer, Qt
from PySide6.QtGui import QFont

# PyQtGraph配置
pg.setConfigOption("background", "#1a1a1a")  # 暗色背景
pg.setConfigOption("foreground", "#d0d0d0")  # 浅色前景


# ============================================================================
# 自适应采样参数计算器
# ============================================================================
class AdaptiveSamplingCalculator:
    """
    自适应采样参数计算
    根据FPGA测得的频率 + 用户设置的目标周期数，自动计算最优采样率和深度

    核心算法：
    1. 每周期至少20点 (保证波形质量)
    2. 采样率 = 频率 × 每周期点数
    3. 采样深度 = 每周期点数 × 目标周期数
    """

    # 🔥 V8.7.59: Buffer模式最大采样率限制为25MSPS（硬件瓶颈）
    # 理论分析：50MHz系统时钟 × 16位 = 100MB/s理论带宽
    #          考虑DDR3突发传输效率(88%)、FIFO握手(90%)、刷新周期(95%)
    #          实际可用带宽约75MB/s，而50MSPS需要100MB/s → 不可行
    #          25MSPS仅需50MB/s，有1.5倍裕量 → 稳定可靠
    SAMPLE_RATES = [
        # 50000000,  # 50 MSPS - ❌ 硬件瓶颈：DDR3写入带宽不足(需要100MB/s，实际仅75MB/s)
        25000000,  # 25 MSPS    (div=2) - ✅ Buffer模式最大安全采样率
        10000000,  # 10 MSPS    (div=5)
        5000000,  # 5 MSPS     (div=10)
        2500000,  # 2.5 MSPS   (div=20)
        1000000,  # 1 MSPS     (div=50)
        500000,  # 500 KSPS   (div=100)
        250000,  # 250 KSPS   (div=200)
        100000,  # 100 KSPS   (div=500)
        50000,  # 50 KSPS    (div=1000)
        25000,  # 25 KSPS    (div=2000)
        10000,  # 10 KSPS    (div=5000)
        5000,  # 5 KSPS     (div=10000)
        2500,  # 2.5 KSPS   (div=20000)
        1000,  # 1 KSPS     (div=50000)
        500,  # 500 SPS    (div=100000)
        100,  # 100 SPS    (div=500000)
        50,  # 50 SPS     (div=1000000)
    ]

    # 🔥 V8.7.24: 可用采样深度 (点) - 统一为二进制(1024倍数)
    SAMPLE_DEPTHS = [
        1024,  # 1K
        2048,  # 2K
        4096,  # 4K
        8192,  # 8K
        10240,  # 10K
        20480,  # 20K
        51200,  # 50K
        102400,  # 100K
        204800,  # 200K
        512000,  # 500K
        1048576,  # 1M
    ]

    @staticmethod
    def calculate(
        signal_freq,
        target_periods=6,
        min_points_per_period=20,  # 🔥 从50降到20，支持更高频率
        max_points_per_period=70,
        min_depth=4096,
    ):
        """
        计算最优采样参数 (V4.2 - 🔥优化高频支持，降低每周期点数要求)

        FPGA硬件配置：
            - 基准时钟: 50MHz
            - RESAMPLE_RATIO: 1 (无降采样)
            - 基础采样率: 50MHz
            - div_set范围: 2~4294967295 (Buffer模式最小div=2，即25MSPS)
            - 实际采样率: 50MHz / div_set
            - ⚠️ Buffer模式限制: 最大25MSPS（DDR3性能瓶颈）

        🎯 支持频率范围（优化后）：
            - 低频: 0.1Hz ~ 1kHz     (高精度，30点/周期)
            - 中频: 1kHz ~ 1MHz      (标准质量，30点/周期)
            - 高频: 1MHz ~ 2.5MHz    (降级质量，20-50点/周期) ✅
            - 超高频: 2.5MHz ~ 12.5MHz (最低质量，4-20点/周期) ✅
            - 极限: >12.5MHz         (不支持，点数<4)

        🔥 V4.2优化策略：
            - 目标点数: 30点/周期 (从50降低，支持更高频)
            - 最低要求: 4点/周期 (能看到基本波形)
            - 固定深度: 4096点 (FFT友好)
            - 自动降级: 高频时减少点数保证可用

        频率支持对照表：
            1MHz   → 30点/周期 ✅ 优秀
            2MHz   → 25点/周期 ✅ 良好
            3MHz   → 16点/周期 ✅ 可用
            5MHz   → 10点/周期 ⚠️ 基本
            10MHz  → 5点/周期  ⚠️ 最低
            12.5MHz→ 4点/周期  ⚠️ 极限

        Args:
            signal_freq: FPGA测得的信号频率(Hz)
            target_periods: 目标显示周期数 (默认6)
            min_points_per_period: 每周期最少点数 (默认20) 🔥
            max_points_per_period: 每周期最多点数 (默认70)
            min_depth: 最小采样深度 (默认4096)

        Returns:
            dict: {
                'sample_rate': 采样率(Hz),
                'sample_depth': 4096 (固定),
                'div_factor': 分频系数(发送给FPGA的div_set值),
                'points_per_period': 实际每周期点数,
                'actual_periods': 实际采集周期数,
                'display_periods': 建议显示周期数,
                'warning': 警告信息（可选）
            } 或 None(无法计算)
        """
        if signal_freq is None or signal_freq <= 0:
            return None

        # FPGA硬件常量
        BASE_FREQ = 50000000  # 50MHz基准时钟
        MAX_SAMPLE_RATE = 25000000  # 🔥 V8.7.59: Buffer模式最大25MSPS（DDR3带宽限制）
        FIXED_DEPTH = 4096  # 固定采样深度
        TARGET_POINTS = 30  # 🔥 目标每周期点数：从50降到30
        MIN_POINTS = 4  # 最少每周期点数

        # 频率范围限制
        MAX_FREQ = BASE_FREQ / MIN_POINTS  # 12.5MHz (50MHz / 4点)
        MIN_FREQ = 0.1  # 0.1Hz

        # 检查频率范围
        if signal_freq > MAX_FREQ:
            return {
                "sample_rate": BASE_FREQ,
                "sample_depth": FIXED_DEPTH,
                "div_factor": 1,
                "points_per_period": int(BASE_FREQ / signal_freq),
                "actual_periods": FIXED_DEPTH * signal_freq / BASE_FREQ,
                "display_periods": 6,
                "warning": f"⚠️ 信号频率过高（{signal_freq/1e6:.2f}MHz > 12.5MHz），每周期<4点，建议降低频率",
            }
        if signal_freq < MIN_FREQ:
            return None  # 低于0.1Hz不支持

        # 🎯 核心算法：自适应采样率计算
        # 步骤1：计算理想采样率（频率 × 目标点数）
        ideal_sample_rate = signal_freq * TARGET_POINTS

        # 步骤2：判断是否需要降级
        if ideal_sample_rate <= BASE_FREQ:
            # 常规情况：硬件能力足够，使用理想采样率
            sample_rate = ideal_sample_rate
            actual_points_per_period = TARGET_POINTS
            warning = None
        else:
            # 高频情况：硬件能力不足，使用最大采样率并降低点数
            sample_rate = BASE_FREQ
            actual_points_per_period = BASE_FREQ / signal_freq

            # 安全检查：确保点数不低于最小值
            if actual_points_per_period < MIN_POINTS:
                # 🔥 即使点数不足，也返回结果（带警告）
                warning = f"⚠️ 信号频率极高（{signal_freq/1e6:.2f}MHz），每周期仅{actual_points_per_period:.1f}点，波形质量很差"
            elif actual_points_per_period < 10:
                warning = f"⚠️ 高频信号（{signal_freq/1e6:.2f}MHz），每周期仅{actual_points_per_period:.1f}点，波形细节较少"
            else:
                warning = None

        # 步骤3：计算分频系数
        div_set = int(round(BASE_FREQ / sample_rate))

        # 🔥 V8.7.59: Buffer模式限制最小div=2（最大25MSPS）
        # 原因：DDR3写入带宽约75MB/s，50MSPS需要100MB/s，硬件无法支持
        if div_set < 2:  # 最小div=2 → 最大25MSPS
            div_set = 2
            warning = "⚠️ Buffer模式限制：最大采样率25MSPS（DDR3带宽瓶颈）"
        # 🔥 新增：最大div_factor限制（避免FPGA计数器溢出）
        # 理由：低频信号（10Hz）会产生div=166,667，过大的div可能导致：
        #   1. FPGA计数器超时（采样间隔大于触发超时）
        #   2. DDR3刷新间隔内未有数据写入，导致数据丢失
        #   3. 系统状态机卡死
        # 解决：限制最大div=100,000 （对应50MHz基准 = 500Hz最低采样率）
        MAX_DIV_FACTOR = 100000  # 最大分频系数
        if div_set > MAX_DIV_FACTOR:
            div_set = MAX_DIV_FACTOR
            actual_sample_rate = BASE_FREQ / div_set  # 500Hz
            actual_points_per_period = actual_sample_rate / signal_freq
            if not warning:
                warning = f"⚠️ 低频信号（{signal_freq:.2f}Hz），采样率被限制到{actual_sample_rate:.0f}Hz，每周期{actual_points_per_period:.1f}点"
        elif div_set > 4294967295:
            return None  # div_set超出32位范围

        # 步骤4：计算实际参数（考虑整数除法误差）
        actual_sample_rate = BASE_FREQ / div_set
        final_points_per_period = actual_sample_rate / signal_freq

        # 🔥 V8.7.24: 动态计算采样深度（根据目标周期数）
        target_depth = int(final_points_per_period * target_periods)

        # 🔥 新增：低频信号提高深度下限
        # 原因：低频信号（<100Hz）周期很长，需要更多点才能显示完整
        # 例：10Hz信号，500Hz采样率，50点/周期，6周期=300点 → 太少！
        # 提高到至少2K点，确保低频信号也有足够的采样数据
        if signal_freq < 100:  # 低频信号
            min_depth_for_low_freq = 2048  # 最少2K点
            target_depth = max(target_depth, min_depth_for_low_freq)

        # 对齐到标准深度（二进制1024倍数）
        standard_depths = [1024, 2048, 4096, 8192, 10240, 20480, 51200, 102400]
        sample_depth = min(
            [d for d in standard_depths if d >= target_depth], default=4096  # 默认4K
        )

        # 计算实际周期数
        actual_periods = sample_depth / final_points_per_period

        # 返回完整参数
        return {
            "sample_rate": int(actual_sample_rate),
            "sample_depth": sample_depth,  # 🔥 动态深度而非固定4096
            "div_factor": div_set,
            "points_per_period": int(round(final_points_per_period)),
            "actual_periods": actual_periods,
            "display_periods": target_periods,  # 实际目标周期数
            "warning": warning,
        }


# ============================================================================
# 示波器Tab主类
# ============================================================================


# ============================================================================
# FPGA硬件频率计接口
# ============================================================================
class FPGAFrequencyMeter:
    """
    FPGA硬件频率测量接口
    通过CH340串口发送0x27命令,接收4字节频率值(Hz)
    """

    def __init__(self, serial_manager):
        self.serial_manager = serial_manager
        self.measured_freq = None
        self.waiting_response = False

    def request_frequency(self):
        """发送频率测量请求到FPGA"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            return False

        cmd = bytes([0x27, 0x00])  # 命令0x27 + 预留字节
        try:
            self.serial_manager.cdc_port.write(cmd)
            self.waiting_response = True
            return True
        except:
            return False

    def parse_frequency_response(self, data):
        """
        解析FPGA返回的频率数据
        Args:
            data: 4字节数据 (32位小端序频率值)
        Returns:
            频率值(Hz) 或 None
        """
        if len(data) < 4:
            return None

        try:
            freq = struct.unpack("<I", data[:4])[0]  # 小端序32位无符号整数
            self.measured_freq = freq
            self.waiting_response = False
            return freq
        except:
            return None


# ============================================================================
# FPGA硬件频率计接口
# ============================================================================
class FPGAFrequencyMeter:
    """
    FPGA硬件频率测量接口
    通过CH340串口发送0x27命令,接收4字节频率值(Hz)
    """

    def __init__(self, serial_manager):
        self.serial_manager = serial_manager
        self.measured_freq = None
        self.waiting_response = False

    def request_frequency(self):
        """发送频率测量请求到FPGA"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            return False

        cmd = bytes([0x27, 0x00])  # 命令0x27 + 预留字节
        try:
            self.serial_manager.cdc_port.write(cmd)
            self.waiting_response = True
            return True
        except:
            return False

    def parse_frequency_response(self, data):
        """
        解析FPGA返回的频率数据
        Args:
            data: 4字节数据 (32位小端序频率值)
        Returns:
            频率值(Hz) 或 None
        """
        if len(data) < 4:
            return None

        try:
            freq = struct.unpack("<I", data[:4])[0]  # 小端序32位无符号整数
            self.measured_freq = freq
            self.waiting_response = False
            return freq
        except:
            return None


# ============================================================================
# 示波器Tab主类
# ============================================================================
class OscilloscopeTab(QWidget):
    """双通道示波器界面(自适应采样版)"""

    def __init__(self, serial_manager=None):
        super().__init__()
        self.serial_manager = serial_manager

        # 采集参数
        self.current_mode = "stream"  # 当前模式：stream(流模式), buffer(Buffer模式)
        self.base_sample_rate = 50000000  # 50MSPS (RESAMPLE_RATIO=1)
        self.sample_rate = 50000000
        self.buffer_size = 4096  # 🔥 V8.7.24: 默认4K(二进制)
        self.div_factor = 1

        # 通道配置（硬件级使能）
        self.ch1_enabled = True
        self.ch2_enabled = True  # CH2已启用（双通道ADC）

        # 通道波形可见性状态（UI显示控制）
        self.ch1_visible = True
        self.ch2_visible = True  # 与ch2_enabled保持一致（默认显示双通道）

        # 🔥 V7.1: 使用高性能环形缓冲区替代普通列表
        # V8.6.7: 增加容量到200K（原100K），避免高频数据波形不连续
        # V8.6.20: 进一步增加到500K，提升双通道稳定性（2.5倍容量）
        # 优势：固定内存、线程安全、零拷贝快照
        # 计算：100kHz双通道@1.923MSPS → 961k samples/s → 500K可缓存0.52秒
        self.ch1_buffer = RingBuffer(capacity=500000, dtype=np.float32)
        self.ch2_buffer = RingBuffer(capacity=500000, dtype=np.float32)
        self.max_display_points = 10000  # 显示窗口大小（兼容性保留）

        # FPGA频率测量结果
        self.fpga_measured_freq = None
        self.fpga_measured_freq_ch2 = None  # V2.0：CH2频率
        self.auto_measuring = False  # 标记是否正在自动测频（首次启动时）

        # 🔥 FPGA自动测频相关变量
        # 注意：FPGA每1秒自动测频并发送数据，上位机只需被动接收
        self.last_measured_freq = None  # 上一次测量的频率
        self.freq_change_threshold = (
            0.15  # 频率变化阈值（15%）🔥 提高避免100kHz测频误差
        )
        self.is_reconfiguring = False  # 标记是否正在重新配置（Auto按钮触发）
        self.need_adjust_xaxis = False  # 标记是否需要调整X轴（重新配置后）
        self.fixed_xaxis_range = None  # 固定的X轴范围（微秒），Auto后锁定

        # 🔥 频率显示控制标志
        self.freq_display_locked = False  # 频率显示锁定标志（Buffer单次模式用）

        # 自适应采样参数
        self.target_periods = 6  # 默认显示6个周期
        self.adaptive_params = None  # 计算出的自适应参数

        # 测量参数
        self.ch1_params = {}
        self.ch2_params = {}  # CH2预留

        # 性能优化标志
        self.is_capturing = False
        self.display_update_counter = 0
        self.display_update_interval = 5

        # 🔥 V5.9: 数据更新锁，防止绘图时数据不同步
        self.data_updating = False

        # 🔥 V5.14: UDP包相位跟踪（解决1008字节奇数导致的包边界相位翻转）
        # 每包1008字节 → 504个样本对 → 奇数!下一包起始位置会翻转
        self.udp_phase_offset = 0  # 0=正常相位(偶数索引=CH1), 1=反转相位(奇数索引=CH1)

        # 🔥 新增：智能流重组机制（基于协议头的数据流同步）
        self.residual_data = b""  # 残留数据缓存，处理UDP分包和奇数长度包
        self.swap_channels_enabled = False  # 手动通道交换开关（调试用）

        # FFT功能
        self.fft_enabled = False  # FFT开关
        self.fft_window_type = "hamming"  # 默认窗函数类型（汉明窗旁瓣抑制好）

        # 🔥 V7.4: 软件触发参数（流模式波形稳定的关键！）
        self.trigger_enabled = True  # 触发功能开关（默认开启）
        self.trigger_source = "CH1"  # 触发源："CH1" 或 "CH2"
        self.trigger_level = 0.0  # 触发电平（伏特）
        self.trigger_edge = "rising"  # 触发边沿："rising" 或 "falling"
        self.trigger_holdoff = 100  # 触发抑制点数（防止同一周期多次触发）

        # 🔥 V7.5: 流模式显示策略
        self.stream_display_mode = (
            "auto"  # "auto", "roll"（滚动）, "triggered"（触发刷新）
        )
        self.stream_freq_threshold = 100  # Hz，低于此频率使用滚动模式，高于使用触发模式

        # 🔥 V8.7.9: Buffer模式自动停止功能
        self.buffer_mode_auto_stop = True  # Buffer模式自动停止开关
        self.buffer_expected_packets = 0  # Buffer模式预期接收的UDP包数
        self.buffer_received_packets = 0  # Buffer模式已接收的UDP包数

        # 以太网接收器（用于UDP传输）
        self.ethernet_receiver = EthernetReceiver(local_ip="0.0.0.0", local_port=6102)
        self.ethernet_receiver.log_message.connect(self.on_ethernet_log)
        self.ethernet_receiver.adc_data_received.connect(self.on_ethernet_adc_data)

        # 初始化UI
        self.init_ui()

        # 连接信号
        if self.serial_manager:
            self.connect_signals()
            if self.serial_manager.is_connected():
                self.on_serial_connected()

        # 初始化实际参数显示
        self.update_actual_params_display()

        # 🔥 V7.5: 初始化UI状态（流模式默认）
        self.on_mode_changed()  # 触发一次模式切换，设置正确的控件状态

        # 启动更新定时器
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_measurements)
        self.update_timer.start(500)

        # 🔥 V8.6.33: 显示更新定时器 - 主动拉取模式，避免信号队列积压
        # 50ms = 20Hz刷新，人眼流畅且CPU负载低
        # 优势: 不依赖UDP线程emit频率，始终从RingBuffer读取最新数据
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.update_display)
        self.display_timer.start(50)  # 20 FPS

    def init_ui(self):
        """初始化用户界面(紧凑版)"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(3, 3, 3, 3)
        main_layout.setSpacing(3)

        # ==== 创建分屏显示区域（时域波形 + FFT频谱）====
        display_splitter = QWidget()
        display_layout = QVBoxLayout(display_splitter)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.setSpacing(3)

        # ==== 时域波形显示区域 ====
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel("left", "电压 (V)", color="#d0d0d0", size="10pt")
        self.plot_widget.setLabel("bottom", "时间 (us)", color="#d0d0d0", size="10pt")
        self.plot_widget.setYRange(-5, 5)  # ADC范围：-5V到+5V
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setBackground("#1a1a1a")

        # 🔥 V8.3: 启用鼠标交互（XY轴平移和缩放始终可用）
        self.plot_widget.setMouseEnabled(x=True, y=True)
        self.plot_widget.enableAutoRange(axis="x", enable=False)
        self.plot_widget.enableAutoRange(axis="y", enable=False)

        # 🔥 ViewBox鼠标模式：PanMode允许拖拽平移，滚轮缩放XY轴
        vb = self.plot_widget.getViewBox()
        vb.setMouseMode(vb.PanMode)  # 平移模式（左键=平移，滚轮=缩放）

        # 设置网格样式
        self.plot_widget.getAxis("left").setPen(pg.mkPen("#404040", width=1))
        self.plot_widget.getAxis("bottom").setPen(pg.mkPen("#404040", width=1))
        self.plot_widget.getAxis("left").setTextPen("#d0d0d0")
        self.plot_widget.getAxis("bottom").setTextPen("#d0d0d0")

        # 添加PyQtGraph内置图例（参考示波器样式）
        self.legend = self.plot_widget.addLegend(offset=(-10, 10))
        self.legend.setBrush(pg.mkBrush(30, 30, 30, 200))  # 半透明背景
        self.legend.setPen(pg.mkPen(80, 80, 80, 180))  # 边框

        # 双通道波形曲线
        self.ch1_curve = self.plot_widget.plot(
            pen=pg.mkPen("#00ff00", width=2), name="CH1"
        )
        self.ch2_curve = self.plot_widget.plot(
            pen=pg.mkPen("#ffff00", width=2), name="CH2"
        )

        # 为图例项添加点击事件，实现点击切换显示/隐藏并同步复选框
        # 使用functools.partial避免闭包问题
        from functools import partial

        for item in self.legend.items:
            for single_item in item:
                if hasattr(single_item, "item"):
                    curve_item = single_item.item
                    # 创建一个绑定curve的函数，避免lambda闭包问题
                    single_item.mouseClickEvent = partial(
                        self.on_legend_click_wrapper, curve_item
                    )

        # 设置CH2初始显示（双通道ADC）
        self.ch2_visible = True
        self.ch2_curve.setVisible(True)

        # 延迟更新图例外观以确保图例已完全创建
        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, self.update_legend_appearance)

        # 🔥 添加可拖动的触发线（两种模式都可用）
        self.trigger_line = pg.InfiniteLine(
            pos=0,
            angle=0,
            pen=pg.mkPen("#ff0000", width=2, style=Qt.DashLine),
            movable=True,
            label="Trigger={value:.3f}V",
            labelOpts={"position": 0.95, "color": "#ff0000", "fill": (0, 0, 0, 100)},
        )
        self.trigger_line.sigPositionChanged.connect(self.on_trigger_line_moved)
        self.trigger_line.setVisible(True)  # 默认可见
        self.plot_widget.addItem(self.trigger_line)

        # 🔥 添加右键菜单
        self.plot_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.plot_widget.customContextMenuRequested.connect(self.show_context_menu)

        display_layout.addWidget(self.plot_widget, stretch=6)  # 时域波形占60%

        # ==== FFT频谱显示区域 ====
        self.fft_widget = pg.PlotWidget()
        self.fft_widget.setLabel("left", "幅度 (dBV)", color="#d0d0d0", size="10pt")
        self.fft_widget.setLabel("bottom", "频率 (Hz)", color="#d0d0d0", size="10pt")
        self.fft_widget.showGrid(x=True, y=True, alpha=0.3)
        self.fft_widget.setBackground("#1a1a1a")
        # 🔥 使用线性刻度（更符合频谱分析习惯）
        self.fft_widget.setLogMode(x=False, y=False)

        # 设置网格样式
        self.fft_widget.getAxis("left").setPen(pg.mkPen("#404040", width=1))
        self.fft_widget.getAxis("bottom").setPen(pg.mkPen("#404040", width=1))
        self.fft_widget.getAxis("left").setTextPen("#d0d0d0")
        self.fft_widget.getAxis("bottom").setTextPen("#d0d0d0")

        # 🔥 V8.6.43: 启用FFT图表的鼠标交互（拖拽平移和滚轮缩放）
        self.fft_widget.setMouseEnabled(x=True, y=True)  # 启用鼠标拖拽
        self.fft_widget.enableAutoRange(enable=False)  # 禁用自动范围，使用手动控制

        # 🔥 V8.6.43: 双通道FFT曲线（颜色匹配波形：CH1绿色，CH2黄色）
        self.fft_curve_ch1 = self.fft_widget.plot(
            pen=pg.mkPen("#00ff00", width=2), name="CH1 FFT"
        )
        self.fft_curve_ch2 = self.fft_widget.plot(
            pen=pg.mkPen("#ffff00", width=2), name="CH2 FFT"
        )

        # 默认隐藏FFT显示
        self.fft_widget.setVisible(False)
        display_layout.addWidget(self.fft_widget, stretch=4)  # FFT频谱占40%

        main_layout.addWidget(display_splitter, stretch=10)  # 显示区域占最大空间

        # ==== 控制面板 ====
        control_panel = self.create_control_panel()
        main_layout.addWidget(control_panel)

        # ==== 测量参数显示 ====
        measurement_panel = self.create_measurement_panel()
        main_layout.addWidget(measurement_panel)

    def create_control_panel(self):
        """创建控制面板（紧凑版）"""
        group = QGroupBox("⚙️ 采集设置")
        group.setMaximumHeight(100)  # 限制高度

        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(8, 8, 8, 8)

        # === 第一行：通道 + 模式 + 触发 + FFT + 按钮 ===
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(12)

        # 🔥 V5.2：通道选择复选框（硬件级使能+可见性控制）
        channel_label = QLabel("通道:")
        channel_label.setFixedWidth(40)
        channel_label.setToolTip("硬件级ADC采集使能\n取消勾选将停止对应通道的数据采集")
        row1_layout.addWidget(channel_label)

        self.ch1_checkbox = QCheckBox("CH1")
        self.ch1_checkbox.setChecked(True)
        self.ch1_checkbox.setToolTip("CH1硬件采集使能\n取消勾选将停止CH1的ADC采集")
        self.ch1_checkbox.stateChanged.connect(self.on_ch1_enable_changed)
        self.ch1_checkbox.stateChanged.connect(self.sync_legend_from_control)
        row1_layout.addWidget(self.ch1_checkbox)

        self.ch2_checkbox = QCheckBox("CH2")
        self.ch2_checkbox.setChecked(True)
        self.ch2_checkbox.setToolTip("CH2硬件采集使能\n取消勾选将停止CH2的ADC采集")
        self.ch2_checkbox.stateChanged.connect(self.on_ch2_enable_changed)
        self.ch2_checkbox.stateChanged.connect(self.sync_legend_from_control)
        row1_layout.addWidget(self.ch2_checkbox)

        # 分隔线
        sep1 = QLabel("|")
        sep1.setStyleSheet("color: #ccc;")
        row1_layout.addWidget(sep1)

        # 🔥 模式选择（提前到最前面）
        mode_label = QLabel("模式:")
        mode_label.setFixedWidth(40)
        row1_layout.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["流模式", "Buffer模式"])
        self.mode_combo.setFixedWidth(100)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        self.mode_combo.setToolTip(
            "流模式：持续采集，UDP实时传输，环形缓冲显示\n"
            "  - 只需设置采样率和显示窗口\n"
            "  - 适用于观察连续信号\n\n"
            "Buffer模式：单次触发，DDR3存储，完整波形\n"
            "  - 设置采样率+采集深度\n"
            "  - 适用于捕捉单次事件"
        )
        row1_layout.addWidget(self.mode_combo)

        # 分隔线
        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #ccc;")
        row1_layout.addWidget(sep2)

        # 触发电平设置（两种模式都可用）
        trigger_label = QLabel("触发:")
        trigger_label.setFixedWidth(40)
        row1_layout.addWidget(trigger_label)
        self.trigger_level_label = trigger_label

        self.trigger_level_spin = QSpinBox()
        self.trigger_level_spin.setRange(-5000, 5000)
        self.trigger_level_spin.setValue(0)
        self.trigger_level_spin.setSuffix(" mV")
        self.trigger_level_spin.setFixedWidth(90)
        self.trigger_level_spin.setToolTip(
            "触发电平（毫伏）\n流模式：边沿触发\nBuffer模式：DDR3触发采集"
        )
        self.trigger_level_spin.valueChanged.connect(self.on_trigger_level_changed)
        self.trigger_level_spin.setEnabled(True)  # 默认启用
        row1_layout.addWidget(self.trigger_level_spin)

        # 触发边沿选择按钮
        self.trigger_edge_btn = QPushButton("⬆ 上升沿")
        self.trigger_edge_btn.setFixedWidth(80)
        self.trigger_edge_btn.setFixedHeight(26)
        self.trigger_edge_btn.setCheckable(True)
        self.trigger_edge_btn.setChecked(False)  # 默认上升沿
        self.trigger_edge_btn.setToolTip("点击切换触发边沿\n上升沿: 0\n下降沿: 1")
        self.trigger_edge_btn.clicked.connect(self.on_trigger_edge_toggled)
        self.trigger_edge_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:checked {
                background-color: #d0d0d0;
            }
            QPushButton:hover { background-color: #e5e5e5; }
        """
        )
        row1_layout.addWidget(self.trigger_edge_btn)

        # 分隔线
        sep3 = QLabel("|")
        sep3.setStyleSheet("color: #ccc;")
        row1_layout.addWidget(sep3)

        # FFT开关
        self.fft_checkbox = QCheckBox("FFT")
        self.fft_checkbox.setChecked(False)
        self.fft_checkbox.setEnabled(True)  # 启用FFT功能
        self.fft_checkbox.setToolTip(
            "显示/隐藏FFT频谱图\n数据源跟随通道复选框和图例显示状态"
        )
        self.fft_checkbox.stateChanged.connect(self.on_fft_toggled)
        row1_layout.addWidget(self.fft_checkbox)

        # 窗函数选择
        window_label = QLabel("窗:")
        window_label.setFixedWidth(25)
        window_label.setToolTip("FFT窗函数选择")
        row1_layout.addWidget(window_label)

        self.window_combo = QComboBox()
        self.window_combo.addItems(
            [
                "矩形窗",  # Rectangular (None)
                "汉宁窗",  # Hanning
                "汉明窗",  # Hamming
                "Blackman窗",  # Blackman (推荐)
                "Bartlett窗",  # Bartlett
                "Kaiser窗",  # Kaiser
            ]
        )
        self.window_combo.setCurrentIndex(2)  # 默认汉明窗（旁瓣抑制好）
        self.window_combo.setFixedWidth(100)
        self.window_combo.setToolTip(
            "FFT窗函数选择：\n"
            "• 矩形窗：最窄主瓣，最大旁瓣\n"
            "• 汉宁窗：均衡性能\n"
            "• 汉明窗：旁瓣抑制好\n"
            "• Blackman窗：最佳旁瓣抑制（推荐）\n"
            "• Bartlett窗：三角窗，简单\n"
            "• Kaiser窗：可调参数，灵活性高"
        )
        self.window_combo.currentIndexChanged.connect(self.on_window_changed)
        row1_layout.addWidget(self.window_combo)

        row1_layout.addStretch()

        # Auto 自动缩放按钮
        self.auto_scale_btn = QPushButton("🔍 Auto")
        self.auto_scale_btn.setFixedWidth(70)
        self.auto_scale_btn.setFixedHeight(32)
        self.auto_scale_btn.clicked.connect(self.auto_scale)
        self.auto_scale_btn.setToolTip("自动调整时间轴和Y轴显示范围")
        self.auto_scale_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #17a2b8;
                color: white;
                font-weight: bold;
                font-size: 12px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #138496; }
        """
        )
        row1_layout.addWidget(self.auto_scale_btn)

        # 启动/停止切换按钮
        self.toggle_btn = QPushButton("▶ 启动")
        self.toggle_btn.setFixedWidth(90)
        self.toggle_btn.setFixedHeight(32)
        self.toggle_btn.clicked.connect(self.on_toggle_capture)
        self.toggle_btn.setEnabled(False)
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #5cb85c;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #4cae4c; }
            QPushButton:disabled { 
                background-color: #cccccc; 
                color: #666;
            }
        """
        )
        row1_layout.addWidget(self.toggle_btn)

        layout.addLayout(row1_layout)

        # === 第二行：采样参数设置 ===
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(12)

        # 🔥 V7.9: Buffer模式参数选择（只在Buffer模式下显示）
        self.buffer_mode_label = QLabel("参数:")
        self.buffer_mode_label.setFixedWidth(40)
        row2_layout.addWidget(self.buffer_mode_label)

        self.buffer_mode_combo = QComboBox()
        self.buffer_mode_combo.addItems(["自动", "手动"])
        self.buffer_mode_combo.setCurrentIndex(0)  # 默认自动
        self.buffer_mode_combo.setFixedWidth(80)
        self.buffer_mode_combo.setToolTip(
            "自动：根据信号频率自动计算采样参数\n" "手动：自定义采样率和深度"
        )
        self.buffer_mode_combo.currentIndexChanged.connect(self.on_buffer_mode_changed)
        row2_layout.addWidget(self.buffer_mode_combo)

        # 🔥 分隔线
        self.sep_manual = QLabel("|")
        self.sep_manual.setStyleSheet("color: #ccc;")
        row2_layout.addWidget(self.sep_manual)

        # 🔥 V7.9: 抽取率/采样率设置（流模式=抽取率，Buffer模式=采样率）
        self.rate_label = QLabel("抽取率:")  # 默认流模式文本
        self.rate_label.setFixedWidth(55)
        row2_layout.addWidget(self.rate_label)

        self.sample_rate_combo = QComboBox()
        # 🔥 V8.7.26: 动态生成采样率选项（与SAMPLE_RATES数组同步）
        rate_options = []
        for rate in AdaptiveSamplingCalculator.SAMPLE_RATES:
            if rate >= 1_000_000:
                rate_options.append(f"{rate / 1_000_000:.10g} MSPS")
            elif rate >= 1_000:
                rate_options.append(f"{rate / 1_000:.10g} KSPS")
            else:
                rate_options.append(f"{rate:.10g} SPS")
        self.sample_rate_combo.addItems(rate_options)
        self.sample_rate_combo.setCurrentIndex(0)
        self.sample_rate_combo.setFixedWidth(120)
        self.sample_rate_combo.setToolTip("流模式：抽取率\nBuffer模式：采样率")
        self.sample_rate_combo.setEnabled(True)  # 流模式默认启用
        self.sample_rate_combo.currentIndexChanged.connect(self.on_sample_rate_changed)
        row2_layout.addWidget(self.sample_rate_combo)

        # 分隔线
        sep4 = QLabel("|")
        sep4.setStyleSheet("color: #ccc;")
        row2_layout.addWidget(sep4)

        # 🔥 V7.9: 深度设置（流模式=显示深度，Buffer模式=采集深度）
        self.depth_label = QLabel("显示:")  # 默认流模式文本
        self.depth_label.setFixedWidth(40)
        row2_layout.addWidget(self.depth_label)

        self.sample_depth_combo = QComboBox()
        # 🔥 V8.7.26: 动态生成深度选项（与SAMPLE_DEPTHS数组同步）
        depth_options = []
        for depth in AdaptiveSamplingCalculator.SAMPLE_DEPTHS:
            if depth >= 1048576:  # >= 1M
                depth_options.append(f"{depth // 1048576}M")
            elif depth >= 1024:  # >= 1K
                depth_options.append(f"{depth // 1024}K")
            else:
                depth_options.append(f"{depth}")
        self.sample_depth_combo.addItems(depth_options)
        self.sample_depth_combo.setCurrentIndex(2)  # 默认4K(索引2)
        self.sample_depth_combo.setFixedWidth(80)
        self.sample_depth_combo.setToolTip(
            "流模式：显示窗口大小\nBuffer模式：采集深度\n(1K=1024点)"
        )
        self.sample_depth_combo.setEnabled(True)  # 默认启用
        self.sample_depth_combo.currentIndexChanged.connect(
            self.on_sample_depth_changed
        )
        row2_layout.addWidget(self.sample_depth_combo)

        # 分隔线
        sep5 = QLabel("|")
        sep5.setStyleSheet("color: #ccc;")
        row2_layout.addWidget(sep5)

        # 🔥 V8.6.5: 流模式输入带宽固定提示（双通道2MSPS×2=4M, 单通道4MSPS）
        self.stream_bandwidth_label = QLabel("输入带宽: 双通道2MSPS×2, 单通道4MSPS")
        self.stream_bandwidth_label.setStyleSheet(
            "color: #0066cc; font-weight: bold; font-size: 11px;"
        )
        self.stream_bandwidth_label.setVisible(True)  # 流模式默认显示
        row2_layout.addWidget(self.stream_bandwidth_label)

        # 🔥 V8.7.24: Buffer模式实际参数显示（Buffer模式专用）
        self.actual_params_label = QLabel("实际: 未启动")
        self.actual_params_label.setStyleSheet("color: #888; font-size: 10px;")
        self.actual_params_label.setVisible(False)  # Buffer模式才显示
        row2_layout.addWidget(self.actual_params_label)

        row2_layout.addStretch()

        layout.addLayout(row2_layout)

        group.setLayout(layout)
        return group

    def create_measurement_panel(self):
        """创建测量参数显示面板（简洁表格风格）"""
        group = QGroupBox("实时测量")
        group.setMaximumHeight(115)
        group.setStyleSheet(
            """
            QGroupBox {
                font-weight: normal;
                border: 1px solid #ddd;
                border-radius: 0px;
                margin-top: 10px;
                padding-top: 10px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #555;
                font-size: 12px;
            }
        """
        )

        main_layout = QVBoxLayout()
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # 创建表格样式的测量显示
        table_widget = QWidget()
        table_widget.setStyleSheet("background: white;")
        grid = QGridLayout(table_widget)
        grid.setSpacing(0)
        grid.setContentsMargins(0, 0, 0, 0)

        # 表格样式定义
        header_style = """
            background: #f5f5f5;
            color: #666;
            font-size: 11px;
            border: 1px solid #ddd;
            border-right: none;
            padding: 5px;
        """
        header_last_style = """
            background: #f5f5f5;
            color: #666;
            font-size: 11px;
            border: 1px solid #ddd;
            padding: 5px;
        """

        cell_style = """
            background: white;
            color: #333;
            font-size: 13px;
            font-weight: bold;
            border: 1px solid #ddd;
            border-top: none;
            border-right: none;
            padding: 6px;
        """
        cell_last_style = """
            background: white;
            color: #333;
            font-size: 13px;
            font-weight: bold;
            border: 1px solid #ddd;
            border-top: none;
            padding: 6px;
        """

        ch1_cell_style = """
            background: white;
            color: #333;
            font-size: 13px;
            font-weight: bold;
            border: 1px solid #ddd;
            border-top: none;
            border-right: none;
            padding: 6px;
            border-left: 3px solid #5cb85c;
        """

        ch1_cell_disabled_style = """
            background: #fafafa;
            color: #999;
            font-size: 12px;
            border: 1px solid #ddd;
            border-top: none;
            border-right: none;
            padding: 6px;
            border-left: 3px solid #ccc;
        """

        ch2_cell_style = """
            background: white;
            color: #333;
            font-size: 13px;
            font-weight: bold;
            border: 1px solid #ddd;
            border-top: none;
            border-right: none;
            padding: 6px;
            border-left: 3px solid #f0ad4e;
        """

        ch2_cell_disabled_style = """
            background: #fafafa;
            color: #999;
            font-size: 12px;
            border: 1px solid #ddd;
            border-top: none;
            border-right: none;
            padding: 6px;
            border-left: 3px solid #ccc;
        """

        # 表头 - 保留频率列
        headers = ["", "频率", "峰峰值", "有效值", "最大", "最小"]
        for col, text in enumerate(headers):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            if col == len(headers) - 1:
                label.setStyleSheet(header_last_style)
            else:
                label.setStyleSheet(header_style)
            grid.addWidget(label, 0, col)

        # CH1 行
        ch1_label = QLabel("CH1")
        ch1_label.setAlignment(Qt.AlignCenter)
        ch1_label.setStyleSheet(ch1_cell_style)
        grid.addWidget(ch1_label, 1, 0)

        self.ch1_freq_label = QLabel("-- Hz")
        self.ch1_freq_label.setAlignment(Qt.AlignCenter)
        self.ch1_freq_label.setStyleSheet(cell_style)
        grid.addWidget(self.ch1_freq_label, 1, 1)

        self.ch1_vpp_label = QLabel("-- V")
        self.ch1_vpp_label.setAlignment(Qt.AlignCenter)
        self.ch1_vpp_label.setStyleSheet(cell_style)
        grid.addWidget(self.ch1_vpp_label, 1, 2)

        self.ch1_vrms_label = QLabel("-- V")
        self.ch1_vrms_label.setAlignment(Qt.AlignCenter)
        self.ch1_vrms_label.setStyleSheet(cell_style)
        grid.addWidget(self.ch1_vrms_label, 1, 3)

        self.ch1_vmax_label = QLabel("-- V")
        self.ch1_vmax_label.setAlignment(Qt.AlignCenter)
        self.ch1_vmax_label.setStyleSheet(cell_style)
        grid.addWidget(self.ch1_vmax_label, 1, 4)

        self.ch1_vmin_label = QLabel("-- V")
        self.ch1_vmin_label.setAlignment(Qt.AlignCenter)
        self.ch1_vmin_label.setStyleSheet(cell_last_style)
        grid.addWidget(self.ch1_vmin_label, 1, 5)

        # CH2 行（默认启用，双通道ADC）
        self.ch2_label = QLabel("CH2")
        self.ch2_label.setAlignment(Qt.AlignCenter)
        self.ch2_label.setStyleSheet(ch2_cell_style)  # 🔥 默认启用样式
        grid.addWidget(self.ch2_label, 2, 0)

        self.ch2_freq_label = QLabel("-- Hz")
        self.ch2_freq_label.setAlignment(Qt.AlignCenter)
        self.ch2_freq_label.setStyleSheet(cell_style)  # 使用普通样式，无橙色竖条
        grid.addWidget(self.ch2_freq_label, 2, 1)

        self.ch2_vpp_label = QLabel("-- V")
        self.ch2_vpp_label.setAlignment(Qt.AlignCenter)
        self.ch2_vpp_label.setStyleSheet(cell_style)  # 使用普通样式，无橙色竖条
        grid.addWidget(self.ch2_vpp_label, 2, 2)

        self.ch2_vrms_label = QLabel("-- V")
        self.ch2_vrms_label.setAlignment(Qt.AlignCenter)
        self.ch2_vrms_label.setStyleSheet(cell_style)  # 使用普通样式，无橙色竖条
        grid.addWidget(self.ch2_vrms_label, 2, 3)

        self.ch2_vmax_label = QLabel("-- V")
        self.ch2_vmax_label.setAlignment(Qt.AlignCenter)
        self.ch2_vmax_label.setStyleSheet(cell_style)  # 使用普通样式，无橙色竖条
        grid.addWidget(self.ch2_vmax_label, 2, 4)

        self.ch2_vmin_label = QLabel("-- V")
        self.ch2_vmin_label.setAlignment(Qt.AlignCenter)
        self.ch2_vmin_label.setStyleSheet(cell_style)  # 使用普通样式，无橙色竖条
        grid.addWidget(self.ch2_vmin_label, 2, 5)

        # 设置列宽 - 恢复频率列
        grid.setColumnStretch(0, 1)  # 通道标签
        grid.setColumnStretch(1, 2)  # 频率
        grid.setColumnStretch(2, 2)  # 峰峰值
        grid.setColumnStretch(3, 2)  # 有效值
        grid.setColumnStretch(4, 2)  # 最大
        grid.setColumnStretch(5, 2)  # 最小

        main_layout.addWidget(table_widget)
        group.setLayout(main_layout)

        # 保存CH1相关控件用于启用/禁用
        self.ch1_measurement_widgets = [
            ch1_label,
            self.ch1_freq_label,
            self.ch1_vpp_label,
            self.ch1_vrms_label,
            self.ch1_vmax_label,
            self.ch1_vmin_label,
        ]

        # 保存CH2相关控件用于启用/禁用
        self.ch2_measurement_widgets = [
            self.ch2_label,
            self.ch2_freq_label,
            self.ch2_vpp_label,
            self.ch2_vrms_label,
            self.ch2_vmax_label,
            self.ch2_vmin_label,
        ]

        # 保存样式字符串用于动态切换
        self.ch1_cell_style_enabled = ch1_cell_style
        self.ch1_cell_style_disabled = ch1_cell_disabled_style
        self.ch2_cell_style_enabled = ch2_cell_style
        self.ch2_cell_style_disabled = ch2_cell_disabled_style

        # 保存ch1_label的引用
        self.ch1_label = ch1_label

        return group

    def connect_signals(self):
        """连接串口管理器信号"""
        self.serial_manager.connected.connect(self.on_serial_connected)
        self.serial_manager.disconnected.connect(self.on_serial_disconnected)
        self.serial_manager.adc_data_received.connect(self.on_adc_data_received)
        self.serial_manager.frequency_data_received.connect(
            self.on_frequency_data_received
        )
        self.serial_manager.adc_capture_completed.connect(self.on_adc_capture_completed)

    @Slot()
    def on_serial_connected(self):
        """串口连接成功"""
        self.toggle_btn.setEnabled(True)
        # 启动以太网接收器
        if not self.ethernet_receiver.running:
            self.ethernet_receiver.start()
            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    "✅ 以太网UDP接收器已启动 (0.0.0.0:6102)"
                )

    @Slot()
    def on_serial_disconnected(self):
        """串口断开"""
        self.toggle_btn.setEnabled(False)
        # 停止以太网接收器
        if self.ethernet_receiver.running:
            self.ethernet_receiver.stop()
            if self.serial_manager:
                self.serial_manager.log_message.emit("⏹️ 以太网UDP接收器已停止")

    @Slot(str)
    def on_ethernet_log(self, message):
        """以太网接收器日志转发"""
        if self.serial_manager:
            self.serial_manager.log_message.emit(f"[以太网] {message}")

    @Slot(list)
    # 🔥🔥🔥 注意：此函数已被第3200行附近的同名函数覆盖，这里的代码不会执行！
    # 请直接跳到第3200行查看实际使用的函数
    def on_ethernet_adc_data_DEPRECATED(self, raw_data):
        """
        以太网ADC数据接收处理（🔥 V5.3: 修复数据解析逻辑）

        FPGA V5.2统一交织格式：interleaved_data = {CH2[7:0], CH1[7:0]}
        16-to-8转换后的字节流需要确定字节序

        Args:
            raw_data: 原始字节列表，每字节对应一个8位ADC样点
        """
        # 🔥🔥🔥 强制打印：第一个包必须显示
        if not hasattr(self, "_debug_packet_count"):
            self._debug_packet_count = 0
            self._debug_first_packet_shown = False

        self._debug_packet_count += 1

        # 🔥 第一个包或每500个包强制输出详细调试
        should_debug = (
            self._debug_packet_count == 1 or self._debug_packet_count % 500 == 1
        )

        if len(raw_data) < 2:
            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"⚠️ 以太网数据长度异常: {len(raw_data)} 字节"
                )
            return

        try:
            # 🔥 V8.6.41: 调试输出重定向到调试窗口
            if should_debug:
                preview_bytes = raw_data[:32]
                hex_str = " ".join([f"{b:02X}" for b in preview_bytes])

                debug_msg = (
                    f"\n{'='*60}\n"
                    f"🔍🔍🔍 [UDP数据解析调试 #{self._debug_packet_count}]\n"
                    f"{'='*60}\n"
                    f"📦 数据包大小: {len(raw_data)} 字节\n"
                    f"🎛️  通道状态: CH1={'✅启用' if self.ch1_enabled else '❌禁用'}, "
                    f"CH2={'✅启用' if self.ch2_enabled else '❌禁用'}\n"
                    f"📊 前32字节(HEX): {hex_str}"
                )
                self.debug_output.emit(debug_msg)

            ch1_samples = []
            ch2_samples = []

            # 🔥🔥🔥 测试两种假设的字节序
            # 假设A: [CH2, CH1, CH2, CH1] - V5.3当前实现
            # 假设B: [CH1, CH2, CH1, CH2] - V5.2之前的实现

            # 让我们同时测试两种假设，看哪个更合理
            test_ch1_a = []  # 假设A的CH1数据
            test_ch2_a = []  # 假设A的CH2数据
            test_ch1_b = []  # 假设B的CH1数据
            test_ch2_b = []  # 假设B的CH2数据

            for i in range(0, min(len(raw_data), 20), 2):
                if i + 1 < len(raw_data):
                    byte0 = raw_data[i]
                    byte1 = raw_data[i + 1]

                    # 假设A: byte0=CH2, byte1=CH1
                    test_ch2_a.append(byte0)
                    test_ch1_a.append(byte1)

                    # 假设B: byte0=CH1, byte1=CH2
                    test_ch1_b.append(byte0)
                    test_ch2_b.append(byte1)

            # 🔥 调试输出：对比两种假设
            if should_debug and self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"\n【假设对比分析】\n"
                    f"假设A [CH2,CH1,CH2,CH1]: CH1前10={test_ch1_a[:10]}, CH2前10={test_ch2_a[:10]}\n"
                    f"假设B [CH1,CH2,CH1,CH2]: CH1前10={test_ch1_b[:10]}, CH2前10={test_ch2_b[:10]}\n"
                    f"\n预期结果（CH1输入20kHz正弦，CH2悬空）:\n"
                    f"  - CH1应该有变化（如120~135范围）\n"
                    f"  - CH2应该接近128或随机噪声\n"
                )

            # 🔥🔥🔥 实际解析：使用假设A [CH2, CH1, CH2, CH1]
            for i in range(0, len(raw_data), 2):
                if i + 1 < len(raw_data):
                    ch2_byte = raw_data[i]
                    ch1_byte = raw_data[i + 1]

                    ch2_voltage = (ch2_byte / 255.0) * 10.0 - 5.0
                    ch1_voltage = (ch1_byte / 255.0) * 10.0 - 5.0

                    # 🔥 关键修复：只添加已使能的通道数据
                    if self.ch1_enabled:
                        ch1_samples.append(ch1_voltage)
                    if self.ch2_enabled:
                        ch2_samples.append(ch2_voltage)

            # 🔥 V8.6.41: 解析结果输出到调试窗口
            if should_debug:
                debug_msg = f"\n【解析结果】\n"
                if ch1_samples:
                    ch1_min, ch1_max = min(ch1_samples), max(ch1_samples)
                    ch1_avg = sum(ch1_samples) / len(ch1_samples)
                    debug_msg += f"  CH1: {len(ch1_samples)}点, 范围({ch1_min:.2f}~{ch1_max:.2f}V), 平均{ch1_avg:.2f}V\n"
                else:
                    debug_msg += f"  CH1: 未启用或无数据\n"

                if ch2_samples:
                    ch2_min, ch2_max = min(ch2_samples), max(ch2_samples)
                    ch2_avg = sum(ch2_samples) / len(ch2_samples)
                    debug_msg += f"  CH2: {len(ch2_samples)}点, 范围({ch2_min:.2f}~{ch2_max:.2f}V), 平均{ch2_avg:.2f}V\n"
                else:
                    debug_msg += f"  CH2: 未启用或无数据\n"

                debug_msg += f"{'='*60}"
                self.debug_output.emit(debug_msg)

            # 添加到缓冲区
            if self.ch1_enabled and ch1_samples:
                self.ch1_buffer.append(ch1_samples)

            if self.ch2_enabled and ch2_samples:
                self.ch2_buffer.append(ch2_samples)

            # 🔥 关键：标记接收到数据，触发显示更新
            if not hasattr(self, "data_received_flag"):
                self.data_received_flag = False
            self.data_received_flag = True

            # 🔥 立即更新显示（低频信号需要快速响应）
            if len(self.ch1_buffer) >= 100 or len(self.ch2_buffer) >= 100:
                import time

                current_time = time.time()
                if not hasattr(self, "_last_display_update"):
                    self._last_display_update = 0
                # 低频信号更频繁更新（50ms）
                update_interval = 0.05 if self.sample_rate < 10000 else 0.1
                if current_time - self._last_display_update >= update_interval:
                    self.update_waveform_display()
                    self._last_display_update = current_time

        except Exception as e:
            if self.serial_manager:
                self.serial_manager.log_message.emit(f"⚠️ 以太网数据解析错误: {e}")

    def on_legend_click_wrapper(self, curve_item, event):
        """图例点击事件包装器（解决闭包问题）"""
        self.on_legend_click(curve_item, event)

    def on_legend_click(self, curve_item, event):
        """图例点击事件：仅切换波形显示/隐藏（不影响硬件采集）"""
        # 🔥 修复：图例只控制显示，不触发硬件控制
        is_visible = curve_item.isVisible()
        new_visible = not is_visible
        curve_item.setVisible(new_visible)

        # 更新内部显示状态（不影响ch1_enabled/ch2_enabled）
        if curve_item == self.ch1_curve:
            self.ch1_visible = new_visible
        elif curve_item == self.ch2_curve:
            self.ch2_visible = new_visible

        # 🔥 V8.7.65: 同步FFT曲线可见性
        if self.fft_enabled:
            if curve_item == self.ch1_curve and hasattr(self, "fft_curve_ch1"):
                self.fft_curve_ch1.setVisible(new_visible)
            elif curve_item == self.ch2_curve and hasattr(self, "fft_curve_ch2"):
                self.fft_curve_ch2.setVisible(new_visible)

        # 更新图例的视觉效果
        self.update_legend_appearance()

        # 🔥 不同步到复选框，不触发硬件控制
        if self.serial_manager:
            ch_name = "CH1" if curve_item == self.ch1_curve else "CH2"
            state = "显示" if new_visible else "隐藏"
            self.serial_manager.log_message.emit(
                f"👁️ 图例点击: {ch_name} 波形{state}（仅UI显示，不影响采集）"
            )

    @Slot()
    def sync_legend_from_control(self):
        """从控制面板复选框同步波形显示状态"""
        # 🔥 复选框状态同步到显示（硬件控制在on_channel_changed中处理）
        self.ch1_visible = self.ch1_checkbox.isChecked()
        self.ch1_curve.setVisible(self.ch1_visible)
        self.ch2_visible = self.ch2_checkbox.isChecked()
        self.ch2_curve.setVisible(self.ch2_visible)

        # 🔥 V8.7.65: FFT曲线显示/隐藏 - 只控制可见性，不清空数据
        # Buffer模式：FFT已经计算好，只需切换显示状态（即时响应）
        # Stream模式：FFT持续更新，显示状态也会即时生效
        if self.fft_enabled:
            if hasattr(self, "fft_curve_ch1"):
                self.fft_curve_ch1.setVisible(self.ch1_visible)
            if hasattr(self, "fft_curve_ch2"):
                self.fft_curve_ch2.setVisible(self.ch2_visible)

        # 更新图例的视觉效果
        self.update_legend_appearance()

    def update_legend_appearance(self):
        """更新图例项的视觉效果（半透明表示隐藏）"""
        for item in self.legend.items:
            for single_item in item:
                if hasattr(single_item, "item"):
                    curve = single_item.item
                    # 根据曲线可见性设置图例项透明度
                    if curve.isVisible():
                        single_item.setOpacity(1.0)  # 完全不透明
                    else:
                        single_item.setOpacity(0.3)  # 半透明表示已隐藏

    @Slot()
    def on_channel_changed(self):
        """通道选择变化 - 🔥 V5.0: 硬件级控制"""
        old_ch1 = self.ch1_enabled
        old_ch2 = self.ch2_enabled

        self.ch1_enabled = self.ch1_checkbox.isChecked()
        self.ch2_enabled = self.ch2_checkbox.isChecked()

        # 🔥 V8.6.5: 禁用通道时清空频率显示
        if not self.ch1_enabled:
            self.ch1_freq_label.setText("-- Hz")
        if not self.ch2_enabled:
            self.ch2_freq_label.setText("-- Hz")

        # 🔥 V8.6.7: 通道改变时重新配置FPGA
        # Bug修复：仅发送0x28通道使能命令不够，需要重新计算采样参数
        # 原因：单/双通道模式的采样率、数据解析逻辑完全不同
        # 解决：停止采集 → 发送0x28 → 重新测频计算参数 → 重新启动
        if (old_ch1 != self.ch1_enabled) or (old_ch2 != self.ch2_enabled):
            was_capturing = self.is_capturing

            # 1. 停止当前采集
            if was_capturing:
                self.stop_capture()

            # 2. 发送新的通道使能命令
            self.send_channel_enable_command()

            # 3. 流模式且之前在采集：重新启动（会自动重新测频和配置）
            if was_capturing and self.current_mode == "stream":
                QTimer.singleShot(300, self.start_stream_capture)
                self.serial_manager.log_message.emit(
                    "  🔄 通道切换完成，正在重新配置采样参数..."
                )

        # 切换CH1测量行样式（启用/禁用）
        if hasattr(self, "ch1_measurement_widgets"):
            style = (
                self.ch1_cell_style_enabled
                if self.ch1_enabled
                else self.ch1_cell_style_disabled
            )

            # 第一列（CH1标签）- 左边框
            self.ch1_label.setStyleSheet(style)

            # 中间列 - 移除右边框
            style_middle = style.replace("border-right: none;", "")
            self.ch1_freq_label.setStyleSheet(style_middle)
            self.ch1_vpp_label.setStyleSheet(style_middle)
            self.ch1_vrms_label.setStyleSheet(style_middle)
            self.ch1_vmax_label.setStyleSheet(style_middle)

            # 最后一列 - 保持右边框
            self.ch1_vmin_label.setStyleSheet(style)

        # 切换CH2测量行样式（启用/禁用）
        if hasattr(self, "ch2_measurement_widgets"):
            style = (
                self.ch2_cell_style_enabled
                if self.ch2_enabled
                else self.ch2_cell_style_disabled
            )

            # 第一列（CH2标签）- 左边框
            self.ch2_label.setStyleSheet(style)

            # 中间列 - 移除右边框
            style_middle = style.replace("border-right: none;", "")
            self.ch2_freq_label.setStyleSheet(style_middle)
            self.ch2_vpp_label.setStyleSheet(style_middle)
            self.ch2_vrms_label.setStyleSheet(style_middle)
            self.ch2_vmax_label.setStyleSheet(style_middle)

            # 最后一列 - 保持右边框
            self.ch2_vmin_label.setStyleSheet(style)

    @Slot(int)
    @Slot()
    def on_fft_toggled(self):
        """FFT开关切换"""
        self.fft_enabled = self.fft_checkbox.isChecked()
        self.fft_widget.setVisible(self.fft_enabled)

        if self.fft_enabled:
            # 🔥 V8.7.63: 根据通道可见性设置FFT曲线可见性
            if hasattr(self, "fft_curve_ch1"):
                self.fft_curve_ch1.setVisible(self.ch1_visible)
            if hasattr(self, "fft_curve_ch2"):
                self.fft_curve_ch2.setVisible(self.ch2_visible)

            # 重置日志标志，允许重新输出基波频率和调试信息
            if hasattr(self, "_fft_fundamental_logged"):
                delattr(self, "_fft_fundamental_logged")
            if hasattr(self, "_fft_debug_logged"):
                delattr(self, "_fft_debug_logged")
            self.serial_manager.log_message.emit("✅ FFT频谱显示已启用")
        else:
            self.serial_manager.log_message.emit("⚪ FFT频谱显示已关闭")

    @Slot()
    def on_window_changed(self):
        """窗函数选择变化"""
        # 重置日志标志，允许重新输出基波频率
        if hasattr(self, "_fft_fundamental_logged"):
            delattr(self, "_fft_fundamental_logged")

        window_index = self.window_combo.currentIndex()
        window_names = [
            "rectangular",
            "hanning",
            "hamming",
            "blackman",
            "bartlett",
            "kaiser",
        ]
        window_cn_names = [
            "矩形窗",
            "汉宁窗",
            "汉明窗",
            "Blackman窗",
            "Bartlett窗",
            "Kaiser窗",
        ]

        self.fft_window_type = window_names[window_index]

        if hasattr(self, "serial_manager") and self.serial_manager:
            self.serial_manager.log_message.emit(
                f"🔧 FFT窗函数已切换为: {window_cn_names[window_index]}"
            )

    @Slot()
    def on_mode_changed(self):
        """🔥 V7.9: 模式切换（流模式简化UI）"""
        mode_index = self.mode_combo.currentIndex()

        if mode_index == 0:
            # 🔥 流模式：持续采集，环形缓冲显示
            self.current_mode = "stream"

            # 🔥 V7.9: 流模式隐藏自动/手动选择
            self.buffer_mode_label.setVisible(False)
            self.buffer_mode_combo.setVisible(False)
            self.sep_manual.setVisible(False)
            self.actual_params_label.setVisible(False)  # 隐藏Buffer参数

            # 🔥 V8.0: 显示流模式特有控件
            self.stream_bandwidth_label.setVisible(True)  # 显示输入带宽

            # 🔥 V8.0: 流模式隐藏抽取率和深度控件（完全自适应）
            self.rate_label.setVisible(False)
            self.sample_rate_combo.setVisible(False)
            self.depth_label.setVisible(False)
            self.sample_depth_combo.setVisible(False)

            # 更新输入带宽显示
            self._update_stream_bandwidth_display()

        else:
            # 🔥 Buffer模式：单次触发采集
            self.current_mode = "buffer"

            # 🔥 V8.0: 显示Buffer模式控件
            self.buffer_mode_label.setVisible(True)
            self.buffer_mode_combo.setVisible(True)
            self.sep_manual.setVisible(True)
            self.actual_params_label.setVisible(True)  # 显示Buffer参数
            self.stream_bandwidth_label.setVisible(False)  # 隐藏输入带宽

            # 🔥 V8.0: 恢复采样率和深度控件
            self.rate_label.setVisible(True)
            self.sample_rate_combo.setVisible(True)
            self.depth_label.setVisible(True)
            self.sample_depth_combo.setVisible(True)

            # 修改标签文本
            self.rate_label.setText("采样率:")
            self.depth_label.setText("深度:")

            # 根据自动/手动模式设置控件状态
            is_manual = self.buffer_mode_combo.currentIndex() == 1  # 1=手动
            self.sample_rate_combo.setEnabled(is_manual)
            self.sample_depth_combo.setEnabled(is_manual)
            self.sample_rate_combo.setToolTip("设置ADC采样率")
            self.sample_depth_combo.setToolTip("设置采集深度")

    @Slot()
    def on_buffer_mode_changed(self):
        """🔥 V8.7.54: 自动/手动模式切换（流模式和Buffer模式通用）

        修复关键BUG：
        - 原代码：只在is_capturing=True时更新参数
        - 问题场景：Auto按钮先stop_capture()，切换时is_capturing=False，参数不更新
        - 修复：无论采集状态如何，切换到手动模式时立即更新内部参数
        """
        is_manual = self.buffer_mode_combo.currentIndex() == 1  # 0=自动, 1=手动

        # 控制采样率和深度ComboBox的启用状态
        self.sample_rate_combo.setEnabled(is_manual)
        self.sample_depth_combo.setEnabled(is_manual)

        # 🔥 V8.7.54修复: 移除is_capturing条件限制
        # 原因：用户可能在采集停止期间切换模式，此时也需要立即更新参数
        # 场景：Auto按钮 → stop_capture() → 用户切换到手动 → start时应用手动参数
        if is_manual:
            # 手动模式：立即从UI读取并更新内部参数
            self.on_sample_rate_changed()  # 应用采样率 → 更新self.sample_rate/div_factor
            self.on_sample_depth_changed()  # 应用采样深度 → 更新self.buffer_size
            self.update_actual_params_display()  # 更新显示

            if self.serial_manager:
                capturing_status = "（采集中）" if self.is_capturing else "（未采集）"
                self.serial_manager.log_message.emit(
                    f"✅ 已切换至手动模式并同步UI参数{capturing_status}\n"
                    f"   采样率: {self.sample_rate/1e6:.2f}MSPS (div={self.div_factor})\n"
                    f"   采样深度: {self.buffer_size if self.buffer_size else 'N/A'}点"
                )
        else:
            # 切换回自动模式
            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    "🔄 已切换至自动模式（下次启动时将根据频率自适应调整参数）"
                )

    @Slot()
    def on_manual_mode_changed(self):
        """手动/自动模式切换（🔥 已废弃，保留兼容性）"""
        # 现在使用buffer_mode_combo替代
        pass

    @Slot()
    def on_auto_btn_clicked(self):
        """🔥 Auto按钮点击处理"""
        if self.current_mode == "stream":
            # 🔥 V8.6.7: 流模式触发完整重配置流程
            # Bug修复：之前只做auto_scale()不重新配置FPGA，导致通道切换后波形混乱
            # 解决：调用trigger_reconfigure()重新测频并配置FPGA参数
            if self.is_capturing:
                self.trigger_reconfigure(adjust_xaxis=True)
            else:
                # 未采集状态，先做缩放
                self.auto_scale()
            return

        # Buffer模式
        if self.buffer_mode_combo.currentIndex() == 1:  # 1=手动
            # 手动模式下，Auto按钮仅做XY缩放
            self.auto_scale()
            return

        # Buffer自动模式：触发测频和自适应重新配置
        if self.is_capturing:
            # 停止当前采集
            self.stop_capture()
            QTimer.singleShot(200, self.start_adaptive_capture)
        else:
            self.start_adaptive_capture()

    @Slot()
    def on_trigger_level_changed(self):
        """触发电平SpinBox修改时同步更新触发线位置"""
        trigger_mv = self.trigger_level_spin.value()
        trigger_v = trigger_mv / 1000.0
        self.trigger_line.setValue(trigger_v)

        # 发送触发配置到FPGA
        self.send_trigger_config()

    @Slot()
    def on_trigger_edge_toggled(self):
        """触发边沿切换按钮"""
        is_falling = self.trigger_edge_btn.isChecked()
        if is_falling:
            self.trigger_edge_btn.setText("↘ 下降沿")
        else:
            self.trigger_edge_btn.setText("↗ 上升沿")

        # 发送触发配置到FPGA
        self.send_trigger_config()

    def send_trigger_config(self):
        """🔥 V8.7.1: 发送触发配置到FPGA (命令0x22 - 统一触发系统)"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            return

        # 获取触发参数
        trigger_mv = self.trigger_level_spin.value()

        # 转换为8位ADC值 (0-255)
        # ADC输入范围: -2.5V ~ +2.5V 映射到 0 ~ 255
        trigger_adc_8bit = int((trigger_mv / 1000.0 + 2.5) * 255 / 5.0)
        trigger_adc_8bit = max(0, min(255, trigger_adc_8bit))

        # 触发边沿: 0=上升沿, 1=下降沿
        trigger_edge = 1 if self.trigger_edge_btn.isChecked() else 0

        # 🔥 V8.7.2: 触发使能逻辑优化
        # Buffer模式: 默认禁用触发（立即采集），除非明确勾选"触发使能"
        # 流模式: 根据触发线可见性判断
        if self.current_mode == "buffer":
            # Buffer模式: 默认立即采集，不等待触发
            trigger_enable = 0
        else:
            # 流模式: 根据触发线可见性
            trigger_enable = 1 if self.trigger_line.isVisible() else 0

        # 触发通道选择 (0=CH1, 1=CH2)
        # TODO: 后续添加UI控件，当前固定CH1
        trigger_channel = 0

        # 🔥 新格式：3字节 (V8.7.1统一触发系统)
        # Byte0: bit0=使能, bit1=通道
        # Byte1: bit0=边沿
        # Byte2: 电平(0-255)
        byte0 = (trigger_enable & 0x01) | ((trigger_channel & 0x01) << 1)
        byte1 = trigger_edge & 0x01
        byte2 = trigger_adc_8bit

        # 详细日志（发送前显示）
        ch_name = "CH2" if trigger_channel else "CH1"
        edge_name = "下降沿" if trigger_edge else "上升沿"
        enable_status = "启用" if trigger_enable else "禁用"

        self.serial_manager.log_message.emit(
            f"🎯 触发配置: {enable_status}, 源={ch_name}, "
            f"边沿={edge_name}, 电平={trigger_mv}mV({trigger_adc_8bit})"
        )

        payload = bytes([byte0, byte1, byte2])
        self.serial_manager.send_command(0x22, payload)

    def send_channel_enable_command(self):
        """🔥 V5.0: 发送通道使能命令到FPGA (命令0x28)"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    "⚠️ 串口未连接，跳过0x28通道使能命令"
                )
            return

        # 构造payload: [CH1使能:1字节] + [CH2使能:1字节]
        ch1_en = 1 if self.ch1_enabled else 0
        ch2_en = 1 if self.ch2_enabled else 0
        payload = bytes([ch1_en, ch2_en])
        self.serial_manager.send_command(0x28, payload)

        ch_status = []
        if self.ch1_enabled:
            ch_status.append("CH1✓")
        if self.ch2_enabled:
            ch_status.append("CH2✓")
        status_str = " + ".join(ch_status) if ch_status else "所有通道禁用"

        self.serial_manager.log_message.emit(f"  ✓ 通道使能: {status_str}")

    @Slot()
    def on_trigger_line_moved(self):
        """触发线拖动时同步更新SpinBox值"""
        trigger_v = self.trigger_line.value()
        trigger_mv = int(trigger_v * 1000)
        self.trigger_level_spin.blockSignals(True)  # 阻止信号避免循环
        self.trigger_level_spin.setValue(trigger_mv)
        self.trigger_level_spin.blockSignals(False)

    def on_sample_rate_changed(self):
        """采样率修改（🔥 V8.7.26: 直接从SAMPLE_RATES数组读取）"""
        rate_index = self.sample_rate_combo.currentIndex()

        # 🔥 V8.7.26: 直接从数组读取采样率，不再用二进制计算
        if 0 <= rate_index < len(AdaptiveSamplingCalculator.SAMPLE_RATES):
            self.sample_rate = AdaptiveSamplingCalculator.SAMPLE_RATES[rate_index]
            # 计算对应的div_factor
            self.div_factor = 50000000 // self.sample_rate
        else:
            # 默认50MSPS
            self.sample_rate = 50000000
            self.div_factor = 1

        # 🔥 V7.8: 流模式采样率限制检查
        MAX_STREAM_RATE = 2000000  # 2 MSPS
        if self.current_mode == "stream" and self.sample_rate > MAX_STREAM_RATE:
            # 流模式超过2MSPS，给出警告
            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"⚠️ 流模式采样率过高（{self.sample_rate/1e6:.2f}MSPS > 2MSPS）\n"
                    f"   可能导致：1.上位机绘图性能下降  2.UDP丢包\n"
                    f"   建议：切换到Buffer模式或降低采样率"
                )

        # 🔥 V7.8: 计算并显示流模式输入带宽
        if self.current_mode == "stream":
            # 根据采样定理，每周期至少需要10个点才能重建波形
            input_bandwidth_hz = self.sample_rate / 10
            if input_bandwidth_hz >= 1e6:
                bw_str = f"{input_bandwidth_hz/1e6:.2f} MHz"
            elif input_bandwidth_hz >= 1e3:
                bw_str = f"{input_bandwidth_hz/1e3:.2f} kHz"
            else:
                bw_str = f"{input_bandwidth_hz:.0f} Hz"

            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"📊 采样率={self.sample_rate/1e6:.2f}MSPS, "
                    f"输入带宽≤{bw_str} (每周期10点)"
                )
        else:
            # Buffer模式：显示时间窗口信息
            if self.max_display_points > 0:
                time_window_us = (self.max_display_points / self.sample_rate) * 1e6
                if self.serial_manager:
                    self.serial_manager.log_message.emit(
                        f"📊 采样率={self.sample_rate/1e6:.2f}MSPS, "
                        f"时间窗口={time_window_us:.1f}us"
                    )

        # 更新实际参数显示
        self.update_actual_params_display()

        # 🔥 V7.8: 更新流模式带宽显示
        if self.current_mode == "stream":
            self._update_stream_bandwidth_display()

        # 发送到FPGA
        if (
            self.serial_manager
            and self.serial_manager.is_connected()
            and self.is_capturing
        ):
            import struct

            payload = struct.pack("<I", self.div_factor)
            self.serial_manager.send_command(0x26, payload)

    @Slot()
    def on_sample_depth_changed(self):
        """采样深度修改（🔥 V8.7.26: 直接从SAMPLE_DEPTHS数组读取）"""
        depth_index = self.sample_depth_combo.currentIndex()

        # 🔥 V8.7.26: 直接从数组读取深度值，确保与定义一致
        if 0 <= depth_index < len(AdaptiveSamplingCalculator.SAMPLE_DEPTHS):
            self.buffer_size = AdaptiveSamplingCalculator.SAMPLE_DEPTHS[depth_index]
        else:
            # 默认4K
            self.buffer_size = 4096

        # 🔥 V7.7: 同步更新显示深度,避免显示时间长度不匹配
        self.max_display_points = self.buffer_size

        self.serial_manager.log_message.emit(
            f"📊 显示深度={self.max_display_points:,}点 "
            f"(时间窗口={(self.max_display_points/self.sample_rate*1e6):.1f}us)"
        )

        # 更新实际参数显示
        self.update_actual_params_display()

        # 发送到FPGA
        if (
            self.serial_manager
            and self.serial_manager.is_connected()
            and self.is_capturing
        ):
            import struct

            payload = struct.pack("<I", self.buffer_size)
            self.serial_manager.send_command(0x21, payload)
            self.serial_manager.log_message.emit(f"📊 设置采样深度={self.buffer_size}")

    def update_actual_params_display(self):
        """🔥 V8.7.24: 更新实际参数显示（优化格式和逻辑）"""
        # 格式化采样率
        if self.sample_rate >= 1e6:
            rate_str = f"{self.sample_rate/1e6:.2f}M"
        elif self.sample_rate >= 1e3:
            rate_str = f"{self.sample_rate/1e3:.1f}K"
        else:
            rate_str = f"{self.sample_rate:.0f}"

        # Buffer模式显示深度和包数
        if self.buffer_size is not None and self.buffer_size > 0:
            # 格式化深度（二进制1024基准）
            if self.buffer_size >= 1048576:  # 1M
                depth_str = f"{self.buffer_size/1048576:.1f}M"
            elif self.buffer_size >= 1024:  # 1K
                depth_str = f"{self.buffer_size/1024:.0f}K"
            else:
                depth_str = f"{self.buffer_size}"

            # 计算时间窗口
            time_window = self.buffer_size / self.sample_rate
            if time_window >= 1:
                time_str = f"{time_window:.2f}s"
            elif time_window >= 0.001:
                time_str = f"{time_window*1000:.1f}ms"
            else:
                time_str = f"{time_window*1000000:.0f}μs"

            # 计算预期包数（基于实际深度）
            # 🔥 V8.7.53: FPGA已在total_packets计算时+1，上位机接收FPGA发送的所有包
            # 策略：FPGA多发1包，上位机丢弃最后1包，避免最后一包数据异常
            expected_packets = self.buffer_size >> 9  # ÷512 (FPGA已+1)

            self.actual_params_label.setText(
                f"实际: {rate_str}SPS × {depth_str}点 = {time_str} (上位机{expected_packets}包)"
            )
        else:
            # 流模式只显示采样率
            self.actual_params_label.setText(f"实际: {rate_str}SPS (流模式)")

    @Slot()
    def show_context_menu(self, pos):
        """显示右键菜单"""
        menu = QMenu(self)

        # 清空显示动作
        clear_action = menu.addAction("🗑️ 清空显示")
        clear_action.triggered.connect(self.clear_display)

        # 在鼠标位置显示菜单
        menu.exec_(self.plot_widget.mapToGlobal(pos))

    @Slot()
    def clear_display(self):
        """清空波形显示和测量数据"""
        # 🔥 V7.1: 清空环形缓冲区
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()

        # 清空波形显示
        self.ch1_curve.setData([], [])
        self.ch2_curve.setData([], [])

        # 清空测量参数显示
        self.ch1_vpp_label.setText("-- V")
        self.ch1_vrms_label.setText("-- V")
        self.ch1_vmax_label.setText("-- V")
        self.ch1_vmin_label.setText("-- V")
        self.ch1_freq_label.setText("-- Hz")

        self.ch2_vpp_label.setText("-- V")
        self.ch2_vrms_label.setText("-- V")
        self.ch2_vmax_label.setText("-- V")
        self.ch2_vmin_label.setText("-- V")
        self.ch2_freq_label.setText("-- Hz")

        # 重置X轴范围标志
        self.fixed_xaxis_range = None
        self.need_adjust_xaxis = False

        self.serial_manager.log_message.emit("🗑️ 已清空显示")

    @Slot()
    def on_toggle_capture(self):
        """切换启动/停止状态（🔥 V7.5: 流/Buffer模式分离）"""
        if self.is_capturing:
            # 当前正在采集，执行停止
            self.stop_capture()
        else:
            # 当前停止状态，执行启动
            if self.current_mode == "stream":
                # 🔥 流模式：直接启动，不需要自动/手动区分
                self.start_stream_capture()
            else:
                # 🔥 Buffer模式：使用自适应采集（区分自动/手动）
                self.start_adaptive_capture()

    @Slot()
    def start_stream_capture(self):
        """🔥 流模式启动：自适应抽取系数算法"""
        if not self.serial_manager or not self.serial_manager.is_connected():
            return

        # 清空缓冲区
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()

        # 🔥 流模式自适应逻辑：先测频，自动计算最优抽取系数
        self.serial_manager.log_message.emit("📡 [流模式] 测频并计算最优抽取系数...")
        self.auto_measuring = True
        self.toggle_btn.setEnabled(False)
        self.toggle_btn.setText("⏳ 测频中...")

        # 发送频率测量请求
        if self.serial_manager.request_frequency_measurement():
            # 启动超时保护(5秒)
            QTimer.singleShot(5000, self.on_adaptive_stream_timeout)
        else:
            self.auto_measuring = False
            self.toggle_btn.setEnabled(True)
            self.toggle_btn.setText("▶ 启动")
            self.serial_manager.log_message.emit("❌ 发送测频命令失败")

    @Slot()
    def on_adaptive_stream_timeout(self):
        """流模式自适应测频超时处理"""
        if self.auto_measuring:
            self.auto_measuring = False
            self.toggle_btn.setEnabled(True)
            self.toggle_btn.setText("▶ 启动")
            self.serial_manager.log_message.emit("⚠️ [流模式] 测频超时，请检查信号连接")

    @Slot()
    def start_adaptive_capture(self):
        """
        启动采集流程（支持手动/自动模式）:

        手动模式：
        1. 直接使用UI设置的采样率和深度
        2. 配置FPGA并启动采集
        3. 后台持续测频显示（不影响采样参数）

        自动模式：
        1. 发送频率测量请求(0x27)到FPGA
        2. 等待CH340返回频率
        3. 根据频率自动计算最优采样参数
        4. 配置采样率和深度
        5. 启动采集
        """
        if not self.serial_manager or not self.serial_manager.is_connected():
            return

        # 检查是否手动模式
        is_manual = self.buffer_mode_combo.currentIndex() == 1  # 0=自动, 1=手动

        if is_manual:
            # 🔥 V8.7.54修复：手动模式直接使用UI参数启动（不测频）
            self.serial_manager.log_message.emit(
                "🔧 [手动模式] 使用自定义参数启动采集..."
            )

            # 🔥 关键修复：在启动前强制读取一次UI参数，确保内部变量同步
            # 场景：用户先点Auto（触发stop），然后切换到手动，UI的ComboBox已改变
            # 但内部self.sample_rate/div_factor/buffer_size可能还是旧值
            # 解决：显式调用on_sample_rate_changed()和on_sample_depth_changed()同步参数
            if hasattr(self, "sample_rate_combo") and hasattr(
                self, "sample_depth_combo"
            ):
                # 强制同步一次（如果已经同步过也无妨）
                self.on_sample_rate_changed()
                self.on_sample_depth_changed()

            # 显示当前参数（确认参数已正确加载）
            self.update_actual_params_display()
            self.serial_manager.log_message.emit(
                f"📊 采样率={self.sample_rate/1e6:.2f}MSPS (div={self.div_factor}), "
                f"深度={self.buffer_size if self.buffer_size else 'N/A'}点"
            )

            # 直接启动采集（延迟100ms让日志显示）
            self.toggle_btn.setEnabled(False)
            self.toggle_btn.setText("⏳ 配置中...")
            QTimer.singleShot(100, self.apply_manual_params_and_start)

        else:
            # 自动模式：主动测频（0x27命令）
            # 🔥 V8.6.22: 优先使用已有的后台测频数据（使用已启用通道的频率）
            freq_ch1 = (
                self.fpga_measured_freq
                if hasattr(self, "fpga_measured_freq") and self.fpga_measured_freq
                else 0
            )
            freq_ch2 = (
                self.fpga_measured_freq_ch2
                if hasattr(self, "fpga_measured_freq_ch2")
                and self.fpga_measured_freq_ch2
                else 0
            )

            has_valid_freq = False
            freq = 0
            if self.ch1_enabled and freq_ch1 > 0:
                has_valid_freq = True
                freq = freq_ch1
            if self.ch2_enabled and freq_ch2 > 0:
                has_valid_freq = True
                freq = max(freq, freq_ch2)  # 使用最高频率

            if has_valid_freq:
                # 🚀 快速路径：使用已有的后台测频数据（测量面板显示的频率）
                # 格式化显示频率
                if freq >= 1e6:
                    freq_str = f"{freq/1e6:.6f} MHz"
                elif freq >= 1e3:
                    freq_str = f"{freq/1e3:.3f} kHz"
                else:
                    freq_str = f"{freq:.0f} Hz"

                self.serial_manager.log_message.emit(
                    f"🤖 [自动模式] 使用已测频率: {freq_str}"
                )

                # 直接计算参数并启动
                self.last_measured_freq = freq
                self.adaptive_params = AdaptiveSamplingCalculator.calculate(
                    signal_freq=freq,
                    target_periods=self.target_periods,
                    min_points_per_period=20,
                    max_points_per_period=40,
                )

                if self.adaptive_params:
                    # 显示警告信息（如果有）
                    if (
                        "warning" in self.adaptive_params
                        and self.adaptive_params["warning"]
                    ):
                        self.serial_manager.log_message.emit(
                            f"⚠️ {self.adaptive_params['warning']}"
                        )

                    self.update_adaptive_params_display()
                    self.toggle_btn.setEnabled(False)
                    self.toggle_btn.setText("⏳ 配置中...")
                    QTimer.singleShot(100, self.apply_adaptive_params_and_start)
                else:
                    self.serial_manager.log_message.emit(
                        f"❌ 无法计算采样参数（频率={freq} Hz）"
                    )
            else:
                # 🔄 标准路径：发送0x27命令主动测频
                # 🔥 V8.7.54: 先重置状态机，防止上次超时残留
                self.serial_manager.freq_response_state = "IDLE"
                self.serial_manager.freq_data_buffer = b""

                # 🔥 V8.7.54: 取消之前的超时定时器(如果有)
                if hasattr(self, "_freq_timeout_timer") and self._freq_timeout_timer:
                    try:
                        self._freq_timeout_timer.stop()
                    except:
                        pass

                self.auto_measuring = True
                self.serial_manager.log_message.emit("🤖 [自动模式] 开始测频...")

                # 禁用按钮，显示测频中
                self.toggle_btn.setEnabled(False)
                self.toggle_btn.setText("⏳ 测频中...")

                # 发送频率测量请求
                if self.serial_manager.request_frequency_measurement():
                    self.serial_manager.log_message.emit("📡 步骤1/3: 正在测量频率...")

                    # 🔥 V8.7.54修复：超时时间改为3秒(FPGA测频需要1秒门控+传输时间)
                    # 保存定时器引用，方便取消
                    self._freq_timeout_timer = QTimer()
                    self._freq_timeout_timer.setSingleShot(True)
                    self._freq_timeout_timer.timeout.connect(self.on_adaptive_timeout)
                    self._freq_timeout_timer.start(3000)
                else:
                    self.auto_measuring = False
                    self.toggle_btn.setEnabled(True)
                    self.toggle_btn.setText("▶ 启动")
                    self.serial_manager.log_message.emit("❌ 发送频率测量命令失败")

    def on_adaptive_timeout(self):
        """自适应采集超时处理 - 提示失败"""
        # 🔥 V8.7.54: 清除定时器引用
        if hasattr(self, "_freq_timeout_timer"):
            self._freq_timeout_timer = None

        # 检查是否已经在采集中（说明测频成功并已启动采集）
        if self.is_capturing:
            self.serial_manager.log_message.emit(
                "ℹ️ [调试] 超时检查：采集已启动，忽略超时"
            )
            return  # 已经启动采集，忽略超时

        # 检查是否还在等待测频
        if self.auto_measuring and self.serial_manager.freq_response_state != "IDLE":
            self.auto_measuring = False
            self.serial_manager.freq_response_state = "IDLE"  # 重置状态
            self.serial_manager.freq_data_buffer = b""
            self.serial_manager.log_message.emit(
                "❌ 频率测量超时（3秒无响应），请检查：\n"
                "   1. FPGA是否已烧录最新固件\n"
                "   2. 信号是否连接到ADC通道\n"
                "   3. CH340串口连接是否正常"
            )

            # 恢复按钮状态
            self.toggle_btn.setEnabled(True)
            self.toggle_btn.setText("▶ 启动")

    @Slot(bytes)
    def on_frequency_data_received(self, data):
        """
        接收FPGA频率数据后的处理（V2.0：支持双通道）

        三种模式：
        1. 启动测频：首次启动时测频并配置参数
        2. 持续更新：采集过程中FPGA持续测频，上位机只更新显示
        3. 重新配置：Auto按钮触发，重新测频并重新配置参数（不停止采集）
        """
        import struct

        try:
            # V2.0：解析8字节双通道频率 (CH1 4字节 + CH2 4字节)
            if len(data) < 8:
                self.serial_manager.log_message.emit(
                    f"⚠️ 频率数据长度不足: {len(data)} < 8"
                )
                return

            freq_ch1 = struct.unpack("<I", data[0:4])[0]
            freq_ch2 = struct.unpack("<I", data[4:8])[0]

            self.fpga_measured_freq = freq_ch1
            self.fpga_measured_freq_ch2 = freq_ch2

            # 🔥 V8.7.54: 成功收到频率数据，取消超时定时器
            if hasattr(self, "_freq_timeout_timer") and self._freq_timeout_timer:
                try:
                    self._freq_timeout_timer.stop()
                    self._freq_timeout_timer = None
                except:
                    pass

            # 格式化显示CH1频率
            if freq_ch1 >= 1e6:
                freq_str_ch1 = f"{freq_ch1/1e6:.6f} MHz"
            elif freq_ch1 >= 1e3:
                freq_str_ch1 = f"{freq_ch1/1e3:.3f} kHz"
            else:
                freq_str_ch1 = f"{freq_ch1:.0f} Hz"

            # 格式化显示CH2频率
            if freq_ch2 >= 1e6:
                freq_str_ch2 = f"{freq_ch2/1e6:.6f} MHz"
            elif freq_ch2 >= 1e3:
                freq_str_ch2 = f"{freq_ch2/1e3:.3f} kHz"
            else:
                freq_str_ch2 = f"{freq_ch2:.0f} Hz"

            # 🔥 判断是否频率变化显著（仅检查CH1）
            freq_changed = False
            if self.last_measured_freq is not None and self.last_measured_freq > 0:
                change_ratio = (
                    abs(freq_ch1 - self.last_measured_freq) / self.last_measured_freq
                )
                if change_ratio > self.freq_change_threshold:
                    freq_changed = True
                    freq_str_ch1 += f" 🔄"  # 频率变化标记
                    # 仅在频率显著变化时输出日志
                    self.serial_manager.log_message.emit(
                        f"📊 [以太网] 频率更新: CH1={self.last_measured_freq} Hz → {freq_ch1} Hz (变化{change_ratio*100:.1f}%)"
                    )

            # 🎯 V5.12修复：频率显示始终更新（FPGA后台持续测频）
            # 无论是否在采集，只要收到新频率数据就更新显示
            # 这样用户可以实时看到信号频率变化
            # 🔥 V8.6.5: 只更新已启用通道的频率，禁用通道显示"-- Hz"
            if self.ch1_enabled:
                self.ch1_freq_label.setText(freq_str_ch1)
            else:
                self.ch1_freq_label.setText("-- Hz")

            if self.ch2_enabled:
                self.ch2_freq_label.setText(freq_str_ch2)
            else:
                self.ch2_freq_label.setText("-- Hz")

            # 🔥 V5.16性能优化：移除频繁日志（频率已正常更新到界面）
            # 静默更新，不刷屏日志

            # 🔥 模式1: 启动时的自动测频流程
            if self.auto_measuring:
                self.auto_measuring = False
                # 🔥 V8.6.19: 记录有效通道的频率（优先CH1，其次CH2）
                if self.ch1_enabled and freq_ch1 > 0:
                    self.last_measured_freq = freq_ch1
                elif self.ch2_enabled and freq_ch2 > 0:
                    self.last_measured_freq = freq_ch2
                else:
                    self.last_measured_freq = 0

                self.serial_manager.log_message.emit(
                    f"✅ 测得频率 CH1={freq_str_ch1}, CH2={freq_str_ch2}"
                )

                # 🔥 V8.6.19修复: 检查已启用通道是否有有效频率
                # Bug: 之前只检查CH1,导致单独启用CH2时无法启动
                # 修复: 检查已启用通道中是否至少有一个有效信号
                enabled_channels_have_signal = False
                if self.ch1_enabled and freq_ch1 > 0:
                    enabled_channels_have_signal = True
                if self.ch2_enabled and freq_ch2 > 0:
                    enabled_channels_have_signal = True

                if not enabled_channels_have_signal:
                    # 所有已启用通道都没有信号
                    self.toggle_btn.setEnabled(True)
                    self.toggle_btn.setText("▶ 启动")
                    self.toggle_btn.setStyleSheet("")

                    # 根据启用的通道给出具体提示
                    if self.ch1_enabled and self.ch2_enabled:
                        msg = "❌ CH1和CH2都测得频率为0 Hz，请检查：\n   1. 信号源是否已连接到对应ADC通道\n   2. 信号幅度是否足够（建议1-5Vpp）\n   3. 信号是否为交流信号（直流信号无法测频）"
                    elif self.ch1_enabled:
                        msg = "❌ CH1测得频率为0 Hz，请检查：\n   1. 信号源是否已连接到ADC通道1 (CH1)\n   2. 信号幅度是否足够（建议1-5Vpp）\n   3. 信号是否为交流信号（直流信号无法测频）"
                    else:  # ch2_enabled
                        msg = "❌ CH2测得频率为0 Hz，请检查：\n   1. 信号源是否已连接到ADC通道2 (CH2)\n   2. 信号幅度是否足够（建议1-5Vpp）\n   3. 信号是否为交流信号（直流信号无法测频）"

                    self.serial_manager.log_message.emit(msg)
                    return

                # 🔥 流模式和Buffer模式使用不同的自适应算法
                if self.current_mode == "stream":
                    # ========== 流模式自适应算法 V8.6 ==========
                    # 目标：根据信号频率自适应调整采样率，平衡质量和性能
                    # 🔥 核心原则：
                    #    1. 波形质量: 20点/周期 (保证清晰)
                    #    2. 显示窗口: 10K点,确保低频能显示多个周期
                    #    3. 上位机限制: 双通道总流量≤4MSPS, 单通道≤2MSPS (提升性能)
                    #    4. 输入频率: 适应频率范围 100Hz-100kHz
                    base_freq = 50000000  # 50MHz固定ADC采样率

                    # 🔥 V8.6.22: 双通道带宽限制优化 - 考虑PyQt绘图性能
                    # 实际输入限制: 最高100kHz (用户实际使用场景)
                    # 🔥 V8.6.41: UDP性能限制 - 纯Python接收极限3500 pps
                    # 实测数据:
                    #   - 双50kHz: 1984 pps → 0%丢包 ✅
                    #   - 单100kHz: 3968 pps → <1%丢包 ✅
                    #   - 双80kHz: 6388 pps → 43%丢包 ❌
                    # PyQt绘图性能: 刷新10K点约需50-100ms
                    dual_channel = self.ch1_enabled and self.ch2_enabled
                    if dual_channel:
                        # 双通道: 每通道最高50kHz (总流量2MSPS)
                        # 包速率: 2M / 504 ≈ 3968 pps (接近极限但可用)
                        max_total_rate = 2000000  # 双通道总流量2MSPS
                        max_effective_rate = 1000000  # 每通道最大1MSPS (50kHz×20点)
                        max_input_bandwidth = 50000  # 双通道: 50kHz
                    else:
                        # 单通道: 最高100kHz (2MSPS)
                        # 包速率: 2M / 504 ≈ 3968 pps (接近极限)
                        max_total_rate = 2000000  # 单通道总流量2MSPS
                        max_effective_rate = 2000000  # 单通道: 2MSPS (100kHz×20点)
                        max_input_bandwidth = 100000  # 单通道: 100kHz

                    min_points_per_period = 15  # 最少15点/周期
                    target_points_per_period = 20  # 目标20点/周期

                    # 🔥 V8.6.6修复: 双通道模式下独立计算每个通道的最优采样率
                    # Bug原因: 简单取max(freq_ch1, freq_ch2)导致低频通道点数不足
                    # 例: CH1=10k, CH2=100k → 按100k配置 → CH1只有22点/周期（勉强）
                    #
                    # 新策略: 为每个启用的通道独立计算理想div，取最小值（最高采样率）
                    # 这样确保两个通道都有足够点数

                    ideal_divs = []
                    max_freq = max(
                        freq_ch1 if self.ch1_enabled else 0,
                        freq_ch2 if self.ch2_enabled else 0,
                    )
                    if max_freq == 0:
                        max_freq = 1

                    if self.ch1_enabled and freq_ch1 > 0:
                        # CH1的理想抽取系数
                        div_ch1 = int(base_freq / (freq_ch1 * target_points_per_period))
                        ideal_divs.append(div_ch1)

                    if self.ch2_enabled and freq_ch2 > 0:
                        # CH2的理想抽取系数
                        div_ch2 = int(base_freq / (freq_ch2 * target_points_per_period))
                        ideal_divs.append(div_ch2)

                    if not ideal_divs:
                        # 异常情况：没有启用的通道
                        ideal_div = int(
                            base_freq / (max_freq * target_points_per_period)
                        )
                    else:
                        # 取最小div（最高采样率），确保两个通道都满足要求
                        ideal_div = min(ideal_divs)

                    # 🔥 V8.6.23修复: 彻底解决双通道高低频组合不连续问题
                    # Bug根源: min_div_for_performance强制提高div导致采样率降低
                    # 例: CH1=10k+CH2=100k → ideal_div=25(2MSPS) → 但min_div=26(1.92M) → 网络抖动
                    #
                    # 本质问题:
                    #   1. ideal_div已经选择了min(div_ch1, div_ch2) = 最高采样率
                    #   2. min_div_for_performance不应该再强制降低采样率！
                    #   3. 性能限制应该只防止"过度过采样"，而非降低必要采样率
                    #
                    # 新策略:
                    #   - ideal_div已经保证了高频通道的采样率（20点/周期）
                    #   - 只需检查是否超出max_effective_rate，超出则限制
                    #   - 不再使用min_div_for_performance强制提高div

                    # 计算理想采样率
                    ideal_sample_rate = base_freq / ideal_div

                    # 性能限制：只在超出上限时才限制
                    if ideal_sample_rate > max_effective_rate:
                        # 超出性能限制，计算最小允许的div
                        optimal_div = int(base_freq / max_effective_rate)
                    else:
                        # 未超限，直接使用理想div
                        optimal_div = (
                            ideal_div  # 🔥 V8.6.6: 智能采样率策略 - 平衡质量和性能
                        )
                    # 低频信号问题：过采样导致上位机负担重，显示不流畅
                    # 新策略：
                    #   - 低频(<100Hz): 保持20点/周期，不过采样，提升流畅度
                    #   - 中频(100Hz-10kHz): 正常20点/周期
                    #   - 高频(>10kHz): 根据带宽限制自适应

                    # 🔥 V8.6.7: 根据频率动态调整最低采样率（平衡UDP发包频率和数据量）
                    # FIFO阈值1008字节=504样本，目标：0.5秒发一次包 → 需要1kHz采样率
                    if max_freq <= 5:  # 超低频信号 (1Hz-5Hz)
                        # 使用较低的采样率，减少数据量
                        # 1Hz: 100点/周期，1kHz采样 → 0.5秒发包
                        min_sample_rate = max_freq * 100  # 100点/周期
                        min_sample_rate = max(1000, min_sample_rate)  # 最低1kHz
                    elif max_freq <= 100:  # 低频信号 (5Hz-100Hz)
                        # 保持20点/周期，避免过采样
                        min_sample_rate = max_freq * target_points_per_period
                        # 最低1kHz（504÷1000=0.5秒发包，流畅度可接受）
                        min_sample_rate = max(1000, min_sample_rate)
                    else:
                        # 中高频信号，无特殊限制
                        min_sample_rate = 0  # 不限制

                    # 🔥 V8.6.23: 限制最大div（避免低频信号过采样）
                    # 这个限制只针对超低频信号（<100Hz），防止采样率过低导致发包延迟
                    max_div_for_min_rate = (
                        int(base_freq / min_sample_rate)
                        if min_sample_rate > 0
                        else 999999999
                    )

                    # 最终div：只限制最大值（防止过采样），不强制提高
                    optimal_div = min(optimal_div, max_div_for_min_rate)

                    # 计算实际有效采样率和每周期点数
                    effective_rate = base_freq / optimal_div
                    points_ch1 = effective_rate / freq_ch1 if freq_ch1 > 0 else 20
                    points_ch2 = effective_rate / freq_ch2 if freq_ch2 > 0 else 20

                    # 🔥 V8.6.16: 双通道显示窗口优化
                    # Bug: actual_points_per_period = min() 导致低频通道显示不完整
                    # 修复: 显示窗口按max()计算，确保低频信号有足够空间
                    actual_points_per_period = min(
                        points_ch1, points_ch2
                    )  # 用于质量检查（最严格）
                    display_points_per_period = max(
                        points_ch1, points_ch2
                    )  # 用于显示窗口（最宽松）

                    # 🔥 V8.6.24: 检查双通道频率比例（FPGA交织器限制）
                    # 硬件限制：双通道交织器要求两个通道都ready才输出
                    # 当频率相差过大时（>5倍），低频通道样本会被覆盖导致跳变
                    freq_ratio_warning = None
                    if (
                        self.ch1_enabled
                        and self.ch2_enabled
                        and freq_ch1 > 0
                        and freq_ch2 > 0
                    ):
                        freq_ratio = max(freq_ch1, freq_ch2) / min(freq_ch1, freq_ch2)
                        if freq_ratio > 5.0:
                            freq_ratio_warning = (
                                f"⚠️ 双通道频率差异过大 ({freq_ratio:.1f}倍)\n"
                                f"   硬件限制：FPGA交织器可能导致低频通道波形跳变\n"
                                f"   建议：\n"
                                f"   1. 使用频率相近的信号（比例<5倍）\n"
                                f"   2. 或分别使用单通道模式测试"
                            )

                    # 🔥 检查波形质量
                    warning_msg = None
                    if freq_ratio_warning:
                        # 优先显示频率比例警告
                        warning_msg = freq_ratio_warning
                    elif actual_points_per_period < min_points_per_period:
                        warning_msg = (
                            f"⚠️ 信号频率过高: CH1={freq_ch1/1e3:.1f}kHz, CH2={freq_ch2/1e3:.1f}kHz\n"
                            f"   CH1: {points_ch1:.1f}点/周期, CH2: {points_ch2:.1f}点/周期\n"
                            f"   流模式限制: {'双通道≤50kHz' if dual_channel else '单通道≤100kHz'}\n"
                            f"   建议切换到Buffer模式以获得50MSPS采样率"
                        )
                    elif max_freq > max_input_bandwidth * 0.85:  # 接近上限(85%)
                        warning_msg = (
                            f"💡 信号频率接近流模式上限\n"
                            f"   CH1: {freq_ch1/1e3:.1f}kHz ({int(points_ch1)}点/周期)\n"
                            f"   CH2: {freq_ch2/1e3:.1f}kHz ({int(points_ch2)}点/周期)\n"
                            f"   当前质量良好,若需更高质量可切换Buffer模式"
                        )

                    # 🔥 V8.6.16: 动态显示窗口优化 - 双通道按最大点数/周期计算
                    # 使用display_points_per_period确保低频通道有足够显示空间
                    if max_freq <= 5:  # 超低频信号 (1Hz-5Hz)
                        # 10秒窗口避免缓冲区溢出
                        target_time_seconds = 10
                        dynamic_buffer_size = int(effective_rate * target_time_seconds)
                        dynamic_buffer_size = max(5000, min(20000, dynamic_buffer_size))
                        target_display_periods = (
                            int(dynamic_buffer_size / display_points_per_period)
                            if display_points_per_period > 0
                            else 10
                        )
                    elif max_freq <= 1000:  # 低频信号 (5Hz-1kHz)
                        # 15个周期显示窗口
                        target_display_periods = 15
                        dynamic_buffer_size = int(
                            display_points_per_period * target_display_periods
                        )
                        # 限制在1000-20000之间（扩大上限，适应低频通道）
                        dynamic_buffer_size = max(1000, min(20000, dynamic_buffer_size))
                    else:
                        # 中高频信号(>1kHz)：动态窗口,确保双通道都有足够显示空间
                        # 🔥 V8.6.17: 修复100k+10k组合时低频通道显示不完整
                        # 按display_points_per_period(max值)计算,确保低频通道有15周期
                        target_display_periods = 15
                        dynamic_buffer_size = int(
                            display_points_per_period * target_display_periods
                        )
                        # 限制在5000-20000之间
                        dynamic_buffer_size = max(5000, min(20000, dynamic_buffer_size))

                    self.adaptive_params = {
                        "div_factor": optimal_div,
                        "sample_rate": effective_rate,
                        "sample_depth": 1008,  # 流模式FIFO阈值(固定1008字节)
                        "buffer_size": dynamic_buffer_size,  # 🔥 动态缓冲区大小
                        "input_bandwidth": max_input_bandwidth,
                        "points_per_period": int(actual_points_per_period),
                        "actual_periods": 0,
                        "display_periods": target_display_periods,
                    }

                    channel_mode = "双通道" if dual_channel else "单通道"
                    # 🔥 V8.6.9: 检测低频优化模式
                    is_ultra_low_freq = max_freq <= 5
                    is_low_freq_mode = 5 < max_freq <= 1000  # 扩大到1kHz

                    log_msg = f"📊 [流模式自适应 V8.6.24 {channel_mode}]\n"
                    log_msg += (
                        f"   CH1频率: {freq_str_ch1} ({int(points_ch1)}点/周期)\n"
                    )
                    log_msg += (
                        f"   CH2频率: {freq_str_ch2} ({int(points_ch2)}点/周期)\n"
                    )

                    # 🔥 显示频率比例警告
                    if dual_channel and freq_ch1 > 0 and freq_ch2 > 0:
                        freq_ratio = max(freq_ch1, freq_ch2) / min(freq_ch1, freq_ch2)
                        if freq_ratio > 5.0:
                            log_msg += f"   ⚠️ 频率比例: {freq_ratio:.1f}倍 (建议<5倍避免跳变)\n"
                        else:
                            log_msg += f"   ✅ 频率比例: {freq_ratio:.1f}倍 (良好)\n"

                    log_msg += f"   🔥 理想div: {ideal_div}, 实际div: {optimal_div}\n"
                    log_msg += f"   有效采样率: {effective_rate/1e6:.3f} MSPS ({effective_rate:.0f} Hz)\n"

                    if is_ultra_low_freq:
                        log_msg += f"   🎯 超低频优化: 显示10秒窗口 ({dynamic_buffer_size}点)\n"
                    elif is_low_freq_mode:
                        log_msg += f"   🎯 低频优化: 显示{target_display_periods}周期 ({dynamic_buffer_size}点)\n"
                    else:
                        log_msg += f"   📊 显示窗口: {dynamic_buffer_size}点 ({target_display_periods}周期)\n"

                    log_msg += f"   波形质量: {'✅ 优秀' if actual_points_per_period >= 20 else '⚠️ 良好' if actual_points_per_period >= 15 else '❌ 较差'}\n"
                    log_msg += f"   输入带宽: ≤{max_input_bandwidth/1e3:.0f} kHz\n"

                    # 🔥 V8.6.39: UDP性能限制检测
                    total_flow_msps = (
                        effective_rate * 2 / 1e6
                        if dual_channel
                        else effective_rate / 1e6
                    )
                    udp_packet_rate = (
                        effective_rate * (2 if dual_channel else 1)
                    ) / 504  # 504样本/包
                    log_msg += f"   🔥 总流量: {total_flow_msps:.3f} MSPS\n"
                    log_msg += f"   📡 UDP包速率: {udp_packet_rate:.0f} pps"

                    # UDP性能警告（实测阈值：3500 pps）
                    if udp_packet_rate > 3500:
                        warning_msg = (
                            f"\n⚠️⚠️⚠️ UDP性能警告 ⚠️⚠️⚠️\n"
                            f"   包速率: {udp_packet_rate:.0f} pps (超过性能极限3500 pps)\n"
                            f"   预计丢包率: >3%\n"
                            f"   症状: 波形跳变、数据不连续\n"
                            f"   建议:\n"
                            f"   1. 降低信号频率（<50kHz双通道）\n"
                            f"   2. 使用单通道模式\n"
                            f"   3. 切换到Buffer模式（无UDP限制）\n"
                        )

                    self.serial_manager.log_message.emit(log_msg)

                    if warning_msg:
                        self.serial_manager.log_message.emit(warning_msg)

                else:
                    # ========== Buffer模式自适应算法 V8.7.17 ==========
                    # 🔥 V8.7.17优化：双通道时平衡高低频信号显示
                    #
                    # 策略：
                    #   1. 采样率：根据较高频率计算（确保高频信号质量）
                    #   2. 采样深度：兼顾低频信号周期数要求
                    #
                    # 示例：CH1=100kHz, CH2=10kHz
                    #   - 采样率：按100kHz计算 → 50MSPS（固定）
                    #   - 深度选择：确保10kHz也能显示≥6个周期
                    #     100kHz: 50MSPS/100kHz=500点/周期，4K深度=8周期 ✅
                    #     10kHz:  50MSPS/10kHz=5000点/周期，4K深度=0.8周期 ❌
                    #   - 优化深度：10kHz需要6×5000=30K点，但超DDR限制
                    #   - 折中方案：选择合理深度（如10K），让低频显示2周期

                    dual_channel = self.ch1_enabled and self.ch2_enabled

                    if dual_channel and freq_ch1 > 0 and freq_ch2 > 0:
                        # 双通道模式：平衡高低频显示
                        max_freq = max(freq_ch1, freq_ch2)
                        min_freq = min(freq_ch1, freq_ch2)

                        # 1. 根据高频计算采样率
                        high_freq_params = AdaptiveSamplingCalculator.calculate(
                            signal_freq=max_freq,
                            target_periods=self.target_periods,
                            min_points_per_period=20,
                            max_points_per_period=40,
                            min_depth=4096,
                        )

                        if high_freq_params:
                            sample_rate = high_freq_params["sample_rate"]
                            div_factor = high_freq_params["div_factor"]

                            # 2. 计算两个通道的每周期点数
                            points_high = sample_rate / max_freq
                            points_low = sample_rate / min_freq

                            # 3. 计算低频信号需要的深度（至少6个周期）
                            min_periods_low = 6  # 低频信号至少6个周期
                            depth_for_low = int(points_low * min_periods_low)

                            # 4. 计算高频信号需要的深度
                            depth_for_high = int(points_high * self.target_periods)

                            # 5. 选择合适深度（取较大值，但限制在合理范围）
                            target_depth = max(depth_for_low, depth_for_high)

                            # 限制深度范围：1K-100K (二进制)
                            target_depth = max(1024, min(102400, target_depth))

                            # 6. 对齐到标准深度 (🔥 V8.7.24: 统一使用二进制1024倍数)
                            standard_depths = [
                                1024,  # 1K
                                2048,  # 2K
                                4096,  # 4K
                                8192,  # 8K
                                10240,  # 10K
                                20480,  # 20K
                                51200,  # 50K
                                102400,  # 100K
                            ]
                            sample_depth = min(
                                [d for d in standard_depths if d >= target_depth],
                                default=102400,
                            )

                            # 7. 计算实际显示周期数
                            periods_high = sample_depth / points_high
                            periods_low = sample_depth / points_low

                            self.adaptive_params = {
                                "sample_rate": sample_rate,
                                "sample_depth": sample_depth,
                                "div_factor": div_factor,
                                "points_per_period": int(points_high),  # 高频为主
                                "actual_periods": int(periods_high),
                                "display_periods": int(min(periods_high, periods_low)),
                                "warning": None,
                            }

                            # 日志输出
                            freq_str_high = (
                                f"{max_freq/1e3:.2f}kHz"
                                if max_freq >= 1000
                                else f"{max_freq:.2f}Hz"
                            )
                            freq_str_low = (
                                f"{min_freq/1e3:.2f}kHz"
                                if min_freq >= 1000
                                else f"{min_freq:.2f}Hz"
                            )

                            log_msg = f"📊 [Buffer双通道优化 V8.7.17]\n"
                            log_msg += f"   高频: {freq_str_high} → {int(points_high)}点/周期, {int(periods_high)}周期\n"
                            log_msg += f"   低频: {freq_str_low} → {int(points_low)}点/周期, {int(periods_low)}周期\n"
                            log_msg += f"   采样率: {sample_rate/1e6:.2f} MSPS (div={div_factor})\n"
                            log_msg += f"   采样深度: {sample_depth:,}点 (优化选择)\n"
                            log_msg += f"   平衡显示: 高频{int(periods_high)}周期 + 低频{int(periods_low)}周期"

                            self.serial_manager.log_message.emit(log_msg)
                        else:
                            # 高频计算失败，回退到单频率模式
                            target_freq = max_freq
                            self.adaptive_params = AdaptiveSamplingCalculator.calculate(
                                signal_freq=target_freq,
                                target_periods=self.target_periods,
                                min_points_per_period=20,
                                max_points_per_period=40,
                                min_depth=4096,
                            )
                    else:
                        # 单通道模式：使用原有逻辑
                        target_freq = 0
                        if self.ch1_enabled and freq_ch1 > 0:
                            target_freq = freq_ch1
                        if self.ch2_enabled and freq_ch2 > 0:
                            target_freq = max(target_freq, freq_ch2)

                        self.adaptive_params = AdaptiveSamplingCalculator.calculate(
                            signal_freq=target_freq,
                            target_periods=self.target_periods,
                            min_points_per_period=20,
                            max_points_per_period=40,
                            min_depth=4096,
                        )

                if self.adaptive_params:
                    # 更新显示
                    self.update_adaptive_params_display()

                    # 应用参数并启动采集（延迟100ms让界面更新）
                    QTimer.singleShot(100, self.apply_adaptive_params_and_start)
                else:
                    # 计算失败（可能频率超出范围）
                    self.toggle_btn.setEnabled(True)
                    self.toggle_btn.setText("▶ 启动")
                    self.toggle_btn.setStyleSheet("")  # 恢复默认样式
                    self.serial_manager.log_message.emit(
                        f"❌ 无法计算采样参数（频率={freq_ch1} Hz）\n"
                        f"   频率可能超出支持范围（1Hz - 12MHz）"
                    )

            # 🔥 模式2: 重新配置过程中收到测频结果
            elif self.is_reconfiguring:
                self.is_reconfiguring = False
                # 🔥 V8.6.19: 记录有效通道的频率
                if self.ch1_enabled and freq_ch1 > 0:
                    self.last_measured_freq = freq_ch1
                elif self.ch2_enabled and freq_ch2 > 0:
                    self.last_measured_freq = freq_ch2
                else:
                    self.last_measured_freq = 0

                self.serial_manager.log_message.emit(
                    f"✅ [重配置] 测得频率 CH1={freq_str_ch1}, CH2={freq_str_ch2}"
                )

                # 重新计算自适应参数（使用有效通道频率）
                target_freq = self.last_measured_freq
                new_params = AdaptiveSamplingCalculator.calculate(
                    signal_freq=target_freq,
                    target_periods=self.target_periods,
                    min_points_per_period=20,
                    max_points_per_period=40,
                )

                if new_params:
                    self.adaptive_params = new_params
                    self.update_adaptive_params_display()
                    # 🔥 关键：重新配置采样参数
                    self.reconfigure_sampling_params()
                else:
                    self.serial_manager.log_message.emit("❌ [重配置] 参数计算失败")

            # 🔥 模式3: 采集过程中FPGA自动测频更新（静默更新，仅在变化时提示）
            elif self.is_capturing:
                # 🔥 V7.2流模式优化：采集过程中禁用自动重配置
                # 原因：重配置会导致波形出现相位跳变（即使不清空buffer）
                # 解决方案：只允许用户手动按Auto按钮触发重配置

                # 🔥 V8.6.19: 记录有效通道的频率
                current_freq = 0
                if self.ch1_enabled and freq_ch1 > 0:
                    current_freq = freq_ch1
                elif self.ch2_enabled and freq_ch2 > 0:
                    current_freq = freq_ch2

                # 静默更新频率显示
                if self.last_measured_freq is not None and self.last_measured_freq > 0:
                    change_ratio = (
                        abs(current_freq - self.last_measured_freq)
                        / self.last_measured_freq
                    )
                    if change_ratio > self.freq_change_threshold:
                        freq_changed = True
                        change_percent = change_ratio * 100
                        # 只提示频率变化，不自动重配置
                        self.serial_manager.log_message.emit(
                            f"📊 频率变化 {change_percent:.1f}%: "
                            f"{self.last_measured_freq} Hz → {current_freq} Hz "
                            f"(需要重新配置请按Auto按钮)"
                        )

                # 更新频率记录（不触发重配置）
                self.last_measured_freq = current_freq

            # 🔥 模式4: 未在采集状态收到频率数据（静默更新）
            else:
                # 静默更新，不输出日志
                # 🔥 V8.6.19: 记录有效通道的频率
                if self.ch1_enabled and freq_ch1 > 0:
                    self.last_measured_freq = freq_ch1
                elif self.ch2_enabled and freq_ch2 > 0:
                    self.last_measured_freq = freq_ch2

        except Exception as e:
            self.auto_measuring = False
            self.is_reconfiguring = False
            self.toggle_btn.setEnabled(True)
            self.toggle_btn.setText("▶ 启动")
            self.toggle_btn.setStyleSheet("")  # 恢复默认样式
            self.serial_manager.log_message.emit(f"❌ 频率数据解析失败: {e}")

    def update_adaptive_params_display(self):
        """🔥 V8.7.61: 更新自适应参数显示（统一格式，增加时间窗口）"""
        if not self.adaptive_params:
            return

        params = self.adaptive_params

        # 采样率 - 简洁显示
        if params["sample_rate"] >= 1e6:
            rate_str = f"{params['sample_rate']/1e6:.2f}M"
        elif params["sample_rate"] >= 1e3:
            rate_str = f"{params['sample_rate']/1e3:.1f}K"
        else:
            rate_str = f"{params['sample_rate']:.0f}"

        # 采样深度（二进制格式）
        depth = params["sample_depth"]
        if depth >= 1048576:  # 1M
            depth_str = f"{depth/1048576:.1f}M"
        elif depth >= 1024:  # 1K
            depth_str = f"{depth/1024:.0f}K"
        else:
            depth_str = str(depth)

        # 🔥 V8.7.61: 计算时间窗口（与手动模式统一）
        time_window = depth / params["sample_rate"]
        if time_window >= 1:
            time_str = f"{time_window:.2f}s"
        elif time_window >= 0.001:
            time_str = f"{time_window*1000:.1f}ms"
        else:
            time_str = f"{time_window*1000000:.0f}μs"

        # 计算预期包数（与FPGA一致）
        if self.current_mode == "buffer":
            total_bytes = depth * 2
            expected_packets = (total_bytes + 1023) >> 10
            packets_info = f" (上位机{expected_packets}包)"
        else:
            packets_info = ""

        # 🔥 只在自动模式下更新界面显示（统一格式：采样率 × 深度 = 时间）
        if self.buffer_mode_combo.currentIndex() == 0:  # 0=自动
            self.actual_params_label.setText(
                f"实际: {rate_str}SPS × {depth_str}点 = {time_str}{packets_info}"
            )

        # 📊 详细日志
        self.serial_manager.log_message.emit(f"📊 自适应参数计算完成:")
        self.serial_manager.log_message.emit(
            f"  ├─ 采样率: {params['sample_rate']:,} Hz (div={params['div_factor']})"
        )
        self.serial_manager.log_message.emit(
            f"  ├─ 采样深度: {depth:,}点{packets_info}"
        )
        self.serial_manager.log_message.emit(
            f"  └─ 波形质量: {params['points_per_period']}点/周期 × {params['actual_periods']:.1f}周期"
        )

    def start_direct_capture(self):
        """
        直接启动采集（不使用自适应算法，使用当前UI参数）
        ⚠️ 已废弃：所有模式现在都使用自适应算法
        """
        # 重定向到自适应采集
        self.start_adaptive_capture()

    def _continue_start_direct_capture(self):
        """继续启动直接采集（延迟执行部分）- 已废弃"""
        pass  # 不再使用

    def apply_manual_params_and_start(self):
        """应用手动参数并启动采集"""
        import struct

        # 🔥 V8.7.32关键修复: 强制同步current_mode与UI选择
        # 避免用户在程序启动后第一次点击启动时，current_mode还是初始值"stream"
        mode_index = self.mode_combo.currentIndex()
        self.current_mode = "stream" if mode_index == 0 else "buffer"

        # 🔥 V7.1: 清空环形缓冲区 + 重置包对齐标志
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()
        self._last_display_update = 0
        self.need_adjust_xaxis = True  # 🔥 V8.7.51: 手动模式也需要调整X轴
        self.freq_display_locked = False

        # 重置V6.5包对齐状态（强制重新对齐）
        if hasattr(self, "_v65_aligned"):
            delattr(self, "_v65_aligned")
        if hasattr(self, "_v65_first_packet_logged"):
            delattr(self, "_v65_first_packet_logged")
        if hasattr(self, "_v65_rejected_packets"):
            delattr(self, "_v65_rejected_packets")

        # 🔥 V6.7: 重置包交替计数器（关键！）
        if hasattr(self, "_v67_packet_index"):
            delattr(self, "_v67_packet_index")
        if hasattr(self, "_v67_packet_count"):
            delattr(self, "_v67_packet_count")

        if self.serial_manager:
            self.serial_manager.log_message.emit(
                "🔄 [V6.7] 缓冲区已清空，包交替计数器已重置"
            )  # 📡 开始配置FPGA（命令顺序：0x20 → 0x21 → 0x26 → 0x28(新增) → 0x23）
        mode_name = "流模式" if self.current_mode == "stream" else "Buffer模式"
        self.serial_manager.log_message.emit(f"📡 正在配置FPGA参数（{mode_name}）...")

        # 1. 设置采集模式（0=流模式, 1=Buffer模式）- 必须首先设置！
        mode_val = 0 if self.current_mode == "stream" else 1
        import time

        payload_0x20 = bytes([mode_val])
        self.serial_manager.send_command(0x20, payload_0x20)
        time.sleep(0.05)  # 等待50ms

        # 🔥 V8.7.46: 恢复2倍补偿（FPGA只发一半数据）
        # 2. 设置采样深度
        fpga_buffer_size = self.buffer_size * 2  # 发送2倍给FPGA
        payload_0x21 = struct.pack("<I", fpga_buffer_size)
        self.serial_manager.send_command(0x21, payload_0x21)
        time.sleep(0.05)  # 等待50ms

        # 🔥 V8.7.56修复: 预期包数应基于原始深度而非FPGA深度
        # 策略：FPGA多发1包，上位机只用前N包，丢弃最后1包避免数据异常
        #
        # 示例：用户要4096点
        #   上位机：buffer_size=4096, fpga_buffer_size=8192
        #   FPGA DDR3写入：8192个16位数据 = 16384字节 = 16包
        #   上位机实际需要：4096点 = 8192字节 = 8包
        #   上位机预期：buffer_size >> 9 = 8包 ✅
        #   FPGA会多发1包冗余，上位机收到9包时自动停止
        expected_packets = (
            self.buffer_size >> 9
        )  # 基于原始深度：÷512 (用户要的点数对应的包数)

        self.serial_manager.log_message.emit(
            f"  ✓ 采样深度: {self.buffer_size:,}点 (发送{fpga_buffer_size:,}点给FPGA，2倍补偿)"
        )
        self.serial_manager.log_message.emit(
            f"  ✓ 上位机预期: {expected_packets}包 (FPGA会多发1包冗余)"
        )

        # 3. 设置采样率分频系数
        payload_0x26 = struct.pack("<I", self.div_factor)
        self.serial_manager.send_command(0x26, payload_0x26)
        time.sleep(0.05)  # 等待50ms

        # 🔥 V7.5: 立即更新sample_rate变量（关键！防止被adaptive_params覆盖）
        base_freq = 50000000  # 50MHz基准
        self.sample_rate = base_freq / self.div_factor

        # 🔥 V7.5: 流模式和Buffer模式显示窗口逻辑分离
        if self.current_mode == "stream":
            # 🔥 V8.6.6: 流模式动态显示窗口（低频优化）
            # 从adaptive_params读取动态buffer_size
            self.max_display_points = self.adaptive_params.get("buffer_size", 10000)
            self.serial_manager.log_message.emit(
                f"  ✓ 流模式显示窗口: {self.max_display_points:,} 点"
                + (" (低频优化)" if self.max_display_points < 10000 else " (标准)")
            )
        else:
            # Buffer模式：显示窗口=采集深度
            self.max_display_points = self.buffer_size
            self.serial_manager.log_message.emit(
                f"  ✓ Buffer模式显示窗口: {self.max_display_points:,} 点"
            )

        # 🔥 V7.3: 手动模式立即锁定X轴范围（防止X轴跳动！）
        self.fixed_xaxis_range = (
            self.max_display_points / self.sample_rate * 1e6
        )  # 微秒
        self.serial_manager.log_message.emit(
            f"  ✓ X轴锁定: {self.fixed_xaxis_range:.3f} μs"
        )

        # 🔥 4. 设置通道使能（V5.0新增：硬件级控制）
        self.send_channel_enable_command()

        # 🔥 4.5 V8.7.1: 发送触发配置（统一触发系统）
        self.send_trigger_config()

        # 5. 启动以太网UDP接收器（ADC数据通过UDP传输）
        # 🔥 V8.7.14.7: 无论receiver是否运行,都要更新mode(关键修复!)
        self.ethernet_receiver.set_mode(self.current_mode)  # "stream" 或 "buffer"

        import time

        if not self.ethernet_receiver.running:
            self.ethernet_receiver.start()
            # 🔥 关键修复：等待接收器线程完全启动（避免丢失首包）
            time.sleep(0.1)  # 100ms确保接收器线程进入监听状态
            self.serial_manager.log_message.emit(
                f"  ✓ 以太网UDP接收器已启动 (0.0.0.0:6102) [{self.current_mode}模式]"
            )
        else:
            self.serial_manager.log_message.emit(
                f"  ✓ 以太网UDP接收器运行中 (已切换至{self.current_mode}模式)"
            )

        # 🔥 清空数据缓冲区
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()

        # 🔥 V8.7.9修复: 重置自动停止相关标志，确保每次启动都是干净状态
        if hasattr(self, "_auto_stop_triggered"):
            delattr(self, "_auto_stop_triggered")
        if hasattr(self, "_is_auto_stopping"):
            delattr(self, "_is_auto_stopping")
        if hasattr(self, "_dummy_capture_done"):
            delattr(self, "_dummy_capture_done")

        # 🔥 V8.6.2: 重置所有调试和跟踪标志（确保采集状态干净）
        self._last_display_update = 0
        if hasattr(self, "_v70_first_logged"):
            delattr(self, "_v70_first_logged")
        if hasattr(self, "_last_display_time"):
            delattr(self, "_last_display_time")
        if hasattr(self, "_v70_packet_count"):
            delattr(self, "_v70_packet_count")

        # 🔥 V5.14: 重置UDP包相位跟踪（采集开始时从反转相位开始）
        # 根据实际测试：FPGA发送[CH2,CH1,CH2,CH1...] → 奇数索引=CH1
        self.udp_phase_offset = 1  # 第一包从反转相位开始(奇数索引=CH1)

        # 🔥 V8.7.55修复: 手动模式完全复用自动模式的多包策略逻辑
        # 保存预期包数（基于实际深度，不是FPGA配置深度）
        if self.current_mode == "buffer":
            # 🔥 关键：使用expected_packets（已经按V8.7.53规则计算）
            # 规则：fpga_buffer_size >> 9 = FPGA写入点数 / 512
            # FPGA会多发1包，上位机只用前N包，丢弃最后1包
            self.buffer_expected_packets = expected_packets
            self.buffer_received_packets = 0  # 重置计数器

            self.serial_manager.log_message.emit(
                f"  ✓ 多包策略: 预期{expected_packets}包，丢弃最后1包冗余数据"
            )

        # 6. 发送启动采集命令 (0x23) - 这个命令会触发FPGA开始采集和发送数据
        import time

        time.sleep(0.05)  # 等待50ms，确保前面的配置命令都已处理完成
        self.serial_manager.send_command(0x23)
        self.serial_manager.log_message.emit(f"  ✓ 发送启动命令 (0x23)")

        # 🔥 V8.7.34: Buffer模式下可以请求频率测量（可选）
        # 注意：Buffer模式下频率测量已在trigger_capture中完成，这里无需重复

        self.serial_manager.log_message.emit(
            f"📡 等待FPGA发送UDP数据到 192.168.0.3:6102 ..."
        )

        # 更新状态
        self.is_capturing = True
        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("⏸ 停止")
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #d9534f;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #c9302c; }
        """
        )

        # 🔥 V7.2: 手动模式下更新界面实际参数显示
        self.update_actual_params_display()

        self.serial_manager.log_message.emit("✅ [手动模式] 采集已启动")
        self.serial_manager.log_message.emit(
            "💡 提示: FPGA会持续测频并显示，但不影响采样参数"
        )

    def apply_stream_params_and_start(self):
        """流模式专用启动函数（🔥 V7.5新增）"""
        import struct
        import time

        # 🔥 清空环形缓冲区
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()
        self._last_display_update = 0

        # 🔥 V8.6.2: 重置UDP接收状态标志
        if hasattr(self, "_v70_first_logged"):
            delattr(self, "_v70_first_logged")
        if hasattr(self, "_last_display_time"):
            delattr(self, "_last_display_time")
        if hasattr(self, "_v70_packet_count"):
            delattr(self, "_v70_packet_count")

        mode_name = "流模式"
        self.serial_manager.log_message.emit(f"📡 正在配置FPGA参数（{mode_name}）...")

        # 1. 设置采集模式（0=流模式）
        mode_val = 0
        self.serial_manager.send_command(0x20, bytes([mode_val]))
        time.sleep(0.05)
        self.serial_manager.log_message.emit(f"  ✓ 采集模式: {mode_name}")

        # 2. 设置FIFO触发阈值（流模式下用于控制UDP发送时机）
        # 🔥 关键修复:流模式应设置为1008字节(一个UDP包的数据量)
        # 这样FPGA每采集504对样本就立即发送,实现真正的连续采集
        fifo_threshold = 1008  # 1008字节 = 504对样本
        payload_0x21 = struct.pack("<I", fifo_threshold)
        self.serial_manager.send_command(0x21, payload_0x21)
        time.sleep(0.05)
        self.serial_manager.log_message.emit(
            f"  ✓ FIFO阈值: {fifo_threshold} 字节 (504对样本/包)"
        )

        # 3. 设置采样率分频系数
        payload_0x26 = struct.pack("<I", self.div_factor)
        self.serial_manager.send_command(0x26, payload_0x26)
        time.sleep(0.05)

        # 🔥 V7.5: 立即更新sample_rate变量
        base_freq = 50000000  # 50MHz基准
        self.sample_rate = base_freq / self.div_factor
        self.serial_manager.log_message.emit(
            f"  ✓ 采样率: {self.sample_rate / 1e6:.2f} MSPS (div={self.div_factor})"
        )

        # 🔥 V7.5: 流模式固定显示窗口10K点
        self.max_display_points = 10000
        self.serial_manager.log_message.emit(
            f"  ✓ 显示窗口: {self.max_display_points:,} 点（固定）"
        )

        # 🔥 锁定X轴范围
        self.fixed_xaxis_range = self.max_display_points / self.sample_rate * 1e6
        self.serial_manager.log_message.emit(
            f"  ✓ X轴锁定: {self.fixed_xaxis_range:.3f} μs"
        )

        # 4. 设置通道使能
        self.send_channel_enable_command()

        # 🔥 4.5 V8.7.1: 发送触发配置（统一触发系统）
        # 🔥 V8.7.1: 发送触发配置（统一触发系统）
        self.send_trigger_config()

        # 5. 启动以太网UDP接收器
        import time

        if not self.ethernet_receiver.running:
            self.ethernet_receiver.set_mode("buffer")  # 🔥 V8.7.14: 设置Buffer模式
            self.ethernet_receiver.start()
            # 🔥 关键修复：等待接收器线程完全启动（避免丢失首包）
            time.sleep(0.1)  # 100ms确保接收器线程进入监听状态
            self.serial_manager.log_message.emit(
                f"  ✓ 以太网UDP接收器已启动 (0.0.0.0:6102)"
            )
        else:
            self.serial_manager.log_message.emit(f"  ✓ 以太网UDP接收器运行中")

        # 6. 发送启动采集命令 (0x23)
        self.serial_manager.send_command(0x23, b"")
        time.sleep(0.05)

        # 🔥 V7.5: 设置采集状态并更新UI
        self.is_capturing = True
        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("⏹ 停止")
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #d9534f;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #c9302c; }
        """
        )

        self.serial_manager.log_message.emit("✅ [流模式] 采集已启动")
        self.serial_manager.log_message.emit(
            "📊 输入带宽: 双通道1MSPS×2 (总2MSPS), 单通道2MSPS (V8.6.41优化)"
        )
        self.serial_manager.log_message.emit(
            "💡 推荐范围: 双通道≤50kHz, 单通道≤100kHz 可获得最佳质量 (纯Python UDP限制)"
        )

    def apply_adaptive_params_and_start(self):
        """应用自适应参数并启动采集（精确FPGA配置）- 用于自动模式"""
        if not self.adaptive_params:
            self.serial_manager.log_message.emit("❌ [错误] adaptive_params为空")
            return

        params = self.adaptive_params

        # 🔥 V8.7.32关键修复: 强制同步current_mode与UI选择
        mode_index = self.mode_combo.currentIndex()
        self.current_mode = "stream" if mode_index == 0 else "buffer"

        # 清空缓冲区
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()
        # 🔥 重置显示更新时间戳，确保低频信号能立即显示
        self._last_display_update = 0
        # 🔥 设置标志：首次显示时调整X轴范围
        self.need_adjust_xaxis = True
        # 🔥 解锁频率显示（开始新的采集）
        self.freq_display_locked = False

        # 🔥 V8.7.9修复: 重置自动停止相关标志
        if hasattr(self, "_auto_stop_triggered"):
            delattr(self, "_auto_stop_triggered")
        if hasattr(self, "_is_auto_stopping"):
            delattr(self, "_is_auto_stopping")
        if hasattr(self, "_dummy_capture_done"):
            delattr(self, "_dummy_capture_done")
        self.buffer_received_packets = 0

        # 🔥 V8.6.2: 重置UDP接收状态标志
        if hasattr(self, "_v70_first_logged"):
            delattr(self, "_v70_first_logged")
        if hasattr(self, "_last_display_time"):
            delattr(self, "_last_display_time")
        if hasattr(self, "_v70_packet_count"):
            delattr(self, "_v70_packet_count")

        # 📡 开始配置FPGA（命令顺序必须严格按照：0x20 → 0x21 → 0x26 → 0x23）
        mode_name = "流模式" if self.current_mode == "stream" else "Buffer模式"
        self.serial_manager.log_message.emit(f"📡 正在配置FPGA参数（{mode_name}）...")

        # 1. 设置采集模式（0=流模式Stream, 1=Buffer模式）
        mode_val = 0 if self.current_mode == "stream" else 1
        import time

        if not self.serial_manager.send_command(0x20, bytes([mode_val])):
            self.serial_manager.log_message.emit("❌ 设置采集模式失败，停止配置")
            return
        time.sleep(0.1)  # 🔥 修复：增加到100ms，确保应答帧被接收
        self.serial_manager.log_message.emit(
            f"  ✓ 采集模式: {mode_name} (mode={mode_val})"
        )

        # 🔥 V8.7.33补偿方案: Buffer模式发送2倍深度给FPGA
        # 2. 设置0x21命令参数（流模式=FIFO阈值, Buffer模式=采样深度）
        depth = int(params["sample_depth"])
        fpga_depth = (
            depth * 2 if self.current_mode == "buffer" else depth
        )  # Buffer模式2倍补偿
        payload = struct.pack("<I", fpga_depth)
        if not self.serial_manager.send_command(0x21, payload):
            self.serial_manager.log_message.emit("❌ 设置采样深度失败，停止配置")
            return
        time.sleep(0.1)  # 🔥 修复：增加到100ms

        if self.current_mode == "stream":
            self.serial_manager.log_message.emit(
                f"  ✓ FIFO阈值: {depth} 字节 ({depth//2}对样本/UDP包)"
            )
        else:
            # Buffer模式
            self.serial_manager.log_message.emit(
                f"  ✓ 采样深度: {depth:,}点 (发送{fpga_depth:,}点给FPGA，2倍补偿)"
            )

        # 3. 设置采样率分频系数（算法精确计算的div_factor）
        div_factor = int(params["div_factor"])

        # 🔥 新增：参数验证（避免异常值导致FPGA卡死）
        if div_factor < 2:
            div_factor = 2
            self.serial_manager.log_message.emit("⚠️ div_factor过小，已调整为2")
        elif div_factor > 100000:
            self.serial_manager.log_message.emit(
                f"⚠️ 低频信号：div_factor={div_factor} 较大，可能导致采样间隔过长"
            )
        elif div_factor > 4294967295:
            self.serial_manager.log_message.emit(
                f"❌ div_factor={div_factor} 超出32位范围，无法采集"
            )
            return

        payload = struct.pack("<I", div_factor)
        if not self.serial_manager.send_command(0x26, payload):
            self.serial_manager.log_message.emit("❌ 设置采样率失败，停止配置")
            return
        time.sleep(0.1)  # 🔥 修复：增加到100ms

        # 计算并显示精确的FPGA配置信息
        base_freq = 50000000  # 50MHz基准（RESAMPLE_RATIO=1）
        actual_rate = base_freq / div_factor

        # 🔥 详细的配置日志（帮助诊断低频问题）
        if actual_rate < 1000:
            rate_str = f"{actual_rate:.2f} Hz"
            sampling_interval = 1000.0 / actual_rate  # ms
            self.serial_manager.log_message.emit(
                f"  ✓ 采样率分频: div_set={div_factor}\n"
                f"     实际采样率: {rate_str} (采样间隔={sampling_interval:.2f}ms)\n"
                f"     ⚠️ 低频采样：请确保信号稳定，采集时间较长"
            )
        else:
            rate_str = (
                f"{actual_rate/1e6:.2f} MSPS"
                if actual_rate >= 1e6
                else f"{actual_rate/1e3:.2f} kSPS"
            )
            self.serial_manager.log_message.emit(
                f"  ✓ 采样率分频: div_set={div_factor} (实际采样率={rate_str})"
            )

        # 🔥 V8.6.18: 4. 设置通道使能（硬件级控制）
        ch1_en = 1 if self.ch1_enabled else 0
        ch2_en = 1 if self.ch2_enabled else 0
        payload_0x28 = bytes([ch1_en, ch2_en])
        if not self.serial_manager.send_command(0x28, payload_0x28):
            self.serial_manager.log_message.emit("❌ 设置通道使能失败，停止配置")
            return
        time.sleep(0.1)  # 等待100ms

        ch_status = []
        if self.ch1_enabled:
            ch_status.append("CH1✓")
        if self.ch2_enabled:
            ch_status.append("CH2✓")
        status_str = " + ".join(ch_status) if ch_status else "所有通道禁用"
        self.serial_manager.log_message.emit(f"  ✓ 通道使能: {status_str}")

        # 🔥 4.5 V8.7.1: 发送触发配置（统一触发系统）
        self.send_trigger_config()

        # 🔥 V8.0: 更新内部变量
        self.sample_rate = params["sample_rate"]
        self.div_factor = params["div_factor"]

        if self.current_mode == "stream":
            # 流模式: FPGA持续采集,无采样深度概念
            self.buffer_size = None  # 流模式不使用
            # 🔥 V8.5: 流模式显示窗口10000点(确保低频信号能显示多个周期)
            # 1kHz信号20点/周期,采样率20kSPS,10K点=500ms,能显示500个周期
            # 50kHz信号20点/周期,采样率1MSPS,10K点=10ms,能显示500个周期
            self.max_display_points = 10000
            self.serial_manager.log_message.emit(f"  ✓ 显示策略: 10K点窗口(滚动显示)")
        else:
            # Buffer模式: 单次采集,有明确深度
            self.buffer_size = params["sample_depth"]  # 原始深度（用户设置）
            # Buffer模式：显示窗口=采集深度
            self.max_display_points = params["sample_depth"]

            # 🔥 V8.7.53修复: FPGA已在total_packets计算时+1，上位机接收FPGA发送的所有包
            # 策略：FPGA多发1包，上位机只用前N包，丢弃最后1包避免数据异常
            # FPGA接收: 2048点 → total_packets = 4+1 = 5包
            # FPGA实际发: 1024点 → 理论2包，FPGA发2+1=3包
            # 上位机预期: 1024点 >> 9 = 2包 (FPGA会多发1包)
            self.buffer_expected_packets = (
                self.buffer_size >> 9
            )  # 基于实际深度：÷512 (FPGA已+1)
            self.buffer_received_packets = 0  # 重置计数器

            self.serial_manager.log_message.emit(
                f"  ✓ Buffer模式显示窗口: {self.max_display_points:,} 点, 上位机预期{self.buffer_expected_packets}包(FPGA会多发1包冗余)"
            )

        # 4. 启动以太网UDP接收器（ADC数据通过UDP传输）
        # 🔥 V8.7.14.4: 必须先设置mode,否则Buffer模式会被当成stream处理!
        self.ethernet_receiver.set_mode(self.current_mode)  # "stream" 或 "buffer"

        import time

        if not self.ethernet_receiver.running:
            self.ethernet_receiver.start()
            # 🔥 关键修复：等待接收器线程完全启动（避免丢失首包）
            time.sleep(0.1)  # 100ms确保接收器线程进入监听状态
            self.serial_manager.log_message.emit(
                f"  ✓ 以太网UDP接收器已启动 (0.0.0.0:6102) [{self.current_mode}模式]"
            )
        else:
            self.serial_manager.log_message.emit(
                f"  ✓ 以太网UDP接收器运行中 (已切换至{self.current_mode}模式)"
            )

        # 🔥 清空数据缓冲区
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()

        # 🔥 V5.14: 重置UDP包相位跟踪（采集开始时从反转相位开始）
        # 根据实际测试：FPGA发送[CH2,CH1,CH2,CH1...] → 奇数索引=CH1
        self.udp_phase_offset = 1  # 第一包从反转相位开始(奇数索引=CH1)

        # 5. 发送启动采集命令 (0x23) - 这个命令会触发FPGA开始采集和发送数据
        import time

        time.sleep(0.05)  # 等待50ms，确保前面的配置命令都已处理完成
        self.serial_manager.send_command(0x23)
        self.serial_manager.log_message.emit(f"  ✓ 发送启动命令 (0x23)")
        self.serial_manager.log_message.emit(
            f"📡 等待FPGA发送UDP数据到 192.168.0.3:6102 ..."
        )

        # 🔥 注意：FPGA会自动每1秒测频并通过CH340发送数据
        # 上位机只需被动接收频率数据，无需额外控制

        # 更新UI状态 - 变为红色停止按钮
        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("⏹ 停止")
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #d9534f;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #c9302c; }
        """
        )
        self.is_capturing = True

        self.serial_manager.log_message.emit(f"✅ {mode_name}采集已启动")

    @Slot()
    def stop_capture(self):
        """停止采集"""
        if not self.serial_manager:
            return

        # 🔥 V8.7.54: 停止时重置测频状态机，防止残留
        if hasattr(self, "auto_measuring"):
            self.auto_measuring = False
        if hasattr(self.serial_manager, "freq_response_state"):
            self.serial_manager.freq_response_state = "IDLE"
        if hasattr(self.serial_manager, "freq_data_buffer"):
            self.serial_manager.freq_data_buffer = b""

        # 停止以太网UDP接收器
        if self.ethernet_receiver.running:
            self.ethernet_receiver.stop()
            self.serial_manager.log_message.emit("⏹ 以太网UDP接收器已停止")

        # 发送停止命令
        self.serial_manager.send_command(0x24)

        # 🔥 清空缓冲区,避免下次启动显示旧数据
        self.ch1_buffer.clear()
        self.ch2_buffer.clear()

        # 🔥 V8.6.37: 输出最终统计报告（在重置之前！）
        total_packets = getattr(self, "_total_received_packets", 0)
        loss_count = getattr(self, "_packet_loss_count", 0)
        loss_events_list = getattr(self, "_packet_loss_events", [])
        loss_events = len(loss_events_list)

        if total_packets > 0:
            loss_rate = (loss_count / total_packets) * 100
            self.serial_manager.log_message.emit(
                f"\n{'='*60}\n"
                f"📊 UDP接收统计总结\n"
                f"{'='*60}\n"
                f"  总接收包数: {total_packets}\n"
                f"  丢包总数:   {loss_count}\n"
                f"  丢包率:     {loss_rate:.4f}%\n"
                f"  丢包事件:   {loss_events}次\n"
                f"{'='*60}\n"
            )
        else:
            # 🔥 V8.6.37: 调试 - 为什么没有统计数据？
            self.serial_manager.log_message.emit(
                f"ℹ️ [调试] 无UDP统计数据: "
                f"total_packets={total_packets}, "
                f"has_attr={hasattr(self, '_total_received_packets')}"
            )

        # 🔥 V8.6.31: 重置所有UDP接收相关的内部状态标志
        # 避免反复启动停止后标志残留导致数据不更新
        if hasattr(self, "_v70_first_logged"):
            delattr(self, "_v70_first_logged")
        if hasattr(self, "_v70_rejected_packets"):
            delattr(self, "_v70_rejected_packets")
        if hasattr(self, "_v70_header_errors"):
            delattr(self, "_v70_header_errors")
        if hasattr(self, "_v70_packet_count"):
            delattr(self, "_v70_packet_count")
        if hasattr(self, "_last_packet_seq"):
            delattr(self, "_last_packet_seq")
        if hasattr(self, "_packet_loss_count"):
            delattr(self, "_packet_loss_count")
        if hasattr(self, "_packet_loss_events"):
            delattr(self, "_packet_loss_events")
        if hasattr(self, "_total_received_packets"):
            delattr(self, "_total_received_packets")
        if hasattr(self, "_first_seq_logged"):
            delattr(self, "_first_seq_logged")
        if hasattr(self, "_packet_loss_warned"):
            delattr(self, "_packet_loss_warned")
        if hasattr(self, "_last_display_time"):
            delattr(self, "_last_display_time")
        if hasattr(self, "_last_display_update"):
            delattr(self, "_last_display_update")

        # 🔥 V8.7.9修复: 重置Buffer模式自动停止相关状态
        self.buffer_received_packets = 0
        self.buffer_expected_packets = 0
        if hasattr(self, "packet_count"):
            self.packet_count = 0

        # 🔥 重置掩耳盗铃标志（手动停止时）
        if hasattr(self, "_dummy_capture_done"):
            self._dummy_capture_done = False

        # 重置UDP接收器内部统计
        self.ethernet_receiver.reset_statistics()

        # 🔥 注意：FPGA的自动测频会继续运行，但上位机不再处理
        # （因为is_capturing=False，频率数据仍会接收但不会触发变化检测）

        # 更新UI状态 - 恢复绿色启动按钮
        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("▶ 启动")
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #5cb85c;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #4cae4c; }
            QPushButton:disabled { 
                background-color: #cccccc; 
                color: #666;
            }
        """
        )
        self.is_capturing = False
        self.auto_measuring = False

        # 🔥 锁定频率显示(保留当前值,不再更新)
        # 不清除频率,让用户可以看到停止前的频率
        self.freq_display_locked = True

        self.serial_manager.log_message.emit("⏹ 示波器已停止")

    @Slot(list)
    def on_adc_data_received(self, samples):
        """
        接收ADC数据并更新显示（V3.2统一命名版）

        模式说明：
        - Buffer连续 (buffer_continuous)：滚动窗口实时显示，数据从CDC接收 ✅已实现
        - Buffer单次 (buffer_single)：单次触发一次性显示，预留接口 🔧
        - Stream触发 (stream_trigger)：与Buffer连续功能完全相同 ✅已实现

        当前所有模式均通过CDC传输数据
        """
        if not samples:
            return

        # 将8位ADC数据转换为电压值
        # 127 = 0V基准, 0 = -5V, 255 = +5V
        # 公式：V = (ADC - 127) * 10 / 255
        voltage_data = [(v - 127) * 10.0 / 255.0 for v in samples]

        # 所有模式都使用滚动窗口显示（简化逻辑）
        if False:  # Buffer单次模式已废弃
            self.ch1_buffer = voltage_data
            # Buffer单次模式下立即更新显示
            self.update_waveform_display()
        # Buffer连续模式和Stream触发模式：滚动窗口，保持时间轴连续（功能相同）
        else:
            # RingBuffer自动管理滚动窗口
            self.ch1_buffer.append(voltage_data)

            # 🔥 优化：对于低频信号，尽早显示数据（不等定时器）
            # 当缓冲区达到一定阈值时立即更新显示
            if len(self.ch1_buffer) >= 100:  # 至少100个点就显示
                # 检查距离上次显示更新是否超过100ms
                import time

                current_time = time.time()
                if not hasattr(self, "_last_display_update"):
                    self._last_display_update = 0

                # 🔥 V8.6: 进一步降低刷新率,减少CPU负载和视觉刷新过快
                # 低频信号(<100kHz): 300ms刷新 (3Hz), 高频信号: 150ms刷新 (6Hz)
                update_interval = 0.3 if self.sample_rate < 100000 else 0.15

                if current_time - self._last_display_update >= update_interval:
                    self.update_waveform_display()
                    self._last_display_update = current_time

    @Slot()
    def on_adc_capture_completed(self):
        """
        Buffer单次采集完成处理
        自动停止采集并更新UI状态
        """
        # 自动停止采集
        self.is_capturing = False

        # 🔥 Buffer单次模式：锁定频率显示，保留当前显示的频率值
        # 不清除频率，而是锁定它，防止FPGA后续测频更新
        self.freq_display_locked = True

        # 🔥 计算并更新测量参数(单次采集完成后显示)
        if self.ch1_buffer and len(self.ch1_buffer) >= 10:
            try:
                data = self.ch1_buffer.get_all()

                # 计算最大值和最小值
                vmax = np.max(data)
                vmin = np.min(data)
                vpp = vmax - vmin  # 峰峰值

                # 更新显示
                self.ch1_vpp_label.setText(f"{vpp:.3f} V")
                self.ch1_vmax_label.setText(f"{vmax:.3f} V")
                self.ch1_vmin_label.setText(f"{vmin:.3f} V")

                # 计算Vrms（有效值）
                vrms = np.sqrt(np.mean((data - np.mean(data)) ** 2))
                self.ch1_vrms_label.setText(f"{vrms:.3f} V")
            except Exception as e:
                self.serial_manager.log_message.emit(f"⚠️ 测量参数计算错误: {e}")

        # 🔥 V8.7.64: Buffer模式采集完成后更新波形和FFT显示
        # 关键：必须调用update_waveform_display()来刷新波形和FFT，和流模式保持一致
        self.update_waveform_display()

        # 更新UI状态 - 恢复绿色启动按钮
        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("▶ 启动")
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #5cb85c;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #4cae4c; }
            QPushButton:disabled { 
                background-color: #cccccc; 
                color: #666;
            }
        """
        )

        # 注意：不需要调用stop_adc_stream()和send_command(0x24)
        # 因为Buffer单次模式在FPGA端已经自动停止
        # 数据读取线程也已经自然退出

    def find_trigger_index(self, data, trigger_level, trigger_edge="rising"):
        """
        🔥 V7.6: 优化软件触发搜索 - 在缓冲区末尾搜索最新触发点

        参考建议：在流模式下应该在环形缓冲区末尾附近搜索，
        这样能捕捉到最新的触发事件，让波形稳定锁定。

        Args:
            data: numpy数组，待搜索的数据
            trigger_level: 触发电平（伏特）
            trigger_edge: 触发边沿，"rising"=上升沿，"falling"=下降沿

        Returns:
            触发点索引，未找到返回-1
        """
        if len(data) < 2:
            return -1

        # 🔥 V7.6优化: 在缓冲区后半段搜索(最新数据区域)
        # 搜索范围: 后50%的数据，确保捕捉最新触发事件
        search_start = len(data) // 2

        for i in range(search_start, len(data) - 1):
            if trigger_edge == "rising":
                # 上升沿：前一点 < 触发电平 <= 当前点
                if data[i - 1] < trigger_level <= data[i]:
                    return i
            else:  # falling
                # 下降沿：前一点 > 触发电平 >= 当前点
                if data[i - 1] > trigger_level >= data[i]:
                    return i

        return -1  # 未找到触发点

    def update_waveform_display(self):
        """更新波形显示（🔥 V8.0: 流模式显示最新10K点）"""

        # 🔥 V8.6.30修复：避免在数据更新时读取buffer（线程安全）
        if self.data_updating:
            return  # 跳过本次刷新，等待数据写入完成

        # 🔥 V8.7.14.8: Buffer模式禁用显示调试日志
        # Buffer模式是单次采集,不需要重复打印
        if self.current_mode != "buffer":
            # 🔥 V8.6.30调试：检查buffer实际大小（仅流模式）
            if not hasattr(self, "_display_debug_count"):
                self._display_debug_count = 0
            self._display_debug_count += 1

            if self._display_debug_count <= 20 or self._display_debug_count % 50 == 1:
                if self.serial_manager:
                    self.serial_manager.log_message.emit(
                        f"🔍 [显示调试#{self._display_debug_count}] "
                        f"Buffer: CH1={len(self.ch1_buffer)}点, CH2={len(self.ch2_buffer)}点, "
                        f"max_display={self.max_display_points}点, "
                        f"sample_rate={self.sample_rate/1e6:.2f}MSPS"
                    )

        # 🔥 V8.0: 按max_display_points截取数据
        ch1_data = (
            self.ch1_buffer.get_latest(self.max_display_points)
            if self.ch1_enabled
            else np.array([])
        )
        ch2_data = (
            self.ch2_buffer.get_latest(self.max_display_points)
            if self.ch2_enabled
            else np.array([])
        )

        # 🔥 V8.7.14.8: Buffer模式禁用连续性检测
        # Buffer模式是单次采集,不应该重复检测同一批数据
        if self.current_mode != "buffer":
            # 🔥 V8.6.36: 显示时检测数据连续性（仅流模式）
            if not hasattr(self, "_display_discontinuity_count"):
                self._display_discontinuity_count = {"ch1": 0, "ch2": 0}
                self._display_glitch_stats = {"ch1": [], "ch2": []}  # 记录所有跳变

            if len(ch1_data) > 10:
                # 检查CH1数据内部连续性
                # 🔥 动态阈值：检测超过信号幅度30%的跳变
                ch1_range = np.ptp(ch1_data)  # peak-to-peak
                threshold = max(0.3 * ch1_range, 0.5)  # 至少0.5V，或信号30%

                diffs = np.abs(np.diff(ch1_data))
                anomalies = np.where(diffs > threshold)[0]

                if len(anomalies) > 0:
                    max_diff_idx = np.argmax(diffs)
                    max_diff = diffs[max_diff_idx]
                    self._display_discontinuity_count["ch1"] += len(anomalies)
                    self._display_glitch_stats["ch1"].append(
                        {
                            "count": len(anomalies),
                            "max_diff": max_diff,
                            "positions": anomalies.tolist()[:5],  # 记录前5个位置
                        }
                    )

                    # 检测到显示不连续，但不输出警告日志（避免刷屏）
                    pass

            if len(ch2_data) > 10:
                # 检查CH2数据内部连续性
                ch2_range = np.ptp(ch2_data)
                threshold = max(0.3 * ch2_range, 0.5)

                diffs = np.abs(np.diff(ch2_data))
                anomalies = np.where(diffs > threshold)[0]

                if len(anomalies) > 0:
                    max_diff_idx = np.argmax(diffs)
                    max_diff = diffs[max_diff_idx]
                    self._display_discontinuity_count["ch2"] += len(anomalies)
                    self._display_glitch_stats["ch2"].append(
                        {
                            "count": len(anomalies),
                            "max_diff": max_diff,
                            "positions": anomalies.tolist()[:5],
                        }
                    )

                    total_count = self._display_discontinuity_count["ch2"]

                    # 检测到显示不连续，但不输出警告日志（避免刷屏）
                    pass

        # 检查是否有可用数据
        has_ch1_data = len(ch1_data) >= 10
        has_ch2_data = len(ch2_data) >= 10

        if not has_ch1_data and not has_ch2_data:
            return

        # 🔥 V7.5: 流模式自动选择显示策略
        use_trigger_mode = False
        if self.current_mode == "stream":
            # 🔥 流模式强制使用滚动显示,不使用软件触发
            # 原因:触发搜索会截断数据,导致视觉不连续
            use_trigger_mode = False
        elif self.current_mode == "buffer" and self.stream_display_mode == "auto":
            # Buffer模式根据频率自动选择
            # 🔥 V8.6.22: 使用已启用通道的最高频率
            freq_ch1 = self.fpga_measured_freq if self.fpga_measured_freq else 0
            freq_ch2 = (
                self.fpga_measured_freq_ch2
                if hasattr(self, "fpga_measured_freq_ch2")
                and self.fpga_measured_freq_ch2
                else 0
            )
            max_freq = 0
            if self.ch1_enabled:
                max_freq = freq_ch1
            if self.ch2_enabled:
                max_freq = max(max_freq, freq_ch2)

            if max_freq > 0:
                if max_freq >= self.stream_freq_threshold:
                    use_trigger_mode = True  # 高频：触发刷新模式
                else:
                    use_trigger_mode = False  # 低频：滚动模式
            else:
                use_trigger_mode = True  # 默认使用触发模式
        elif self.stream_display_mode == "triggered":
            use_trigger_mode = True
        elif self.stream_display_mode == "roll":
            use_trigger_mode = False

        # 不进行额外降采样，保持波形质量
        downsample_factor = 1

        # 🔥 V7.2: 固定时间轴基准，避免X轴抖动导致的视觉跳变
        # 始终使用max_display_points计算时间轴，即使实际数据不足
        # 这样可以保证X轴范围稳定，波形滚动平滑

        # 🔥 V7.3: 调试 - 打印关键参数（每100帧打印一次）
        if not hasattr(self, "_display_frame_count"):
            self._display_frame_count = 0
        self._display_frame_count += 1

        if self._display_frame_count % 500 == 1:
            pass  # 已移除调试输出

        # 🔥 V8.0: 时间轴计算 - 统一使用max_display_points避免X轴抖动
        time_axis_base = (
            np.arange(self.max_display_points)
            * downsample_factor
            / self.sample_rate
            * 1e6
        )

        # 🔥 V8.6.10: 移除长度差异警告（双通道允许独立长度）
        # 原因：UDP包到达有时间差，两个通道长度可能不同，这是正常的

        # 🔥 V7.4/V7.5: 软件触发对齐 - 根据显示模式决定是否使用
        trigger_idx = -1

        if use_trigger_mode and (has_ch1_data or has_ch2_data):
            # 🔥 触发刷新模式：搜索触发点，让波形"定"住
            # 根据触发源选择数据
            trigger_data = (
                ch1_data
                if (self.trigger_source == "CH1" and has_ch1_data)
                else ch2_data
            )

            if len(trigger_data) >= 100:  # 至少需要100个点才搜索触发
                trigger_idx = self.find_trigger_index(
                    trigger_data, self.trigger_level, self.trigger_edge
                )
        # else: 滚动模式，不使用触发对齐，trigger_idx保持-1

        # 🔥 V7.4: 根据触发结果决定数据截取方式
        if has_ch1_data and has_ch2_data:
            # 🔥 V8.6.10修复: 双通道模式不强制对齐长度
            # Bug原因: min_len会丢弃较长通道的数据，导致波形不连续
            # 新策略: 各通道独立截取，保留完整数据

            # 🔥 V8.0: 流模式直接显示全部数据,不截取
            if self.current_mode == "stream" or self.max_display_points is None:
                # 流模式: 环形缓冲区自动管理,显示全部可用数据
                ch1_data_to_plot = ch1_data
                ch2_data_to_plot = ch2_data

                # 🔥 修复：流模式使用固定X轴范围（0到窗口大小），波形向左滚动
                # 正确的示波器行为：X轴固定，新数据从右边进来，旧数据从左边消失
                max_len = max(len(ch1_data), len(ch2_data))
                time_axis = np.arange(max_len) / self.sample_rate * 1e6
            elif trigger_idx != -1 and trigger_idx < min(len(ch1_data), len(ch2_data)):
                # Buffer模式+找到触发点：以触发点为中心截取数据
                min_len = min(len(ch1_data), len(ch2_data))
                pre_trigger_points = int(self.max_display_points * 0.1)
                start = max(0, trigger_idx - pre_trigger_points)
                end = min(min_len, start + self.max_display_points)

                if end - start < self.max_display_points:
                    start = max(0, end - self.max_display_points)

                ch1_data_to_plot = ch1_data[start:end]
                ch2_data_to_plot = ch2_data[start:end]
                time_axis = time_axis_base[: len(ch1_data_to_plot)]
            else:
                # Buffer模式+未找到触发点：显示最新数据（滚动模式）
                if len(ch1_data) > self.max_display_points:
                    ch1_data_to_plot = ch1_data[-self.max_display_points :]
                else:
                    ch1_data_to_plot = ch1_data

                if len(ch2_data) > self.max_display_points:
                    ch2_data_to_plot = ch2_data[-self.max_display_points :]
                else:
                    ch2_data_to_plot = ch2_data

                # 时间轴使用较长的数据长度
                max_len = max(len(ch1_data_to_plot), len(ch2_data_to_plot))
                time_axis = time_axis_base[:max_len]

            # 🔥 V8.6.30修复：双通道各自使用独立时间轴
            # Bug: 两个通道数据长度可能不同，用统一time_axis会导致对齐错误

            # 🔥 V8.6.35: 调试长度差异
            if not hasattr(self, "_length_diff_debug_count"):
                self._length_diff_debug_count = 0
            self._length_diff_debug_count += 1

            if self._length_diff_debug_count % 100 == 1:
                len_diff = abs(len(ch1_data_to_plot) - len(ch2_data_to_plot))
                if len_diff > 10 and self.serial_manager:
                    self.serial_manager.log_message.emit(
                        f"🔍 [长度差异#{self._length_diff_debug_count}] "
                        f"CH1={len(ch1_data_to_plot)}点, CH2={len(ch2_data_to_plot)}点, "
                        f"差值={len_diff}点"
                    )

            if self.ch1_visible:
                # 🔥 流模式和Buffer模式都使用time_axis_base（固定X轴范围）
                ch1_time_axis = time_axis_base[: len(ch1_data_to_plot)]
                self.ch1_curve.setData(ch1_time_axis, ch1_data_to_plot)
            else:
                self.ch1_curve.setData([], [])

            if self.ch2_visible:
                # 🔥 流模式和Buffer模式都使用time_axis_base（固定X轴范围）
                ch2_time_axis = time_axis_base[: len(ch2_data_to_plot)]
                self.ch2_curve.setData(ch2_time_axis, ch2_data_to_plot)
            else:
                self.ch2_curve.setData([], [])
        else:
            # 🔥 单通道模式
            if has_ch1_data:
                # 🔥 V8.0: 流模式显示全部数据
                if self.current_mode == "stream" or self.max_display_points is None:
                    ch1_data_to_plot = ch1_data
                    # 🔥 流模式使用固定X轴范围（环形缓冲区自动管理滚动）
                    ch1_time_axis = time_axis_base[: len(ch1_data_to_plot)]
                elif trigger_idx != -1 and self.trigger_source == "CH1":
                    # Buffer模式+触发模式
                    pre_trigger_points = int(self.max_display_points * 0.1)
                    start = max(0, trigger_idx - pre_trigger_points)
                    end = min(len(ch1_data), start + self.max_display_points)
                    if end - start < self.max_display_points:
                        start = max(0, end - self.max_display_points)
                    ch1_data_to_plot = ch1_data[start:end]
                    ch1_time_axis = time_axis_base[: len(ch1_data_to_plot)]
                else:
                    # Buffer模式+滚动模式:显示最新数据
                    if len(ch1_data) > self.max_display_points:
                        ch1_data_to_plot = ch1_data[-self.max_display_points :]
                        # X轴滚动
                        buffer_len = len(self.ch1_buffer)
                        time_offset = (
                            (buffer_len - self.max_display_points)
                            / self.sample_rate
                            * 1e6
                        )
                        ch1_time_axis = (
                            np.arange(self.max_display_points) / self.sample_rate * 1e6
                            + time_offset
                        )
                    else:
                        ch1_data_to_plot = ch1_data
                        ch1_time_axis = time_axis_base[: len(ch1_data_to_plot)]
                if self.ch1_visible:
                    self.ch1_curve.setData(ch1_time_axis, ch1_data_to_plot)
                else:
                    self.ch1_curve.setData([], [])
            else:
                self.ch1_curve.setData([], [])

            if has_ch2_data:
                # 🔥 V8.0: 流模式显示全部数据
                if self.current_mode == "stream" or self.max_display_points is None:
                    ch2_data_to_plot = ch2_data
                    # 🔥 流模式使用固定X轴范围（环形缓冲区自动管理滚动）
                    ch2_time_axis = time_axis_base[: len(ch2_data_to_plot)]
                elif trigger_idx != -1 and self.trigger_source == "CH2":
                    # Buffer模式+触发模式
                    pre_trigger_points = int(self.max_display_points * 0.1)
                    start = max(0, trigger_idx - pre_trigger_points)
                    end = min(len(ch2_data), start + self.max_display_points)
                    if end - start < self.max_display_points:
                        start = max(0, end - self.max_display_points)
                    ch2_data_to_plot = ch2_data[start:end]
                    ch2_time_axis = time_axis_base[: len(ch2_data_to_plot)]
                else:
                    # Buffer模式+滚动模式:显示最新数据
                    if len(ch2_data) > self.max_display_points:
                        ch2_data_to_plot = ch2_data[-self.max_display_points :]
                        # X轴滚动
                        buffer_len = len(self.ch2_buffer)
                        time_offset = (
                            (buffer_len - self.max_display_points)
                            / self.sample_rate
                            * 1e6
                        )
                        ch2_time_axis = (
                            np.arange(self.max_display_points) / self.sample_rate * 1e6
                            + time_offset
                        )
                    else:
                        ch2_data_to_plot = ch2_data
                        ch2_time_axis = time_axis_base[: len(ch2_data_to_plot)]
                if self.ch2_visible:
                    self.ch2_curve.setData(ch2_time_axis, ch2_data_to_plot)
                else:
                    self.ch2_curve.setData([], [])
            else:
                self.ch2_curve.setData([], [])

        # 🔥 V7.1: Y轴自动缩放（使用快照数据）
        all_visible_data = []
        if has_ch1_data and self.ch1_visible:
            all_visible_data.extend(ch1_data.tolist())
        if has_ch2_data and self.ch2_visible:
            all_visible_data.extend(ch2_data.tolist())

        if len(all_visible_data) > 0:
            data_array = np.array(all_visible_data)
            vmax = np.max(data_array)
            vmin = np.min(data_array)
            v_range = vmax - vmin

            # 添加10%的边距使波形不贴边
            margin = v_range * 0.1 if v_range > 0 else 0.5
            y_min = vmin - margin
            y_max = vmax + margin

            # 限制在合理范围内（-10V到+10V）
            y_min = max(y_min, -10)
            y_max = min(y_max, 10)

            self.plot_widget.setYRange(y_min, y_max, padding=0)

        # 🔥 X轴范围管理：使用固定时间窗口
        # 优先使用已计算的time_axis（双通道模式），否则使用单通道的时间轴
        reference_time_axis = None
        if has_ch1_data and has_ch2_data:
            # 双通道模式，使用已计算的time_axis
            reference_time_axis = time_axis if "time_axis" in locals() else None
        elif has_ch1_data:
            reference_time_axis = ch1_time_axis if "ch1_time_axis" in locals() else None
        elif has_ch2_data:
            reference_time_axis = ch2_time_axis if "ch2_time_axis" in locals() else None

        if reference_time_axis is None:
            return

        if self.need_adjust_xaxis and len(reference_time_axis) > 100:
            # Auto按钮触发后，计算并锁定X轴范围
            self.need_adjust_xaxis = False

            # 🔥 V8.6.22: 使用已启用通道的最高频率
            freq_ch1 = self.fpga_measured_freq if self.fpga_measured_freq else 0
            freq_ch2 = (
                self.fpga_measured_freq_ch2
                if hasattr(self, "fpga_measured_freq_ch2")
                and self.fpga_measured_freq_ch2
                else 0
            )
            max_freq = 0
            if self.ch1_enabled:
                max_freq = freq_ch1
            if self.ch2_enabled:
                max_freq = max(max_freq, freq_ch2)

            if max_freq > 0:
                # 计算固定时间窗口：显示N个周期
                signal_period_us = 1e6 / max_freq
                target_periods = getattr(self, "target_periods", 6)
                self.fixed_xaxis_range = signal_period_us * target_periods

                # 设置X轴范围
                self.plot_widget.setXRange(0, self.fixed_xaxis_range, padding=0)
            else:
                # 没有频率信息，显示全部数据
                if len(reference_time_axis) > 0:
                    self.fixed_xaxis_range = reference_time_axis[-1]
                    self.plot_widget.setXRange(0, self.fixed_xaxis_range, padding=0)

        elif self.fixed_xaxis_range is not None:
            # 使用已锁定的X轴范围，不做任何调整
            # X轴保持不变，只更新波形数据（数据在buffer中滚动）
            pass
        else:
            # 首次显示或未设置固定范围，显示全部数据
            if len(reference_time_axis) > 0:
                self.plot_widget.setXRange(0, reference_time_axis[-1], padding=0)

        # 🔥 V8.6.43: FFT频谱更新（双通道独立显示）
        # 逻辑：CH1和CH2都可以同时显示FFT
        if self.fft_enabled:
            # 🔥 V8.7.65: 修复FFT即时显示/隐藏
            # 关键：只在通道可见时计算FFT，隐藏时保留已有数据（不清空）
            if has_ch1_data and self.ch1_enabled:
                if self.ch1_visible:
                    # CH1可见：计算并显示FFT
                    self.update_fft_spectrum(ch1_data, channel_name="CH1")
                # 始终根据ch1_visible状态控制可见性（不清空数据）
                if hasattr(self, "fft_curve_ch1"):
                    self.fft_curve_ch1.setVisible(self.ch1_visible)
            else:
                # CH1禁用或无数据：清空FFT
                self.fft_curve_ch1.setData([], [])
                self._fft_ch1_magnitude = None
                if hasattr(self, "fft_curve_ch1"):
                    self.fft_curve_ch1.setVisible(False)

            if has_ch2_data and self.ch2_enabled:
                if self.ch2_visible:
                    # CH2可见：计算并显示FFT
                    self.update_fft_spectrum(ch2_data, channel_name="CH2")
                # 始终根据ch2_visible状态控制可见性（不清空数据）
                if hasattr(self, "fft_curve_ch2"):
                    self.fft_curve_ch2.setVisible(self.ch2_visible)
            else:
                # CH2禁用或无数据：清空FFT
                self.fft_curve_ch2.setData([], [])
                self._fft_ch2_magnitude = None
                if hasattr(self, "fft_curve_ch2"):
                    self.fft_curve_ch2.setVisible(False)

            # 🔥 统一计算Y轴范围（基于所有可见通道的数据）
            all_magnitudes = []
            if (
                hasattr(self, "_fft_ch1_magnitude")
                and self._fft_ch1_magnitude is not None
            ):
                all_magnitudes.append(self._fft_ch1_magnitude)
            if (
                hasattr(self, "_fft_ch2_magnitude")
                and self._fft_ch2_magnitude is not None
            ):
                all_magnitudes.append(self._fft_ch2_magnitude)

            if all_magnitudes:
                # 合并所有通道的幅度数据
                combined_magnitude = np.concatenate(all_magnitudes)
                median_db = np.median(combined_magnitude)
                max_db = np.max(combined_magnitude)
                y_min = max(median_db - 20, max_db - 100)  # 噪底上20dB，或峰值下100dB
                y_max = max_db + 10  # 留10dB余量
                self.fft_widget.setYRange(y_min, y_max, padding=0)

    def update_fft_spectrum(self, signal_data, channel_name="CH1"):
        """
        计算并更新FFT频谱显示（🔥 V8.6.42增强：支持双通道+流模式）

        优化特性：
        - 自适应FFT长度（1024-8192点）
        - 高频信号优化（>1MHz）
        - 智能频率轴范围
        - 动态范围增强（-120dB ~ 0dB）
        - 双通道独立分析

        Args:
            signal_data: numpy数组，时域信号数据（电压值）
            channel_name: 通道名称（"CH1"或"CH2"），用于日志显示
        """
        if signal_data is None:
            return

        # 🔥 V8.6.42: 统一处理数据类型（支持RingBuffer和numpy数组）
        if hasattr(signal_data, "get_all"):
            # RingBuffer对象，提取数据
            signal_array = signal_data.get_all()
        elif isinstance(signal_data, (list, tuple)):
            signal_array = np.array(signal_data)
        else:
            # 已经是numpy数组
            signal_array = signal_data

        if len(signal_array) < 256:
            # 数据太少，无法进行有效的FFT
            return

        # 🔥 检查采样率是否有效
        if not hasattr(self, "sample_rate") or self.sample_rate <= 0:
            if hasattr(self, "serial_manager") and self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"⚠️ FFT计算失败: 采样率无效 (sample_rate={getattr(self, 'sample_rate', 'None')})"
                )
            return

        try:
            # 🔥 V8.6.43: 采用参考文件的成熟FFT算法 - 固定4096点，更稳定
            FFT_SIZE = 4096

            # 取最新的FFT_SIZE点数据
            if len(signal_array) >= FFT_SIZE:
                signal = signal_array[-FFT_SIZE:]
            else:
                # 数据不足，用0填充
                signal = np.zeros(FFT_SIZE)
                signal[: len(signal_array)] = signal_array

            # 去除直流分量
            signal = signal - np.mean(signal)

            # 🔥 应用窗函数
            if self.fft_window_type == "rectangular":
                window = np.ones(FFT_SIZE)
            elif self.fft_window_type == "hanning":
                window = np.hanning(FFT_SIZE)
            elif self.fft_window_type == "hamming":
                window = np.hamming(FFT_SIZE)
            elif self.fft_window_type == "blackman":
                window = np.blackman(FFT_SIZE)
            elif self.fft_window_type == "bartlett":
                window = np.bartlett(FFT_SIZE)
            elif self.fft_window_type == "kaiser":
                window = np.kaiser(FFT_SIZE, beta=8.6)
            else:
                window = np.hanning(FFT_SIZE)

            signal_windowed = signal * window

            # 🔥 窗函数补偿系数（参考文件的简洁公式）
            window_correction = 2.0 / np.sum(window) * FFT_SIZE

            # 计算FFT
            fft_result = np.fft.fft(signal_windowed, n=FFT_SIZE)
            fft_positive = fft_result[: FFT_SIZE // 2]

            # 计算幅度并归一化
            magnitude = np.abs(fft_positive) * window_correction / FFT_SIZE

            # 转换为dBV
            magnitude_db = 20 * np.log10(magnitude + 1e-12)

            # 🔥 计算频率轴
            # V8.7.66: 修复流模式FFT频率计算
            # 流模式(stream): 单通道数据，直接使用sample_rate
            # Buffer模式: 双通道交织数据[CH2][CH1][CH2][CH1]，需要除以2
            if self.current_mode == "stream":
                # 流模式：单通道数据，不需要除以2
                effective_sample_rate = self.sample_rate
            else:
                # Buffer模式：双通道交织，单通道有效采样率 = 总数据率 / 2
                effective_sample_rate = self.sample_rate / 2.0

            freq_axis = np.fft.fftfreq(FFT_SIZE, d=1.0 / effective_sample_rate)[
                : FFT_SIZE // 2
            ]

            # 🔥 跳过DC分量和近DC噪声（从索引2开始，参考文件方案）
            start_idx = 2

            # 🔥 确定显示频率范围
            # 奈奎斯特频率基于有效采样率
            nyquist_freq = effective_sample_rate / 2.0

            # 获取信号频率（用于智能缩放）
            signal_freq = 0
            if self.ch1_enabled and hasattr(self, "fpga_measured_freq"):
                signal_freq = max(signal_freq, self.fpga_measured_freq or 0)
            if self.ch2_enabled and hasattr(self, "fpga_measured_freq_ch2"):
                signal_freq = max(signal_freq, self.fpga_measured_freq_ch2 or 0)

            # 显示范围：0 到 奈奎斯特频率 或 信号频率*10
            if signal_freq > 0:
                max_display_freq = min(signal_freq * 10, nyquist_freq)
            else:
                max_display_freq = nyquist_freq

            # 找到对应索引
            end_idx = np.searchsorted(freq_axis, max_display_freq)
            end_idx = min(end_idx, len(freq_axis))

            # 提取显示数据
            freq_display = freq_axis[start_idx:end_idx]
            magnitude_display = magnitude_db[start_idx:end_idx]

            # 🔥 V8.6.44: 轻微平滑处理，减少频谱泄漏产生的小尖峰
            # 使用3点移动平均，保留主峰特征的同时减少噪声
            if len(magnitude_display) > 5:
                from scipy.ndimage import uniform_filter1d

                magnitude_display = uniform_filter1d(
                    magnitude_display, size=3, mode="nearest"
                )

            # 🔥 V8.7.65: 增强FFT调试日志，显示更多关键信息
            debug_key = f"_fft_debug_logged_{channel_name}"
            if not hasattr(self, debug_key):
                setattr(self, debug_key, True)
                if hasattr(self, "serial_manager") and self.serial_manager:
                    # 找到峰值频率
                    peak_idx = np.argmax(magnitude_display)
                    peak_freq = (
                        freq_display[peak_idx] if len(freq_display) > peak_idx else 0
                    )
                    peak_mag = (
                        magnitude_display[peak_idx]
                        if len(magnitude_display) > peak_idx
                        else 0
                    )

                    self.serial_manager.log_message.emit(
                        f"\n🔍 [FFT调试] {channel_name}\n"
                        f"  数据率(双通道交织): {self.sample_rate/1e6:.3f} MSPS ({self.sample_rate} Hz)\n"
                        f"  有效采样率(单通道): {effective_sample_rate/1e6:.3f} MSPS ({effective_sample_rate} Hz)\n"
                        f"  FFT点数: {FFT_SIZE}\n"
                        f"  数据长度: {len(signal_array)}点\n"
                        f"  频率分辨率: {effective_sample_rate/FFT_SIZE:.2f} Hz/bin\n"
                        f"  奈奎斯特频率: {nyquist_freq/1e6:.3f} MHz\n"
                        f"  显示频率范围: {freq_display[0]:.1f} Hz ~ {max_display_freq/1e6:.3f} MHz\n"
                        f"  峰值频率: {peak_freq/1e3:.3f} kHz @ {peak_mag:.1f} dBV\n"
                        f"  幅度范围: {magnitude_display.min():.1f} ~ {magnitude_display.max():.1f} dBV"
                    )

            # 🔥 V8.6.43: 根据通道名称更新对应的FFT曲线
            if len(freq_display) > 10:
                if channel_name == "CH1":
                    self.fft_curve_ch1.setData(freq_display, magnitude_display)
                    # 保存CH1的幅度数据用于后续Y轴计算
                    self._fft_ch1_magnitude = magnitude_display
                elif channel_name == "CH2":
                    self.fft_curve_ch2.setData(freq_display, magnitude_display)
                    # 保存CH2的幅度数据用于后续Y轴计算
                    self._fft_ch2_magnitude = magnitude_display

                # 🔥 X轴范围：线性刻度（参考文件方案）
                self.fft_widget.setLogMode(x=False, y=False)
                self.fft_widget.setXRange(0, max_display_freq, padding=0.05)

        except Exception as e:
            if hasattr(self, "serial_manager") and self.serial_manager:
                self.serial_manager.log_message.emit(f"⚠️ FFT计算错误: {e}")

    def update_measurements(self):
        """
        更新测量值（✨ V5.0增强：智能频率测量）

        测量内容：
        - Vpp（峰峰值）：最大值 - 最小值
        - Vrms（有效值）：√(Σ(v²)/N)
        - 频率：多算法融合测频
          * FPGA硬件测频（0-5MHz，高精度）
          * FFT峰值检测（100Hz-25MHz，抛物线插值）
          * 自相关算法（10Hz-1MHz，低频优化）
          * 过零检测（快速验证）
        """

    def update_display(self):
        """定时更新显示（独立线程，降低UI刷新频率）"""
        # 停止状态不更新
        if not self.is_capturing:
            return

        # 🔥 V8.6.2修复: 检查缓冲区是否有足够数据
        has_data = False
        if self.ch1_buffer and len(self.ch1_buffer) >= 10:
            has_data = True
        if self.ch2_buffer and len(self.ch2_buffer) >= 10:
            has_data = True

        # 连续采集模式下定时更新
        if has_data:
            self.update_waveform_display()

    def auto_scale(self):
        """
        🔥 V8.3: 自动缩放（根据模式选择操作）

        流模式：触发自适应算法重配置
        Buffer模式：只做XY轴缩放
        """
        if not self.serial_manager:
            return

        # 🔥 V8.6.1: 双通道模式下同时考虑两个通道的数据
        has_data = False
        data_ch1 = None
        data_ch2 = None

        # 获取启用通道的数据
        if self.ch1_enabled and self.ch1_buffer and len(self.ch1_buffer) >= 10:
            has_data = True
            data_ch1 = self.ch1_buffer.get_all()
        if self.ch2_enabled and self.ch2_buffer and len(self.ch2_buffer) >= 10:
            has_data = True
            data_ch2 = self.ch2_buffer.get_all()

        try:
            if self.current_mode == "stream":
                # 🔥 流模式：采集中触发自适应算法重配置
                if self.is_capturing:
                    self.trigger_reconfigure()
                elif has_data:
                    self._auto_scale_axes_only(data_ch1, data_ch2)
            else:
                # 🔥 Buffer模式：只做XY轴缩放
                if has_data:
                    self._auto_scale_axes_only(data_ch1, data_ch2)
        except Exception as e:
            self.serial_manager.log_message.emit(f"❌ Auto失败: {e}")

    def _auto_scale_axes_only(self, data_ch1=None, data_ch2=None):
        """
        🔥 V8.6.1: 仅XY轴自动缩放（双通道独立考虑）

        Args:
            data_ch1: numpy数组，CH1波形数据
            data_ch2: numpy数组，CH2波形数据
        """
        # Y轴缩放 - 双通道独立计算
        if data_ch1 is not None and len(data_ch1) >= 10:
            vmax_ch1 = np.max(data_ch1)
            vmin_ch1 = np.min(data_ch1)
            v_range_ch1 = vmax_ch1 - vmin_ch1
            margin_ch1 = v_range_ch1 * 0.1 if v_range_ch1 > 0 else 0.5

            self.plot_widget.setYRange(
                vmin_ch1 - margin_ch1, vmax_ch1 + margin_ch1, padding=0
            )

        if data_ch2 is not None and len(data_ch2) >= 10:
            vmax_ch2 = np.max(data_ch2)
            vmin_ch2 = np.min(data_ch2)
            v_range_ch2 = vmax_ch2 - vmin_ch2
            margin_ch2 = v_range_ch2 * 0.1 if v_range_ch2 > 0 else 0.5

            self.plot_widget_ch2.setYRange(
                vmin_ch2 - margin_ch2, vmax_ch2 + margin_ch2, padding=0
            )

        # X轴缩放 - 使用有效数据确定时间范围
        sample_rate = self.sample_rate if self.sample_rate > 0 else 1e6
        max_display_points = (
            self.max_display_points if self.max_display_points else 10000
        )

        time_span = max_display_points / sample_rate
        self.plot_widget.setXRange(0, time_span, padding=0)
        if hasattr(self, "plot_widget_ch2"):
            self.plot_widget_ch2.setXRange(0, time_span, padding=0)

    def trigger_reconfigure(self, adjust_xaxis=True):
        """
        触发重新配置流程（由Auto按钮或频率变化检测调用）

        Args:
            adjust_xaxis: 是否调整X轴范围（Auto按钮=True，频率变化=False）

        流程：
        1. 使用最新的FPGA自动测频结果（self.fpga_measured_freq）
        2. 计算新的自适应参数
        3. 不停止采集，直接重新配置FPGA参数
        4. 清空缓冲区，开始显示新参数下的波形

        注意：由于FPGA每1秒自动测频，这里直接使用最新频率值，
              无需再发送0x27命令等待（减少延迟）
        """
        if not self.serial_manager or not self.serial_manager.is_connected():
            return

        if not self.is_capturing:
            return

        # 🔥 V8.6.22: 检查已启用通道是否有有效频率数据
        freq_ch1 = self.fpga_measured_freq if self.fpga_measured_freq else 0
        freq_ch2 = (
            self.fpga_measured_freq_ch2
            if hasattr(self, "fpga_measured_freq_ch2") and self.fpga_measured_freq_ch2
            else 0
        )

        has_valid_freq = False
        if self.ch1_enabled and freq_ch1 > 0:
            has_valid_freq = True
        if self.ch2_enabled and freq_ch2 > 0:
            has_valid_freq = True

        if not has_valid_freq:
            # 🔥 主动请求一次测频，并标记重新配置状态
            if self.serial_manager.request_frequency_measurement():
                self.is_reconfiguring = True
            return

        # 🔥 直接使用最新频率重新计算参数（freq_ch1/freq_ch2已在上面获取）

        # 🔥 V8.6.22: 判断频率是否变化（使用已启用通道的频率）
        current_freq = 0
        if self.ch1_enabled and freq_ch1 > 0:
            current_freq = freq_ch1
        elif self.ch2_enabled and freq_ch2 > 0:
            current_freq = freq_ch2

        if self.last_measured_freq and self.last_measured_freq > 0 and current_freq > 0:
            change_percent = (
                abs(current_freq - self.last_measured_freq)
                / self.last_measured_freq
                * 100
            )
            if change_percent > 1.0:  # 变化超过1%
                # 格式化显示频率
                if current_freq >= 1e6:
                    freq_str = f"{current_freq/1e6:.6f} MHz"
                elif current_freq >= 1e3:
                    freq_str = f"{current_freq/1e3:.3f} kHz"
                else:
                    freq_str = f"{current_freq:.0f} Hz"

                self.serial_manager.log_message.emit(
                    f"📊 频率变化 {change_percent:.1f}%: "
                    f"{self.last_measured_freq} Hz → {freq_str}"
                )

        # 🔥 V8.6: 根据模式使用不同的自适应算法
        if self.current_mode == "stream":
            # ========== 流模式自适应算法 V8.6 ==========
            base_freq = 50000000

            # 根据通道数决定参数
            dual_channel = self.ch1_enabled and self.ch2_enabled
            if dual_channel:
                max_effective_rate = 2000000  # 双通道: 2MSPS/通道 (总4MSPS)
                max_input_bandwidth = 100000  # 双通道: 100kHz
            else:
                max_effective_rate = 2000000  # 单通道: 2MSPS
                max_input_bandwidth = 100000  # 单通道: 100kHz

            min_points_per_period = 15
            target_points_per_period = 20

            max_freq = max(freq_ch1, freq_ch2)
            if max_freq == 0:
                max_freq = freq_ch1 if freq_ch1 > 0 else 1000  # 默认1kHz

            ideal_div = int(base_freq / (max_freq * target_points_per_period))
            min_div_for_performance = int(base_freq / max_effective_rate)
            optimal_div = max(ideal_div, min_div_for_performance)
            if optimal_div % 2 == 1 and optimal_div > 1:
                optimal_div += 1

            effective_rate = base_freq / optimal_div

            new_params = {
                "div_factor": optimal_div,
                "sample_rate": effective_rate,
                "sample_depth": 1008,
                "buffer_size": 10000,
                "input_bandwidth": max_input_bandwidth,
                "points_per_period": (
                    int(effective_rate / max_freq) if max_freq > 0 else 20
                ),
                "actual_periods": 0,
                "display_periods": 0,
            }
        else:
            # ========== Buffer模式自适应算法 ==========
            # 🔥 V8.6.22: 使用已启用通道中频率较高的那个
            target_freq = 0
            if self.ch1_enabled and freq_ch1 > 0:
                target_freq = freq_ch1
            if self.ch2_enabled and freq_ch2 > 0:
                target_freq = max(target_freq, freq_ch2)

            new_params = AdaptiveSamplingCalculator.calculate(
                signal_freq=target_freq,
                target_periods=self.target_periods,
                min_points_per_period=20,
                max_points_per_period=40,
            )

        if new_params:
            self.adaptive_params = new_params
            # 🔥 V8.6.22: 更新频率记录（优先CH1，其次CH2）
            if self.ch1_enabled and freq_ch1 > 0:
                self.last_measured_freq = freq_ch1
            elif self.ch2_enabled and freq_ch2 > 0:
                self.last_measured_freq = freq_ch2
            self.update_adaptive_params_display()

            # 🔥 关键：直接重新配置参数
            self.reconfigure_sampling_params(adjust_xaxis=adjust_xaxis)
        else:
            self.serial_manager.log_message.emit("❌ 参数计算失败")

    def reconfigure_sampling_params(self, adjust_xaxis=True):
        """
        🔥 V8.3: 重新配置采样参数（根据模式区分操作）

        Args:
            adjust_xaxis: 是否调整X轴范围（Auto按钮=True，频率变化=False）

        流模式流程：
        1. 发送新的div_set (0x26) - 只改变抽取系数
        2. 不修改采样深度(流模式固定1008字节FIFO阈值)
        3. 不清空缓冲区，平滑过渡

        Buffer模式流程：
        1. 发送新的div_set (0x26)
        2. 发送新的采样深度 (0x21)
        3. 清空缓冲区
        """
        if not self.adaptive_params:
            return

        import struct

        params = self.adaptive_params

        self.serial_manager.log_message.emit("🔧 [重配置] 正在更新FPGA参数...")

        # 1. 设置新的采样率分频系数
        div_factor = int(params["div_factor"])
        payload = struct.pack("<I", div_factor)
        self.serial_manager.send_command(0x26, payload)

        # 2. 🔥 V8.3: 只有Buffer模式才修改采样深度
        if self.current_mode != "stream":
            depth = int(params["sample_depth"])
            payload = struct.pack("<I", depth)
            self.serial_manager.send_command(0x21, payload)
            self.buffer_size = params["sample_depth"]
            self.max_display_points = params["sample_depth"]
            self.serial_manager.log_message.emit(f"  ✓ 新采样深度: {depth:,} 点")
        else:
            # 🔥 V8.6.7: 流模式应用动态buffer_size（低频优化）
            dynamic_buffer_size = params.get("buffer_size", 10000)
            self.max_display_points = dynamic_buffer_size

            # 🔥 关键：重新创建环形缓冲区以调整大小（RingBuffer无resize方法）
            # 保留旧数据，迁移到新buffer
            old_ch1_data = (
                self.ch1_buffer.get_all() if len(self.ch1_buffer) > 0 else np.array([])
            )
            old_ch2_data = (
                self.ch2_buffer.get_all() if len(self.ch2_buffer) > 0 else np.array([])
            )

            self.ch1_buffer = RingBuffer(capacity=dynamic_buffer_size, dtype=np.float32)
            self.ch2_buffer = RingBuffer(capacity=dynamic_buffer_size, dtype=np.float32)

            # 恢复数据（只保留最新的dynamic_buffer_size个点）
            if len(old_ch1_data) > 0:
                self.ch1_buffer.append(old_ch1_data[-dynamic_buffer_size:].tolist())
            if len(old_ch2_data) > 0:
                self.ch2_buffer.append(old_ch2_data[-dynamic_buffer_size:].tolist())

            self.serial_manager.log_message.emit(
                f"  ✓ 流模式缓冲区: {dynamic_buffer_size:,} 点 "
                f"({'10秒窗口' if dynamic_buffer_size > 10000 else '6周期窗口' if dynamic_buffer_size < 10000 else '10K点'})"
            )

        # 3. 更新内部变量
        self.sample_rate = params["sample_rate"]
        self.div_factor = params["div_factor"]

        # 4. 🔥 流模式不清空缓冲区，Buffer模式清空
        if self.current_mode != "stream":
            self.ch1_buffer.clear()
            self.ch2_buffer.clear()

        # 重置显示更新时间戳
        self._last_display_update = 0

        # 计算并显示新的FPGA配置
        base_freq = 50000000  # 50MHz基准
        actual_rate = base_freq / div_factor

        self.serial_manager.log_message.emit(
            f"  ✓ 新分频系数: div_set={div_factor} "
            f"(基准50MHz ÷ {div_factor} = {actual_rate:,.0f} Hz)"
        )
        self.serial_manager.log_message.emit("✅ [重配置] 参数更新完成，继续采集")

        # 🔥 根据参数决定是否调整X轴
        if adjust_xaxis:
            # Auto按钮触发：下次显示时调整X轴范围
            self.need_adjust_xaxis = True
        else:
            # 频率变化触发：保持当前X轴范围不变
            pass

    def update_measurements(self):
        """更新测量参数（🔥 V5.1增强：支持双通道测量）"""
        # 🔥 V8.7.34: 只要有数据就更新电压测量，频率保持之前的值
        if not (self.ch1_buffer or self.ch2_buffer):
            return

        try:
            # ========== CH1 测量 ==========
            if self.ch1_enabled and self.ch1_buffer and len(self.ch1_buffer) >= 10:
                data = self.ch1_buffer.get_all()

                # 计算最大值和最小值
                vmax = np.max(data)
                vmin = np.min(data)
                vpp = vmax - vmin  # 峰峰值

                self.ch1_params["vmax"] = vmax
                self.ch1_params["vmin"] = vmin
                self.ch1_params["vpp"] = vpp

                # 更新显示
                self.ch1_vpp_label.setText(f"{vpp:.3f} V")
                self.ch1_vmax_label.setText(f"{vmax:.3f} V")
                self.ch1_vmin_label.setText(f"{vmin:.3f} V")

                # 计算Vrms（有效值）
                vrms = np.sqrt(np.mean((data - np.mean(data)) ** 2))
                self.ch1_params["vrms"] = vrms
                self.ch1_vrms_label.setText(f"{vrms:.3f} V")

                # 🔥 智能频率测量（多算法融合）
                self._update_frequency_measurement(data, channel=1)
            else:
                # CH1未启用,清空显示
                if not self.ch1_enabled:
                    self.ch1_vpp_label.setText("-- V")
                    self.ch1_vmax_label.setText("-- V")
                    self.ch1_vmin_label.setText("-- V")
                    self.ch1_vrms_label.setText("-- V")
                    self.ch1_freq_label.setText("-- Hz")  # 清空频率显示

            # ========== CH2 测量 ==========
            if self.ch2_enabled and self.ch2_buffer and len(self.ch2_buffer) >= 10:
                data = self.ch2_buffer.get_all()

                # 计算最大值和最小值
                vmax = np.max(data)
                vmin = np.min(data)
                vpp = vmax - vmin  # 峰峰值

                self.ch2_params["vmax"] = vmax
                self.ch2_params["vmin"] = vmin
                self.ch2_params["vpp"] = vpp

                # 更新显示
                self.ch2_vpp_label.setText(f"{vpp:.3f} V")
                self.ch2_vmax_label.setText(f"{vmax:.3f} V")
                self.ch2_vmin_label.setText(f"{vmin:.3f} V")

                # 计算Vrms（有效值）
                vrms = np.sqrt(np.mean((data - np.mean(data)) ** 2))
                self.ch2_params["vrms"] = vrms
                self.ch2_vrms_label.setText(f"{vrms:.3f} V")

                # 🔥 智能频率测量（多算法融合）
                self._update_frequency_measurement(data, channel=2)
            else:
                # CH2未启用,清空显示
                if not self.ch2_enabled:
                    self.ch2_vpp_label.setText("-- V")
                    self.ch2_vmax_label.setText("-- V")
                    self.ch2_vmin_label.setText("-- V")
                    self.ch2_vrms_label.setText("-- V")
                    self.ch2_freq_label.setText("-- Hz")  # 清空频率显示

        except Exception as e:
            if self.serial_manager:
                self.serial_manager.log_message.emit(f"❌ 测量参数计算错误: {e}")

    def _update_frequency_measurement(self, data, channel=1):
        """
        🔥 V2.0: 双通道FPGA硬件测频（纯硬件方案）

        策略：完全依赖FPGA硬件测频（0-50MHz，高精度）
        - FPGA每秒自动发送双通道频率数据
        - 上位机只负责更新参数字典和tooltip
        - 频率显示由 on_frequency_data_received 统一管理

        Args:
            data: numpy数组，信号数据（保留参数，未使用）
            channel: 通道号（1=CH1, 2=CH2）
        """
        try:
            if channel == 1 and self.ch1_enabled:
                if self.fpga_measured_freq is not None and self.fpga_measured_freq > 0:
                    # CH1：使用FPGA硬件测频
                    freq = self.fpga_measured_freq
                    self.ch1_params["frequency"] = freq
                    self.ch1_freq_label.setToolTip(
                        f"通道: CH1\n测量方法: FPGA硬件\n置信度: 高\n频率: {freq} Hz"
                    )

            elif channel == 2 and self.ch2_enabled:
                if (
                    self.fpga_measured_freq_ch2 is not None
                    and self.fpga_measured_freq_ch2 > 0
                ):
                    # CH2：使用FPGA硬件测频
                    freq = self.fpga_measured_freq_ch2
                    self.ch2_params["frequency"] = freq
                    self.ch2_freq_label.setToolTip(
                        f"通道: CH2\n测量方法: FPGA硬件\n置信度: 高\n频率: {freq} Hz"
                    )

        except Exception as e:
            if self.serial_manager:
                self.serial_manager.log_message.emit(f"❌ CH{channel}频率测量错误: {e}")

    def _software_frequency_measurement(self, data):
        """
        软件频率测量（多算法融合）
        返回: (频率Hz, 方法名, 置信度)
        """
        if len(data) < 100:
            return None, "数据不足", "无"

        # 算法1：FFT峰值检测（适合100Hz-25MHz）
        fft_freq = self._measure_frequency_by_fft(data)

        # 算法2：自相关（适合10Hz-1MHz，低频优化）
        autocorr_freq = self._measure_frequency_by_autocorrelation(data)

        # 算法3：过零检测（快速但不够准确）
        zerocross_freq = self._measure_frequency_by_zerocross(data)

        # 🎯 智能选择算法
        candidates = []

        if fft_freq:
            if fft_freq < 100:
                candidates.append(
                    (autocorr_freq if autocorr_freq else zerocross_freq, "自相关", "中")
                )
            elif fft_freq < 1_000_000:
                candidates.append((fft_freq, "FFT", "高"))
            else:
                candidates.append((fft_freq, "FFT", "中"))

        if autocorr_freq and autocorr_freq < 1_000_000:
            candidates.append((autocorr_freq, "自相关", "中"))

        if zerocross_freq:
            candidates.append((zerocross_freq, "过零", "低"))

        # 选择最可靠的结果
        if len(candidates) >= 2:
            # 多个算法结果接近，取平均值
            freqs = [c[0] for c in candidates if c[0]]
            if len(freqs) >= 2:
                mean_freq = np.mean(freqs)
                std_freq = np.std(freqs)
                if std_freq / mean_freq < 0.1:  # 标准差<10%
                    return mean_freq, "多算法融合", "高"
                else:
                    # 结果差异较大，选FFT
                    return fft_freq if fft_freq else autocorr_freq, "FFT(单一)", "中"

        # 单一算法
        if candidates:
            return candidates[0]

        return None, "测量失败", "无"

    def _measure_frequency_by_fft(self, data):
        """FFT峰值检测法测频"""
        try:
            if len(data) < 256:
                return None

            # 使用最新的2048点进行FFT
            n_fft = min(2048, len(data))
            signal = data[-n_fft:]

            # 去除直流
            signal = signal - np.mean(signal)

            # 应用汉宁窗
            window = np.hanning(n_fft)
            signal_windowed = signal * window

            # FFT
            fft_result = np.fft.fft(signal_windowed)
            fft_magnitude = np.abs(fft_result[: n_fft // 2])

            # 频率轴
            freq_axis = np.fft.fftfreq(n_fft, d=1.0 / self.sample_rate)[: n_fft // 2]

            # 找到峰值（跳过DC分量）
            if len(fft_magnitude) > 10:
                peak_idx = np.argmax(fft_magnitude[5:]) + 5  # 跳过前5个点
                peak_freq = freq_axis[peak_idx]

                # 抛物线插值提高精度
                if 1 < peak_idx < len(fft_magnitude) - 1:
                    alpha = fft_magnitude[peak_idx - 1]
                    beta = fft_magnitude[peak_idx]
                    gamma = fft_magnitude[peak_idx + 1]

                    p = 0.5 * (alpha - gamma) / (alpha - 2 * beta + gamma)
                    peak_freq = freq_axis[peak_idx] + p * (freq_axis[1] - freq_axis[0])

                return abs(peak_freq) if peak_freq > 0 else None

        except:
            return None

        return None

    def _measure_frequency_by_autocorrelation(self, data):
        """自相关法测频（低频优化）"""
        try:
            if len(data) < 200:
                return None

            # 使用最新的4096点
            n = min(4096, len(data))
            signal = data[-n:]

            # 去除直流和趋势
            signal = signal - np.mean(signal)

            # 计算自相关
            autocorr = np.correlate(signal, signal, mode="full")
            autocorr = autocorr[len(autocorr) // 2 :]  # 只取正延迟部分

            # 归一化
            autocorr = autocorr / autocorr[0]

            # 找到第一个峰值（跳过中心峰）
            # 寻找范围：至少间隔10个采样点
            min_lag = max(10, int(self.sample_rate / 50000))  # 最高50kHz
            max_lag = min(len(autocorr) - 1, int(self.sample_rate / 10))  # 最低10Hz

            if max_lag > min_lag:
                peaks = []
                for i in range(min_lag, max_lag):
                    if (
                        autocorr[i] > autocorr[i - 1]
                        and autocorr[i] > autocorr[i + 1]
                        and autocorr[i] > 0.3
                    ):  # 阈值
                        peaks.append(i)

                if peaks:
                    period_samples = peaks[0]
                    frequency = self.sample_rate / period_samples
                    return frequency

        except:
            return None

        return None

    def _measure_frequency_by_zerocross(self, data):
        """过零检测法测频（快速但精度一般）"""
        try:
            if len(data) < 100:
                return None

            # 去除直流
            signal = data - np.mean(data)

            # 检测过零点
            zero_crossings = np.where(np.diff(np.sign(signal)))[0]

            if len(zero_crossings) < 4:
                return None

            # 计算相邻过零点间隔（半周期）
            intervals = np.diff(zero_crossings)

            # 过滤异常值
            median_interval = np.median(intervals)
            valid_intervals = intervals[
                (intervals > median_interval * 0.5)
                & (intervals < median_interval * 2.0)
            ]

            if len(valid_intervals) > 0:
                avg_half_period = np.mean(valid_intervals)
                frequency = self.sample_rate / (2 * avg_half_period)
                return frequency

        except:
            return None

        return None

    # ============================================================================
    # 以太网数据接收处理
    # ============================================================================

    def on_ethernet_log(self, message):
        """以太网日志消息处理"""
        if self.serial_manager:
            self.serial_manager.log_message.emit(message)

    def on_ethernet_adc_data(self, new_packet_bytes):
        """
        处理以太网ADC数据

        🔥 V8.7.30: Buffer模式和流模式都使用协议头
        - Buffer模式: 16字节协议头 + 1024字节ADC数据 = 1040字节总包
        - 流模式: 16字节协议头 + 1008字节ADC数据 = 1024字节总包

        协议格式 (两种模式通用):
        - [0-1]    帧头: 0x5A 0xAA
        - [2-3]    包序号: 16位大端序
        - [4]      标志: Bit1=最后一包, Bit0=相位标志 (0=CH1首, 1=CH2首)
        - [5]      通道使能: Bit1=CH2, Bit0=CH1
        - [6-7]    总包数: 16位大端序 (FPGA计算的total_packets)
        - [8-9]    当前包号: 16位大端序 (FPGA的current_packet,1-based)
        - [10-15]  保留字节
        - [16-...]  ADC数据: Buffer模式1024字节, 流模式1008字节
        """
        if not self.is_capturing or not new_packet_bytes:
            return

        try:
            # 🔥 V8.7.30: 两种模式都检查帧头
            if len(new_packet_bytes) < 16:
                if self.serial_manager:
                    self.serial_manager.log_message.emit(
                        f"⚠️ 丢弃错误包: {len(new_packet_bytes)}字节 (预期≥1024)"
                    )
                return

            # 检查帧头 (0x5A 0xAA)
            if new_packet_bytes[0] != 0x5A or new_packet_bytes[1] != 0xAA:
                if not hasattr(self, "_v730_header_errors"):
                    self._v730_header_errors = 0
                self._v730_header_errors += 1
                if self._v730_header_errors <= 5 and self.serial_manager:
                    hex_preview = " ".join(
                        f"{new_packet_bytes[i]:02X}"
                        for i in range(min(32, len(new_packet_bytes)))
                    )
                    self.serial_manager.log_message.emit(
                        f"⚠️ 帧头错误#{self._v730_header_errors}: {new_packet_bytes[0]:02X} {new_packet_bytes[1]:02X} (预期5A AA)\n    前32字节: {hex_preview}"
                    )
                return

            # 解析协议头 (两种模式通用)
            packet_seq = (new_packet_bytes[2] << 8) | new_packet_bytes[3]
            flag = new_packet_bytes[4]
            ch_enable = new_packet_bytes[5]
            fpga_total_packets = (new_packet_bytes[6] << 8) | new_packet_bytes[
                7
            ]  # 🔥 FPGA计算的总包数
            fpga_current_packet = (new_packet_bytes[8] << 8) | new_packet_bytes[
                9
            ]  # 🔥 FPGA当前包号
            is_last_packet = (flag >> 1) & 0x01  # 🔥 Bit1=最后一包标志

            # 🔥 V8.7.30: 根据模式选择ADC数据范围
            if self.current_mode == "buffer":
                # ========== Buffer模式：16头+1024数据=1040字节（最后一包可能更短）==========
                # 🔥 V8.7.41修复：最后一包可能不足1024字节，按实际长度读取
                if is_last_packet:
                    # 最后一包：按实际包长读取（至少16字节协议头）
                    if len(new_packet_bytes) < 16:
                        if self.serial_manager:
                            self.serial_manager.log_message.emit(
                                f"⚠️ [Buffer] 最后包长度错误: {len(new_packet_bytes)}字节 (至少需要16字节协议头)"
                            )
                        return
                    adc_data = new_packet_bytes[16:]  # ✅ 读取剩余所有数据
                else:
                    # 非最后一包：固定1040字节
                    if len(new_packet_bytes) != 1040:
                        if self.serial_manager:
                            self.serial_manager.log_message.emit(
                                f"⚠️ [Buffer] 包长度错误: {len(new_packet_bytes)}字节 (预期1040)"
                            )
                        return
                    adc_data = new_packet_bytes[16:1040]  # 1024字节ADC数据

                # Buffer模式统计
                self.buffer_received_packets += 1

                # 🔥 V8.7.53: 多包策略 - 丢弃最后一个冗余包
                # 策略：FPGA多发1包，上位机接收N+1包，只处理前N包数据，丢弃第N+1包
                # 原因：最后一包可能包含DDR3未初始化数据或转换器残留数据
                is_redundant_packet = (
                    hasattr(self, "buffer_expected_packets")
                    and self.buffer_expected_packets > 0
                    and self.buffer_received_packets >= self.buffer_expected_packets
                )

                if is_redundant_packet:
                    # 丢弃冗余包，不写入缓冲区
                    if self.serial_manager:
                        self.serial_manager.log_message.emit(
                            f"🗑️ 丢弃冗余包#{self.buffer_received_packets} (多包策略保护)"
                        )
                    # 触发自动停止
                    if (
                        not hasattr(self, "_auto_stop_triggered")
                        or not self._auto_stop_triggered
                    ):
                        self._auto_stop_triggered = True
                        if self.serial_manager:
                            self.serial_manager.log_message.emit(
                                f"✅ [Buffer模式] 采集完成！收到{self.buffer_received_packets}包 (触发原因:上位机包数达到)"
                            )
                        from PySide6.QtCore import QTimer

                        QTimer.singleShot(100, self.auto_stop_buffer_capture)
                    return  # 🔥 直接返回，不处理此包数据

                # 🔥 V8.7.33: 日志显示上位机预期包数（掩耳盗铃：不显示FPGA错误的计算值）
                should_log = (
                    self.buffer_received_packets <= 10
                    or self.buffer_received_packets
                    >= (self.buffer_expected_packets - 5)
                    or is_last_packet
                )
                if self.serial_manager and should_log:
                    self.serial_manager.log_message.emit(
                        f"📦 Buffer包#{self.buffer_received_packets}/{self.buffer_expected_packets-1}, "
                        f"ADC数据={len(adc_data)}字节"
                    )

                # 🔥 V8.7.53: 移除旧的自动停止逻辑（已在上面的冗余包检测中处理）
                # 注：由于采用多包策略，不再依赖FPGA的last_packet标志

            else:
                # ========== 流模式：16头+1008数据=1024字节 ==========
                if len(new_packet_bytes) != 1024:
                    if self.serial_manager:
                        self.serial_manager.log_message.emit(
                            f"⚠️ [流模式] 包长度错误: {len(new_packet_bytes)}字节 (预期1024)"
                        )
                    return

                adc_data = new_packet_bytes[16:1024]  # 1008字节ADC数据

                # 🔥 V8.6.31: 增强丢包检测（详细统计）
                if not hasattr(self, "_last_packet_seq"):
                    self._last_packet_seq = None
                    self._packet_loss_count = 0
                    self._packet_loss_events = []
                    self._total_received_packets = 0
                    self._first_seq_logged = False

                self._total_received_packets += 1

                # 首包日志
                if not self._first_seq_logged:
                    self._first_seq_logged = True
                    if self.serial_manager:
                        self.serial_manager.log_message.emit(
                            f"📡 [序列号监控] 首包序列号: {packet_seq}"
                        )

                if self._last_packet_seq is not None:
                    expected_seq = (self._last_packet_seq + 1) & 0xFFFF
                    if packet_seq != expected_seq:
                        if packet_seq > self._last_packet_seq:
                            lost_count = packet_seq - expected_seq
                        else:
                            lost_count = (0x10000 - expected_seq) + packet_seq

                        self._packet_loss_count += lost_count
                        loss_event = {
                            "packet_num": self._total_received_packets,
                            "last_seq": self._last_packet_seq,
                            "curr_seq": packet_seq,
                            "lost": lost_count,
                        }
                        self._packet_loss_events.append(loss_event)

                        event_count = len(self._packet_loss_events)
                        if event_count <= 20 or event_count % 10 == 0:
                            if self.serial_manager:
                                loss_rate = (
                                    self._packet_loss_count
                                    / self._total_received_packets
                                ) * 100
                                self.serial_manager.log_message.emit(
                                    f"⚠️ [丢包#{event_count}] 包#{self._total_received_packets}: "
                                    f"序列号 {self._last_packet_seq}→{packet_seq} "
                                    f"(丢失{lost_count}包, 累计丢包率{loss_rate:.3f}%)"
                                )

                self._last_packet_seq = packet_seq

                # 流模式：提取ADC数据 (字节16-1023, 共1008字节)
                adc_data = new_packet_bytes[16:1024]

            # ========== 统一处理ADC数据 ==========
            # 🔥 V8.7.30修正: Buffer和流模式都有16字节协议头
            # Buffer模式: 16字节协议头 + 1024字节ADC数据 = 1040字节总包
            # 流模式: 16字节协议头 + 1008字节ADC数据 = 1024字节总包
            # ADC数据部分格式相同: [CH2,CH1,CH2,CH1...] 8位交织

            # 转换为电压: 8位ADC (0-255) -> -5V到+5V
            voltage_data = [(v / 255.0) * 10.0 - 5.0 for v in adc_data]

            # 根据通道使能解析数据
            # 🔥 V8.7.50关键修复: 纠正双通道索引错误
            #
            # 数据流程解析:
            # 1. FPGA交织器输出16位: interleaved_data = {CH2[7:0], CH1[7:0]}
            # 2. 16位→8位转换: 先发低字节data[7:0]，后发高字节data[15:8]
            # 3. 字节流顺序: [CH1(低), CH2(高), CH1(低), CH2(高), ...]
            # 4. 上位机解析: 偶数索引=CH1, 奇数索引=CH2
            #
            # 单通道模式数据重复，任意索引都可以
            ch1_samples = []
            ch2_samples = []

            if self.ch1_enabled and self.ch2_enabled:
                # ✅ 双通道模式: [CH1, CH2, CH1, CH2, ...]
                # 16位转8位先发低字节(CH1)，后发高字节(CH2)
                ch1_samples = voltage_data[0::2]  # 偶数索引=CH1 ✅
                ch2_samples = voltage_data[1::2]  # 奇数索引=CH2 ✅
            elif self.ch1_enabled:
                # ✅ 单CH1模式: {CH1,CH1} → [CH1, CH1, ...] 全是CH1
                ch1_samples = voltage_data[0::2]
            elif self.ch2_enabled:
                # ✅ 单CH2模式: {CH2,CH2} → [CH2, CH2, ...] 全是CH2
                ch2_samples = voltage_data[0::2]

            # 写入缓冲区
            if self.ch1_enabled and len(ch1_samples) > 0:
                self.ch1_buffer.append(ch1_samples)

            if self.ch2_enabled and len(ch2_samples) > 0:
                self.ch2_buffer.append(ch2_samples)

            # 7. 性能监控
            if not hasattr(self, "_v70_packet_count"):
                self._v70_packet_count = 0
            self._v70_packet_count += 1

            if self._v70_packet_count % 2000 == 0 and self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"📊 [V8.6.33] 已处理 {self._v70_packet_count} 个包，"
                    f"CH1缓冲: {len(self.ch1_buffer)}, CH2缓冲: {len(self.ch2_buffer)}"
                )

            # 🔥 V8.6.33: 移除显示更新逻辑，改用QTimer定时拉取
            # 优势：
            # 1. 解耦数据接收和显示刷新，UDP线程专注接收
            # 2. 避免Qt信号队列积压(2835 emit/s → 10-20次/s刷新)
            # 3. 显示始终读取RingBuffer最新数据，不会"跳到旧数据"
            #
            # 原逻辑问题：每个UDP包都进入此函数做时间检查，
            # 即使不刷新也消耗CPU，且Qt信号队列积压导致处理延迟数秒

            # ⚠️ 注意：display_timer (100ms) 会调用 update_display() → update_waveform_display()

        except Exception as e:
            if self.serial_manager:
                self.serial_manager.log_message.emit(f"⚠️ [V7.0] 解析错误: {e}")

    def auto_stop_buffer_capture(self):
        """
        Buffer模式自动停止采集（V8.7.35关键修复：正确重置FPGA状态）

        触发条件：接收到预期数量的UDP包
        功能：完全停止采集，用户可查看波形和测量值

        🔥 V8.7.35修复核心问题：
        - 必须发送CDC停止命令(0x24)来重置FPGA DDR3状态机
        - 必须重新设置Buffer模式(0x20)为下次采集准备
        - 必须清零包计数器，确保下次采集从0开始
        """
        # 🔥 防止重复调用（多个UDP包可能同时触发）
        if hasattr(self, "_is_auto_stopping") and self._is_auto_stopping:
            return
        self._is_auto_stopping = True

        if self.serial_manager:
            self.serial_manager.log_message.emit(
                f"🎯 Buffer采集完成，共{self.buffer_received_packets}包，"
                f"CH1={len(self.ch1_buffer)}点, CH2={len(self.ch2_buffer)}点"
            )

        # 🔥 V8.7.35关键修复1：停止UDP接收器（防止新数据进入）
        if self.ethernet_receiver and self.ethernet_receiver.running:
            self.ethernet_receiver.stop()
            if self.serial_manager:
                self.serial_manager.log_message.emit("  ✓ UDP接收器已停止")

        import time

        time.sleep(0.05)  # 等待UDP线程完全停止

        # 🔥 V8.7.35关键修复2：发送停止命令，重置FPGA DDR3状态机
        # 这会让DDR3状态机从DDR3_DONE强制返回DDR3_IDLE
        self.serial_manager.send_command(0x24)
        if self.serial_manager:
            self.serial_manager.log_message.emit(
                "  ✓ 已发送停止命令(0x24)，重置FPGA状态机"
            )
        time.sleep(0.1)  # 等待FPGA状态机复位（关键！）

        # 🔥 V8.7.35关键修复3：重新设置Buffer模式，为下次采集准备
        # 这确保FPGA内部的adc_mode保持正确
        payload_0x20 = bytes([1])  # 1=Buffer模式
        self.serial_manager.send_command(0x20, payload_0x20)
        if self.serial_manager:
            self.serial_manager.log_message.emit("  ✓ 已重新设置Buffer模式(0x20)")
        time.sleep(0.05)

        # 🔥 V8.7.35关键修复4：清零包计数器
        self.buffer_received_packets = 0
        self.buffer_expected_packets = 0
        if self.serial_manager:
            self.serial_manager.log_message.emit("  ✓ 包计数器已清零")

        # 🔥 V8.7.34：强制更新一次测量面板（电压值）
        self.update_measurements()

        # 🔥 V8.7.35：保持is_capturing=True，让测量面板继续更新
        # 只修改UI按钮状态，不停止测量
        self.toggle_btn.setText("▶ 启动")
        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #5cb85c;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #4cae4c; }
        """
        )

        # 重置停止标志
        self._is_auto_stopping = False

        # 🔥 彻底停止采集，确保FPGA完全复位
        if self.ethernet_receiver.running:
            self.ethernet_receiver.stop()

        # 发送停止命令给FPGA
        self.serial_manager.send_command(0x24)

        # 重置采集状态
        self.is_capturing = False

        # 🔥 V8.7.50: 清除缓存的频率数据，强制下次采集重新测频
        # 这是关键修复：防止使用错误的缓存频率导致参数计算错误
        self.fpga_measured_freq = None
        self.fpga_measured_freq_ch2 = None
        self.last_measured_freq = None

        # 重置模式为Stream（确保FPGA DDR3状态机复位）
        import time

        time.sleep(0.05)
        self.serial_manager.send_command(0x20, bytes([0]))  # 0=Stream模式

        # 显示完成消息
        if self.serial_manager:
            self.serial_manager.log_message.emit(
                "✅ Buffer模式已完成并重置。点击'启动'可再次采集。"
            )

    def on_ethernet_adc_data_old_adaptive(self, adc_data):
        """
        处理从以太网接收到的ADC数据（🔥 V5.4: 修复字节对齐问题）

        确认字节序: [CH2][CH1][CH2][CH1]... (FPGA interleaved_data格式)

        Args:
            adc_data: list[int] - 8位ADC原始数据（0-255）
        """
        if not self.is_capturing or not adc_data:
            return

        try:
            # 🔥🔥🔥 V5.7修复：标准化UDP包长度处理
            # UDP包应该固定1024字节，如果不是说明有问题
            original_len = len(adc_data)

            # 检查包长度异常
            if original_len != 1024:
                if not hasattr(self, "_abnormal_packet_count"):
                    self._abnormal_packet_count = 0
                self._abnormal_packet_count += 1

                # 仅记录前10次异常
                if self._abnormal_packet_count <= 10 and self.serial_manager:
                    self.serial_manager.log_message.emit(
                        f"⚠️ [UDP包异常] 包#{self._debug_packet_count if hasattr(self, '_debug_packet_count') else '?'}大小{original_len}字节（期望1024）"
                    )

            # 确保长度为偶数
            if len(adc_data) % 2 == 1:
                adc_data = adc_data[:-1]

            # 🔥🔥🔥 强制调试：第1个包和每500个包
            if not hasattr(self, "_debug_packet_count"):
                self._debug_packet_count = 0
            self._debug_packet_count += 1

            # 🔥 V8.7.24: 前3个包的诊断日志（验证FPGA相位）
            if self._debug_packet_count <= 3 and self.serial_manager:
                hex_preview = " ".join(f"{b:02X}" for b in adc_data[:32])
                self.serial_manager.log_message.emit(
                    f"🔬 包#{self._debug_packet_count} 前32字节: {hex_preview}, 总长{len(adc_data)}"
                )

            # 🔥 V5.16性能优化：大幅降低调试频率，从500包→5000包（减少90%日志）
            should_debug = (
                self._debug_packet_count == 1 or self._debug_packet_count % 5000 == 1
            )

            if should_debug and self.serial_manager:
                # 🔥🔥 V5.12增强调试：检查完整数据流，不只是前32字节
                # 分析前128字节，每16字节一行，更容易看出规律
                debug_len = min(len(adc_data), 256)  # 最多看256字节

                # 16进制显示（每16字节一行）
                hex_lines = []
                for i in range(0, debug_len, 16):
                    chunk = adc_data[i : i + 16]
                    hex_line = " ".join(f"{b:02X}" for b in chunk)
                    hex_lines.append(f"   [{i:04d}] {hex_line}")
                hex_str = "\n".join(hex_lines)

                # ✅ V5.17: 移除详细字节流分析（FPGA已根治相位问题）
                # 仅保留简化的包统计信息
                pass

            # 转换为电压值：128=0V(中值), 0=-5V, 255=+5V
            # 🔥 修复V5.4：使用正确的电压转换公式
            # 公式：V = (ADC / 255.0) * 10.0 - 5.0
            voltage_data = [(v / 255.0) * 10.0 - 5.0 for v in adc_data]

            # 🔥 根据通道使能状态解析数据
            if self.ch1_enabled and self.ch2_enabled:
                # 🔥 V8.6.27关键修复：移除相位自适应检测，使用FPGA固定格式
                #
                # FPGA硬件固定输出：
                #   interleaved_data <= {ch2_buffer, ch1_buffer}
                #   16to8转换: [低字节CH1, 高字节CH2, 低字节CH1, 高字节CH2...]
                #   UDP流: [CH1, CH2, CH1, CH2...]
                #
                # 错误的相位检测问题：
                #   - 之前通过std比较判断相位，但这在频率差异大时会误判
                #   - 例如：CH1=10kHz(低频), CH2=100kHz(高频)
                #     → 奇数位(CH2)的std更大 → 误判为"奇数=CH1" → 通道翻转！
                #
                # 正确策略：
                #   - FPGA格式固定，上位机无需检测，直接使用固定索引
                #   - 偶数索引[0::2] = CH1
                #   - 奇数索引[1::2] = CH2

                ch1_samples = voltage_data[0::2]  # 偶数索引 = CH1 (固定)
                ch2_samples = voltage_data[1::2]  # 奇数索引 = CH2 (固定)

                # 🔥 V8.6.30调试：检查是否有异常值
                if not hasattr(self, "_anomaly_check_count"):
                    self._anomaly_check_count = 0
                self._anomaly_check_count += 1

                if self._anomaly_check_count <= 10:
                    ch1_min, ch1_max = min(ch1_samples), max(ch1_samples)
                    ch2_min, ch2_max = min(ch2_samples), max(ch2_samples)

                    # 检查是否有超出±5V的异常值
                    ch1_anomaly = [i for i, v in enumerate(ch1_samples) if abs(v) > 5.5]
                    ch2_anomaly = [i for i, v in enumerate(ch2_samples) if abs(v) > 5.5]

                    if (ch1_anomaly or ch2_anomaly) and self.serial_manager:
                        self.serial_manager.log_message.emit(
                            f"⚠️ [异常值检测] 包#{self._anomaly_check_count}\n"
                            f"   CH1: 范围({ch1_min:.2f}~{ch1_max:.2f}V) 异常索引={ch1_anomaly}\n"
                            f"   CH2: 范围({ch2_min:.2f}~{ch2_max:.2f}V) 异常索引={ch2_anomaly}"
                        )

                # 🔥 V8.6.27调试：检查CH2是否有重复值
                if not hasattr(self, "_ch2_debug_count"):
                    self._ch2_debug_count = 0
                self._ch2_debug_count += 1

                if self._ch2_debug_count <= 5:
                    # 检查前20个CH2样本
                    ch2_first_20 = ch2_samples[:20]
                    ch2_hex = " ".join([f"{int((v+5)*25.5):02X}" for v in ch2_first_20])

                    # 检查重复
                    ch2_unique = []
                    for i, val in enumerate(ch2_first_20):
                        if i == 0 or abs(val - ch2_first_20[i - 1]) > 0.01:
                            ch2_unique.append(f"[{i}]={val:.2f}")

                    if self.serial_manager:
                        self.serial_manager.log_message.emit(
                            f"\n🔍 [V8.6.27调试] 包#{self._ch2_debug_count}\n"
                            f"   CH2前20样本(HEX): {ch2_hex}\n"
                            f"   CH2变化点: {' '.join(ch2_unique)}\n"
                            f"   CH1样本数={len(ch1_samples)}, CH2样本数={len(ch2_samples)}"
                        )

                # 🔥 V5.9: 原子性更新buffer
                self.data_updating = True
                try:
                    self.ch1_buffer.append(ch1_samples)
                    self.ch2_buffer.append(ch2_samples)
                finally:
                    self.data_updating = False

                # 🔥 V8.6.27调试：简化日志（移除相位检测相关）
                if should_debug and self.serial_manager:
                    self.serial_manager.log_message.emit(
                        f"📦 [V8.6.27] 包#{self._debug_packet_count}: "
                        f"CH1={len(ch1_samples)}样本, CH2={len(ch2_samples)}样本 | "
                        f"Buffer: CH1={len(self.ch1_buffer)}, CH2={len(self.ch2_buffer)}"
                    )

            elif self.ch1_enabled and not self.ch2_enabled:
                # 🔥 V5.7优化：单CH1模式FPGA发送重复CH1 [CH1][CH1][CH1][CH1]...
                # 两个字节都是CH1，提取偶数索引即可（或全取，因为都一样）
                ch1_samples = voltage_data[0::2]  # 偶数索引: CH1（奇数索引也是CH1）
                self.ch1_buffer.append(ch1_samples)

                # CH2清空
                self.ch2_buffer.clear()

                # 🔥🔥🔥 调试：单通道模式
                if should_debug and self.serial_manager:
                    ch1_min, ch1_max = min(ch1_samples), max(ch1_samples)
                    ch1_avg = sum(ch1_samples) / len(ch1_samples)
                    self.serial_manager.log_message.emit(
                        f"\n【单CH1模式解析 - 修复版】\n"
                        f"  原始包: {len(voltage_data)}点 → 提取CH1: {len(ch1_samples)}点\n"
                        f"  CH1: 范围({ch1_min:.2f}~{ch1_max:.2f}V), 平均{ch1_avg:.2f}V\n"
                        f"  (已过滤偶数索引的0V填充)\n"
                        f"{'='*60}\n"
                    )

            elif not self.ch1_enabled and self.ch2_enabled:
                # 🔥 V5.7优化：单CH2模式FPGA发送重复CH2 [CH2][CH2][CH2][CH2]...
                # 两个字节都是CH2，提取偶数索引即可（或全取，因为都一样）
                ch2_samples = voltage_data[0::2]  # 偶数索引: CH2（奇数索引也是CH2）
                self.ch2_buffer.append(ch2_samples)

                # CH1清空
                self.ch1_buffer.clear()

                # 🔥🔥🔥 调试：单通道模式
                if should_debug and self.serial_manager:
                    ch2_min, ch2_max = min(ch2_samples), max(ch2_samples)
                    ch2_avg = sum(ch2_samples) / len(ch2_samples)
                    self.serial_manager.log_message.emit(
                        f"\n【单CH2模式解析 - 修复版】\n"
                        f"  原始包: {len(voltage_data)}点 → 提取CH2: {len(ch2_samples)}点\n"
                        f"  CH2: 范围({ch2_min:.2f}~{ch2_max:.2f}V), 平均{ch2_avg:.2f}V\n"
                        f"  (已过滤奇数索引的0V填充)\n"
                        f"{'='*60}\n"
                    )

            else:
                # 双通道都禁用（不应该出现）
                return

            # RingBuffer自动管理容量，无需手动裁剪

        except Exception as e:
            if self.serial_manager:
                self.serial_manager.log_message.emit(f"⚠️ 数据解析错误: {e}")

    def send_channel_enable_command(self):
        """
        发送通道使能命令到FPGA（0x28命令）

        Payload格式：2字节
        - payload[0]: CH1使能（0=禁用，1=启用）
        - payload[1]: CH2使能（0=禁用，1=启用）
        """
        if not self.serial_manager or not self.serial_manager.is_connected():
            return

        try:
            # 构造payload
            ch1_val = 1 if self.ch1_enabled else 0
            ch2_val = 1 if self.ch2_enabled else 0
            payload = bytes([ch1_val, ch2_val])

            # 发送命令
            self.serial_manager.send_command(0x28, payload)

            # 日志
            status_str = []
            if self.ch1_enabled:
                status_str.append("CH1✓")
            if self.ch2_enabled:
                status_str.append("CH2✓")
            if not status_str:
                status_str = ["全部禁用"]

            self.serial_manager.log_message.emit(
                f"  ✓ 通道使能: {' '.join(status_str)} (0x28: {payload.hex()})"
            )

        except Exception as e:
            self.serial_manager.log_message.emit(f"⚠️ 发送通道使能命令失败: {e}")

    def on_ch1_enable_changed(self, state):
        """CH1使能状态改变（硬件级控制）"""
        self.ch1_enabled = state == Qt.Checked
        self.ch1_visible = self.ch1_enabled  # 同步UI显示状态

        # 更新曲线可见性
        self.ch1_curve.setVisible(self.ch1_visible)

        # 🔥 如果正在采集，立即发送命令更新FPGA
        if self.is_capturing:
            self.send_channel_enable_command()
            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"🔄 CH1已{('启用' if self.ch1_enabled else '禁用')}（实时更新）"
                )

        # 更新测量表格样式
        self._update_measurement_table_style()

        # 🔥 同时触发原有的通道变化逻辑（更新图例）
        self.on_channel_changed()

    def on_ch2_enable_changed(self, state):
        """CH2使能状态改变（硬件级控制）"""
        self.ch2_enabled = state == Qt.Checked
        self.ch2_visible = self.ch2_enabled  # 同步UI显示状态

        # 更新曲线可见性
        self.ch2_curve.setVisible(self.ch2_visible)

        # 🔥 如果正在采集，立即发送命令更新FPGA
        if self.is_capturing:
            self.send_channel_enable_command()
            if self.serial_manager:
                self.serial_manager.log_message.emit(
                    f"🔄 CH2已{('启用' if self.ch2_enabled else '禁用')}（实时更新）"
                )

        # 更新测量表格样式
        self._update_measurement_table_style()

        # 🔥 同时触发原有的通道变化逻辑（更新图例）
        self.on_channel_changed()

    def _update_stream_bandwidth_display(self):
        """🔥 V8.3: 流模式输入带宽固定显示（设计限制）"""
        if not hasattr(self, "stream_bandwidth_label"):
            return

        if self.current_mode != "stream":
            return

        # 🔥 V8.6.5: 固定显示双通道2MSPS×2 / 单通道4MSPS (已优化性能)
        self.stream_bandwidth_label.setText("输入带宽: 双通道2MSPS×2, 单通道4MSPS")

    def _update_measurement_table_style(self):
        """更新测量表格的样式（启用/禁用状态）"""
        # CH1样式
        if self.ch1_enabled:
            ch1_cell_style = """
                background: white;
                color: #333;
                font-size: 13px;
                font-weight: bold;
                border: 1px solid #ddd;
                border-top: none;
                border-right: none;
                padding: 6px;
                border-left: 3px solid #5cb85c;
            """
        else:
            ch1_cell_style = """
                background: #fafafa;
                color: #999;
                font-size: 12px;
                border: 1px solid #ddd;
                border-top: none;
                border-right: none;
                padding: 6px;
                border-left: 3px solid #ccc;
            """

        self.ch1_freq_label.setStyleSheet(
            ch1_cell_style.replace("border-right: none;", "")
        )
        self.ch1_vpp_label.setStyleSheet(
            ch1_cell_style.replace("border-right: none;", "")
        )
        self.ch1_vrms_label.setStyleSheet(
            ch1_cell_style.replace("border-right: none;", "")
        )
        self.ch1_vmax_label.setStyleSheet(
            ch1_cell_style.replace("border-right: none;", "")
        )
        self.ch1_vmin_label.setStyleSheet(ch1_cell_style)

        # CH2样式
        if self.ch2_enabled:
            ch2_cell_style = """
                background: white;
                color: #333;
                font-size: 13px;
                font-weight: bold;
                border: 1px solid #ddd;
                border-top: none;
                border-right: none;
                padding: 6px;
                border-left: 3px solid #f0ad4e;
            """
        else:
            ch2_cell_style = """
                background: #fafafa;
                color: #999;
                font-size: 12px;
                border: 1px solid #ddd;
                border-top: none;
                border-right: none;
                padding: 6px;
                border-left: 3px solid #ccc;
            """

        self.ch2_freq_label.setStyleSheet(
            ch2_cell_style.replace("border-right: none;", "")
        )
        self.ch2_vpp_label.setStyleSheet(
            ch2_cell_style.replace("border-right: none;", "")
        )
        self.ch2_vrms_label.setStyleSheet(
            ch2_cell_style.replace("border-right: none;", "")
        )
        self.ch2_vmax_label.setStyleSheet(
            ch2_cell_style.replace("border-right: none;", "")
        )
        self.ch2_vmin_label.setStyleSheet(ch2_cell_style)

    def _update_measurement_table_style(self):
        """更新测量表格的样式（启用/禁用状态）"""
        # CH1样式
        if self.ch1_enabled:
            ch1_style = (
                "background: white; color: #333; border-left: 3px solid #5cb85c;"
            )
        else:
            ch1_style = "background: #fafafa; color: #999; border-left: 3px solid #ccc;"

        self.ch1_freq_label.setStyleSheet(ch1_style)
        self.ch1_vpp_label.setStyleSheet(ch1_style)
        self.ch1_vrms_label.setStyleSheet(ch1_style)
        self.ch1_vmax_label.setStyleSheet(ch1_style)
        self.ch1_vmin_label.setStyleSheet(ch1_style)

        # CH2样式
        if self.ch2_enabled:
            ch2_style = (
                "background: white; color: #333; border-left: 3px solid #f0ad4e;"
            )
        else:
            ch2_style = "background: #fafafa; color: #999; border-left: 3px solid #ccc;"

        self.ch2_freq_label.setStyleSheet(ch2_style)
        self.ch2_vpp_label.setStyleSheet(ch2_style)
        self.ch2_vrms_label.setStyleSheet(ch2_style)
        self.ch2_vmax_label.setStyleSheet(ch2_style)
        self.ch2_vmin_label.setStyleSheet(ch2_style)

    # ============================================================================
    # 串口信号连接
    # ============================================================================
