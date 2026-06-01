#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PulseView SR 文件导出器
将逻辑分析仪采集的帧数据转换为 PulseView 可读的 SR 格式
"""

import zipfile
import struct
import os
import subprocess
from typing import List, Dict, Optional


class FrameParser:
    """数据帧解析器"""

    FRAME_HEADER = [0x5A, 0xA5]

    def __init__(self):
        self.frames = []

    def parse(self, raw_data: bytes) -> List[Dict]:
        """
        解析原始数据中的所有帧

        Args:
            raw_data: 原始字节数据

        Returns:
            解析后的帧列表 [{'seq': 0, 'payload': [...], 'valid': True}, ...]
        """
        frames = []
        i = 0
        data_len = len(raw_data)

        while i < data_len - 7:  # 至少需要帧头+序号+长度
            # 查找帧头 5A A5
            if (
                raw_data[i] == self.FRAME_HEADER[0]
                and raw_data[i + 1] == self.FRAME_HEADER[1]
            ):

                # 检查是否有足够的数据
                if i + 6 >= data_len:
                    break

                # 提取序号（小端序）
                seq = raw_data[i + 2] | (raw_data[i + 3] << 8)

                # 提取长度（小端序）
                length = raw_data[i + 4] | (raw_data[i + 5] << 8)

                # 检查完整帧是否存在
                frame_total_len = 6 + length + 1  # 头+序号+长度+载荷+校验和
                if i + frame_total_len > data_len:
                    break

                # 提取载荷
                payload = list(raw_data[i + 6 : i + 6 + length])

                # 提取校验和
                checksum = raw_data[i + 6 + length]

                # 验证校验和（FPGA从HEADER_1开始累加，包括帧头+序号+长度+载荷）
                calc_sum = sum(raw_data[i : i + 6 + length]) & 0xFF
                valid = calc_sum == checksum

                frames.append(
                    {
                        "seq": seq,
                        "length": length,
                        "payload": payload,
                        "checksum": checksum,
                        "calc_checksum": calc_sum,
                        "valid": valid,
                        "offset": i,
                    }
                )

                i += frame_total_len
            else:
                i += 1

        self.frames = frames
        return frames

    def get_statistics(self) -> Dict:
        """获取解析统计信息"""
        if not self.frames:
            return {
                "total_frames": 0,
                "valid_frames": 0,
                "error_frames": 0,
                "lost_frames": 0,
                "total_samples": 0,
            }

        valid_frames = sum(1 for f in self.frames if f["valid"])
        error_frames = len(self.frames) - valid_frames

        # 检测丢帧
        lost_count = 0
        if len(self.frames) > 1:
            expected_seq = self.frames[0]["seq"]
            for frame in self.frames:
                if frame["seq"] != expected_seq:
                    lost_count += frame["seq"] - expected_seq
                expected_seq = frame["seq"] + 1

        total_samples = sum(f["length"] for f in self.frames if f["valid"])

        return {
            "total_frames": len(self.frames),
            "valid_frames": valid_frames,
            "error_frames": error_frames,
            "lost_frames": lost_count,
            "total_samples": total_samples,
        }

    def extract_samples(self) -> List[int]:
        """
        从所有有效帧中提取原始采样数据

        Returns:
            采样点字节序列 [0x01, 0x00, 0x03, ...]
        """
        samples = []
        for frame in self.frames:
            if frame["valid"]:
                samples.extend(frame["payload"])
        return samples


class PulseViewExporter:
    """PulseView SR 文件导出器"""

    def __init__(self, sample_rate: int = 1_000_000, num_channels: int = 8):
        """
        初始化导出器

        Args:
            sample_rate: 采样率（Hz），默认 1 MSPS
            num_channels: 通道数量，默认 8
        """
        self.sample_rate = sample_rate
        self.num_channels = num_channels

        # 常见的 PulseView 安装路径
        self.pulseview_paths = [
            r"F:\PulseView\pulseview.exe",
            r"C:\Program Files\sigrok\PulseView\pulseview.exe",
            r"C:\Program Files (x86)\sigrok\PulseView\pulseview.exe",
            r"C:\Program Files\PulseView\pulseview.exe",
            "/usr/bin/pulseview",
            "/Applications/PulseView.app/Contents/MacOS/PulseView",
        ]

    def create_sr_file(self, samples: List[int], filename: str = "capture.sr") -> str:
        """
        创建 PulseView SR 格式文件

        Args:
            samples: 采样数据列表 [0x01, 0x00, ...]
            filename: 输出文件名

        Returns:
            创建的文件路径
        """
        # 🔍 数据验证和警告
        if len(samples) == 0:
            raise ValueError("采样数据为空！")

        # 检查数据是否全为同一值（可能是硬件未连接）
        unique_values = set(samples)
        if len(unique_values) == 1:
            print(f"⚠️  警告: 所有采样数据都是 0x{samples[0]:02X}")
            print(f"   这可能表示输入引脚未连接或信号一直保持不变")
            print(f"   PulseView可能会显示为平坦的波形")

        with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as zf:

            # 1. version 文件
            zf.writestr("version", "2")

            # 2. metadata 文件
            metadata_lines = [
                "[device 1]",
                "driver=demo",
                "capturefile=logic-1",
                f"samplerate={self.sample_rate} Hz",
                "unitsize=1",
                f"total probes={self.num_channels}",
            ]

            # 添加通道定义
            for i in range(self.num_channels):
                metadata_lines.append(f"probe{i+1}=CH{i}")

            metadata = "\n".join(metadata_lines) + "\n"
            zf.writestr("metadata", metadata)

            # 3. logic-1-1 二进制数据
            # 直接写入字节序列
            binary_data = bytes(samples)
            zf.writestr("logic-1-1", binary_data)

        return os.path.abspath(filename)

    def find_pulseview(self) -> Optional[str]:
        """查找 PulseView 可执行文件"""
        for path in self.pulseview_paths:
            if os.path.exists(path):
                return path
        return None

    def open_in_pulseview(self, sr_file: str) -> bool:
        """
        在 PulseView 中打开 SR 文件

        Args:
            sr_file: SR 文件路径

        Returns:
            是否成功打开
        """
        pulseview_path = self.find_pulseview()

        if pulseview_path:
            try:
                subprocess.Popen([pulseview_path, sr_file])
                return True
            except Exception as e:
                print(f"❌ 启动 PulseView 失败: {e}")
                return False
        else:
            print(f"⚠️  未找到 PulseView，请手动打开文件: {sr_file}")
            return False


class LogicAnalyzerPipeline:
    """逻辑分析仪数据处理管道"""

    def __init__(self, sample_rate: int = 1_000_000):
        """
        初始化数据处理管道

        Args:
            sample_rate: 采样率（Hz）
        """
        self.sample_rate = sample_rate
        self.parser = FrameParser()
        self.exporter = PulseViewExporter(sample_rate)

    def process_and_export(
        self, raw_data: bytes, output_file: str = "capture.sr", auto_open: bool = True
    ) -> Dict:
        """
        完整的数据处理和导出流程

        Args:
            raw_data: 原始数据
            output_file: 输出SR文件名
            auto_open: 是否自动打开 PulseView

        Returns:
            处理结果统计信息
        """
        print("=" * 60)
        print("逻辑分析仪数据处理管道")
        print("=" * 60)

        # 步骤1: 解析帧
        print("\n步骤1: 解析数据帧...")
        print(f"   原始数据大小: {len(raw_data)} 字节")

        frames = self.parser.parse(raw_data)
        stats = self.parser.get_statistics()

        print(f"   解析到帧数: {stats['total_frames']}")
        print(f"   ├─ 有效帧: {stats['valid_frames']}")
        print(f"   ├─ 错误帧: {stats['error_frames']}")
        print(f"   └─ 丢失帧: {stats['lost_frames']}")

        if stats["total_frames"] == 0:
            print("❌ 未找到有效的数据帧！")
            return stats

        # 显示前3帧信息
        print("\n   前3帧详情:")
        for i, frame in enumerate(frames[:3]):
            status = "✓" if frame["valid"] else "✗"
            print(
                f"   Frame {frame['seq']}: {status} "
                f"长度={frame['length']} "
                f"校验={frame['checksum']:02X} "
                f"(计算={frame['calc_checksum']:02X})"
            )

        # 步骤2: 提取采样数据
        print("\n步骤2: 提取原始采样数据...")
        samples = self.parser.extract_samples()
        print(f"   提取采样点: {len(samples)} 个")

        if len(samples) == 0:
            print("❌ 没有有效的采样数据！")
            return stats

        duration = len(samples) / self.sample_rate
        print(f"   采样时长: {duration:.6f} 秒 ({duration*1000:.3f} 毫秒)")

        # 显示数据统计
        sample_counts = {}
        for s in samples:
            sample_counts[s] = sample_counts.get(s, 0) + 1

        print(f"\n   数据分布 (前5种):")
        for val, count in sorted(
            sample_counts.items(), key=lambda x: x[1], reverse=True
        )[:5]:
            percentage = count * 100.0 / len(samples)
            binary = format(val, "08b")
            print(f"   0x{val:02X} ({binary}): {count} 次 ({percentage:.1f}%)")

        # 步骤3: 生成 SR 文件
        print("\n步骤3: 生成 PulseView SR 文件...")
        sr_path = self.exporter.create_sr_file(samples, output_file)
        file_size = os.path.getsize(sr_path)
        print(f"   ✅ 文件已保存: {sr_path}")
        print(f"   文件大小: {file_size:,} 字节 ({file_size/1024:.2f} KB)")

        # 步骤4: 打开 PulseView
        if auto_open:
            print("\n步骤4: 打开 PulseView...")
            if self.exporter.open_in_pulseview(sr_path):
                print("   ✅ PulseView 已启动")
            else:
                print(f"   ⚠️  请手动打开 PulseView 并导入文件:")
                print(f"      File → Open → 选择 {sr_path}")

        print("\n" + "=" * 60)
        print("处理完成！")
        print("=" * 60)

        # 添加文件路径到统计信息
        stats["output_file"] = sr_path
        stats["file_size"] = file_size
        stats["duration"] = duration

        return stats


# ============ 便捷函数 ============


def export_raw_to_sr(samples: List[int], sample_rate: int, output_file: str) -> str:
    """
    直接传输模式：将原始采样数据导出为 SR 格式

    Args:
        samples: 采样列表，每个元素是一个字节（8个通道状态）
        sample_rate: 采样率 (Hz)
        output_file: 输出文件路径

    Returns:
        导出的文件路径

    注意：
        FPGA端 LOGIC_IN[7:0] 的映射：bit0=CH0, bit1=CH1, ..., bit7=CH7
        PulseView 期望：bit0=CH0（LSB在前），与FPGA一致
        因此不需要进行位序反转！
    """
    # 创建导出器
    exporter = PulseViewExporter(sample_rate=sample_rate, num_channels=8)

    # ✅ 修复：直接使用原始数据，不进行位序反转
    # FPGA采样的bit0对应CH0，bit7对应CH7，与PulseView约定一致
    result = exporter.create_sr_file(samples, filename=output_file)

    # 如果测试后发现通道顺序仍然错误，可能需要反转（但理论上不应该）
    # def reverse_bits(byte_val):
    #     result = 0
    #     for i in range(8):
    #         if byte_val & (1 << i):
    #             result |= (1 << (7 - i))
    #     return result
    # reversed_samples = [reverse_bits(b) for b in samples]
    # result = exporter.create_sr_file(reversed_samples, filename=output_file)

    return result


def parse_and_export(
    raw_data: bytes,
    sample_rate: int = 1_000_000,
    output_file: str = "capture.sr",
    auto_open: bool = True,
) -> Dict:
    """
    便捷函数：一键解析并导出到 PulseView

    Args:
        raw_data: 从串口接收的原始数据
        sample_rate: 采样率（Hz）
        output_file: 输出文件名
        auto_open: 是否自动打开 PulseView

    Returns:
        统计信息字典

    Example:
        >>> import serial
        >>> ser = serial.Serial('COM18', 115200)
        >>> data = ser.read(10000)
        >>> stats = parse_and_export(data, sample_rate=1_000_000)
    """
    pipeline = LogicAnalyzerPipeline(sample_rate)
    return pipeline.process_and_export(raw_data, output_file, auto_open)


if __name__ == "__main__":
    # 测试代码
    import sys

    if len(sys.argv) > 1:
        # 从文件读取数据
        input_file = sys.argv[1]
        print(f"从文件读取数据: {input_file}")

        with open(input_file, "rb") as f:
            raw_data = f.read()

        parse_and_export(raw_data)
    else:
        print("用法: python pulseview_exporter.py <数据文件>")
        print("或在代码中调用 parse_and_export() 函数")
