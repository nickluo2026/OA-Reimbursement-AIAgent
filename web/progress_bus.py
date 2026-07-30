"""流水线进度总线（内存实现，供 SSE 实时下发）。

用途
────
``/upload`` 是同步阻塞接口（整条流水线约 40s+ 才返回）。为让前端流水线动画与
真实处理节拍一致，处理线程通过 :meth:`ProgressChannel.publish` 逐条推送进度事件，
SSE 线程通过 :meth:`ProgressChannel.subscribe` 阻塞式消费并下发浏览器。

设计要点
────────
- **频道 ID 与 request_id 解耦**：频道 ID 由前端随机生成（``progress_id``），
  仅用于订阅进度；报销单号 ``request_id`` 仍由服务端生成，杜绝前端伪造/覆盖既有单据。
- **归属校验**：频道绑定创建者账号，他人订阅一律拒绝，防止跨用户偷窥进度。
- **有界内存**：单频道事件数、全局频道数均有上限，并按 TTL 定期回收，避免内存膨胀。
- **优雅退化**：即使订阅端断开或超时，发布端 ``publish`` 也永不阻塞、永不抛错。

多实例部署提示：内存实现仅适用于单进程（Flask ``threaded=True`` / 单 worker）。
多 worker/多机部署需将本模块替换为 Redis Pub/Sub 实现（接口保持一致即可）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

# ── 容量与时效约束 ──
MAX_CHANNELS = 256  # 全局最大并发频道数
MAX_EVENTS_PER_CHANNEL = 300  # 单频道最大事件数（超出丢弃新事件，防止刷爆内存）
CHANNEL_TTL_SECONDS = 15 * 60  # 频道最长存活时间
CLOSED_TTL_SECONDS = 60  # 已结束频道的保留时间（供慢速客户端补收尾包）


class ProgressChannel:
    """单次上传对应的进度频道：一个生产者（上传线程）+ 若干消费者（SSE 线程）。"""

    def __init__(self, channel_id: str, owner: str) -> None:
        self.channel_id = channel_id
        self.owner = owner
        self.created_at = time.time()
        self.closed_at: float | None = None
        self._events: list[dict[str, Any]] = []
        self._cond = threading.Condition()

    # ── 生产者侧 ──
    def publish(self, event: dict[str, Any]) -> None:
        """追加一条进度事件并唤醒所有订阅者（永不抛错、永不阻塞）。"""
        with self._cond:
            if self.closed_at is not None or len(self._events) >= MAX_EVENTS_PER_CHANNEL:
                return
            self._events.append(event)
            self._cond.notify_all()

    def close(self) -> None:
        """标记频道结束，唤醒订阅者收尾。"""
        with self._cond:
            if self.closed_at is None:
                self.closed_at = time.time()
            self._cond.notify_all()

    @property
    def is_closed(self) -> bool:
        with self._cond:
            return self.closed_at is not None

    # ── 消费者侧 ──
    def subscribe(self, timeout: float = 300.0, poll: float = 1.0) -> Iterator[dict[str, Any] | None]:
        """阻塞式消费事件流。

        Yields:
            进度事件字典；等待期间每 ``poll`` 秒 yield 一次 ``None`` 作为心跳信号，
            供调用方下发 SSE 注释帧，防止反向代理因空闲切断连接。

        频道关闭且事件全部消费完毕，或超过 ``timeout`` 秒后，迭代结束。
        """
        idx = 0
        deadline = time.time() + timeout
        while True:
            with self._cond:
                while idx >= len(self._events) and self.closed_at is None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return
                    self._cond.wait(timeout=min(poll, remaining))
                    if idx >= len(self._events) and self.closed_at is None:
                        break  # 无新事件 → 出去发心跳
                batch = self._events[idx:]
                idx += len(batch)
                finished = self.closed_at is not None and idx >= len(self._events)
            for ev in batch:
                yield ev
            if finished:
                return
            if not batch:
                yield None  # 心跳
            if time.time() >= deadline:
                return


class ProgressBus:
    """频道注册表：按 ID 创建/查找频道，并按 TTL 回收。"""

    def __init__(self) -> None:
        self._channels: dict[str, ProgressChannel] = {}
        self._lock = threading.Lock()

    def open(self, channel_id: str, owner: str) -> ProgressChannel | None:
        """获取或创建频道。频道已存在但归属不符时返回 ``None``（拒绝访问）。"""
        with self._lock:
            self._gc_locked()
            ch = self._channels.get(channel_id)
            if ch is not None:
                return ch if ch.owner == owner else None
            if len(self._channels) >= MAX_CHANNELS:
                self._evict_oldest_locked()
            ch = ProgressChannel(channel_id, owner)
            self._channels[channel_id] = ch
            return ch

    def get(self, channel_id: str, owner: str) -> ProgressChannel | None:
        """按归属查找已存在的频道，不存在或归属不符返回 ``None``。"""
        with self._lock:
            ch = self._channels.get(channel_id)
            return ch if ch is not None and ch.owner == owner else None

    def discard(self, channel_id: str) -> None:
        with self._lock:
            self._channels.pop(channel_id, None)

    # ── 内部：回收 ──
    def _gc_locked(self) -> None:
        now = time.time()
        stale = [
            cid
            for cid, ch in self._channels.items()
            if now - ch.created_at > CHANNEL_TTL_SECONDS
            or (ch.closed_at is not None and now - ch.closed_at > CLOSED_TTL_SECONDS)
        ]
        for cid in stale:
            self._channels.pop(cid, None)

    def _evict_oldest_locked(self) -> None:
        oldest = min(self._channels.values(), key=lambda c: c.created_at, default=None)
        if oldest is not None:
            self._channels.pop(oldest.channel_id, None)


# 进程级单例
progress_bus = ProgressBus()
