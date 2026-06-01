#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高性能环形缓冲区（Ring Buffer）
用于示波器波形数据缓冲，支持乒乓机制和线程安全

特性：
- 固定大小的numpy数组，避免内存重新分配
- 环形写入，自动覆盖旧数据
- 线程安全的读写操作
- 支持快照读取（用于显示）
- 零拷贝获取连续数据

作者：GitHub Copilot
日期：2025-11-21
"""

import numpy as np
import threading
from typing import List, Optional, Tuple


class RingBuffer:
    """
    高性能环形缓冲区

    使用固定大小的numpy数组实现，避免频繁的内存分配
    """

    def __init__(self, capacity: int = 100000, dtype=np.float32):
        """
        初始化环形缓冲区

        Args:
            capacity: 缓冲区容量（样本点数）
            dtype: 数据类型（默认float32节省内存）
        """
        self.capacity = capacity
        self.dtype = dtype

        # 环形缓冲区主存储
        self._buffer = np.zeros(capacity, dtype=dtype)

        # 写入位置（环形）
        self._write_pos = 0

        # 当前有效数据量
        self._data_count = 0

        # 线程锁（保护写入操作）
        self._lock = threading.Lock()

        # 是否已满（区分空和满）
        self._is_full = False

    def append(self, data: List[float]) -> int:
        """
        追加数据到缓冲区（线程安全）

        Args:
            data: 要追加的数据列表

        Returns:
            int: 实际写入的数据量
        """
        if not data:
            return 0

        data_len = len(data)

        with self._lock:
            # 计算写入位置和长度
            if data_len >= self.capacity:
                # 数据量超过缓冲区大小，只保留最新的capacity个数据
                data = data[-self.capacity :]
                self._buffer[:] = np.array(data, dtype=self.dtype)
                self._write_pos = 0
                self._data_count = self.capacity
                self._is_full = True
                return self.capacity

            # 分段写入（处理环形边界）
            end_pos = self._write_pos + data_len

            if end_pos <= self.capacity:
                # 一次性写入
                self._buffer[self._write_pos : end_pos] = np.array(
                    data, dtype=self.dtype
                )
            else:
                # 分两段写入（跨越边界）
                first_part_len = self.capacity - self._write_pos
                second_part_len = data_len - first_part_len

                self._buffer[self._write_pos :] = np.array(
                    data[:first_part_len], dtype=self.dtype
                )
                self._buffer[:second_part_len] = np.array(
                    data[first_part_len:], dtype=self.dtype
                )

            # 更新写入位置
            self._write_pos = end_pos % self.capacity

            # 更新数据计数
            if not self._is_full:
                self._data_count = min(self._data_count + data_len, self.capacity)
                if self._data_count >= self.capacity:
                    self._is_full = True

        return data_len

    def get_latest(self, n: Optional[int] = None) -> np.ndarray:
        """
        获取最新的N个数据（快照，用于显示）

        Args:
            n: 获取的数据量（None=全部）

        Returns:
            np.ndarray: 最新的数据数组（副本）
        """
        with self._lock:
            if self._data_count == 0:
                return np.array([], dtype=self.dtype)

            if n is None or n >= self._data_count:
                # 获取全部有效数据
                n = self._data_count

            if not self._is_full:
                # 🔥 V8.6.30修复：缓冲区未满时，应返回最新的n个数据！
                # 错误的旧代码：return self._buffer[:n].copy()  # 返回最旧的n个
                # 正确的新代码：从write_pos倒推n个
                start_pos = max(0, self._write_pos - n)
                return self._buffer[start_pos : self._write_pos].copy()
            else:
                # 缓冲区已满，需要处理环形
                start_pos = (self._write_pos - n) % self.capacity
                end_pos = self._write_pos

                if start_pos < end_pos:
                    # 数据连续
                    return self._buffer[start_pos:end_pos].copy()
                else:
                    # 数据跨越边界
                    return np.concatenate(
                        [self._buffer[start_pos:], self._buffer[:end_pos]]
                    )

    def get_all(self) -> np.ndarray:
        """
        获取所有有效数据（快照）

        Returns:
            np.ndarray: 所有有效数据（副本）
        """
        with self._lock:
            if self._data_count == 0:
                return np.array([], dtype=self.dtype)

            if not self._is_full:
                # 🔥 V8.6.30修复：未满时返回有效数据部分
                return self._buffer[: self._write_pos].copy()
            else:
                # 已满，返回完整环形数据
                # write_pos指向下一个写入位置，所以从write_pos开始是最旧的数据
                return np.concatenate(
                    [self._buffer[self._write_pos :], self._buffer[: self._write_pos]]
                )

    def clear(self):
        """清空缓冲区"""
        with self._lock:
            self._write_pos = 0
            self._data_count = 0
            self._is_full = False
            # 不需要清零数组，只需重置指针

    def __len__(self) -> int:
        """返回当前有效数据量"""
        return self._data_count

    def __bool__(self) -> bool:
        """支持bool检查: if buffer: ..."""
        return self._data_count > 0

    @property
    def is_full(self) -> bool:
        """缓冲区是否已满"""
        return self._is_full


class PingPongBuffer:
    """
    乒乓缓冲区（Double Buffer）

    使用两个缓冲区交替工作，一个写入，一个读取
    适合高速数据流场景
    """

    def __init__(self, capacity: int = 50000, dtype=np.float32):
        """
        初始化乒乓缓冲区

        Args:
            capacity: 每个缓冲区的容量
            dtype: 数据类型
        """
        self.capacity = capacity
        self.dtype = dtype

        # 两个缓冲区
        self._buffer_a = np.zeros(capacity, dtype=dtype)
        self._buffer_b = np.zeros(capacity, dtype=dtype)

        # 当前写入缓冲区（0=A, 1=B）
        self._active_buffer = 0

        # 写入位置
        self._write_pos = 0

        # 数据量
        self._data_count_a = 0
        self._data_count_b = 0

        # 锁
        self._lock = threading.Lock()

        # 交换标志
        self._swap_requested = False

    def append(self, data: List[float]) -> int:
        """
        追加数据到当前活动缓冲区

        Args:
            data: 要追加的数据

        Returns:
            int: 实际写入的数据量
        """
        if not data:
            return 0

        data_len = len(data)

        with self._lock:
            # 获取当前活动缓冲区
            current_buf = self._buffer_a if self._active_buffer == 0 else self._buffer_b

            # 检查是否需要交换缓冲区
            if self._write_pos + data_len > self.capacity:
                # 缓冲区将满，请求交换
                self._swap_requested = True
                remaining = self.capacity - self._write_pos

                if remaining > 0:
                    # 填满当前缓冲区
                    current_buf[self._write_pos :] = np.array(
                        data[:remaining], dtype=self.dtype
                    )

                # 交换缓冲区
                self._active_buffer = 1 - self._active_buffer
                self._write_pos = 0

                # 更新数据计数
                if self._active_buffer == 0:
                    self._data_count_a = self.capacity
                else:
                    self._data_count_b = self.capacity

                # 写入剩余数据到新缓冲区
                if data_len > remaining:
                    overflow_data = data[remaining:]
                    return remaining + self.append(overflow_data)
                else:
                    return remaining
            else:
                # 正常写入
                end_pos = self._write_pos + data_len
                current_buf[self._write_pos : end_pos] = np.array(
                    data, dtype=self.dtype
                )
                self._write_pos = end_pos

                # 更新数据计数
                if self._active_buffer == 0:
                    self._data_count_a = end_pos
                else:
                    self._data_count_b = end_pos

                return data_len

    def get_display_buffer(self) -> Tuple[np.ndarray, int]:
        """
        获取用于显示的缓冲区（返回非活动缓冲区的快照）

        Returns:
            Tuple[np.ndarray, int]: (数据数组, 有效数据量)
        """
        with self._lock:
            # 返回非活动缓冲区
            if self._active_buffer == 0:
                # A在写，返回B
                return self._buffer_b[: self._data_count_b].copy(), self._data_count_b
            else:
                # B在写，返回A
                return self._buffer_a[: self._data_count_a].copy(), self._data_count_a

    def swap_if_needed(self) -> bool:
        """
        如果需要，交换缓冲区

        Returns:
            bool: 是否进行了交换
        """
        if self._swap_requested:
            self._swap_requested = False
            return True
        return False

    def clear(self):
        """清空所有缓冲区"""
        with self._lock:
            self._write_pos = 0
            self._data_count_a = 0
            self._data_count_b = 0
            self._active_buffer = 0
            self._swap_requested = False


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    import time

    print("=== 测试环形缓冲区 ===")

    # 测试1: 基本功能
    rb = RingBuffer(capacity=10)
    rb.append([1, 2, 3, 4, 5])
    print(f"追加5个数据，当前长度: {len(rb)}")
    print(f"数据: {rb.get_all()}")

    rb.append([6, 7, 8, 9, 10, 11, 12])
    print(f"追加7个数据（总12个），当前长度: {len(rb)}")
    print(f"数据: {rb.get_all()}")
    print(f"最新5个: {rb.get_latest(5)}")

    # 测试2: 性能测试
    print("\n=== 性能测试 ===")
    large_rb = RingBuffer(capacity=100000)

    start = time.time()
    for _ in range(1000):
        large_rb.append(list(range(100)))
    elapsed = time.time() - start

    print(f"写入100,000个数据点，耗时: {elapsed*1000:.2f}ms")
    print(f"吞吐量: {100000/elapsed/1e6:.2f} MSPS")

    # 测试3: 乒乓缓冲区
    print("\n=== 测试乒乓缓冲区 ===")
    pb = PingPongBuffer(capacity=5)

    pb.append([1, 2, 3])
    data, count = pb.get_display_buffer()
    print(f"写入3个数据，显示缓冲区: {data[:count]}")

    pb.append([4, 5, 6, 7])  # 触发交换
    data, count = pb.get_display_buffer()
    print(f"写入4个数据（交换），显示缓冲区: {data[:count]}")

    print("\n✅ 测试完成")
