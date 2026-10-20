"""快照、过滤与告警判定的纯逻辑（不依赖网络，方便测试）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class GpuStatus:
    """某个地区（data_center）某个 GPU 型号的实时状态。"""

    data_center: str
    region_name: str
    gpu_name: str
    idle: int
    total: int
    price: float | None = None

    @property
    def key(self) -> str:
        return f"{self.data_center}|{self.gpu_name}"

    @property
    def usage(self) -> float:
        return 0.0 if self.total <= 0 else 1 - self.idle / self.total

    def with_price(self, price: float | None) -> "GpuStatus":
        return GpuStatus(
            data_center=self.data_center,
            region_name=self.region_name,
            gpu_name=self.gpu_name,
            idle=self.idle,
            total=self.total,
            price=price,
        )


@dataclass(frozen=True, slots=True)
class Alert:
    """一次「有卡了 / 空闲变多」事件。"""

    kind: str  # "new" = 从无到有，"more" = 空闲数增加
    status: GpuStatus
    prev_idle: int
    ts: float = field(default_factory=time.time)

    @property
    def delta(self) -> int:
        return self.status.idle - self.prev_idle


@dataclass(slots=True)
class Snapshot:
    items: dict[str, GpuStatus] = field(default_factory=dict)

    def add(self, status: GpuStatus) -> None:
        self.items[status.key] = status

    def get(self, key: str) -> GpuStatus | None:
        return self.items.get(key)

    def __len__(self) -> int:
        return len(self.items)

    def idle_total(self) -> int:
        return sum(s.idle for s in self.items.values())

    def available(self) -> list[GpuStatus]:
        return [s for s in self.items.values() if s.idle > 0]


def matches(
    status: GpuStatus,
    *,
    gpu_names: Iterable[str] = (),
    data_centers: Iterable[str] = (),
    min_idle: int = 1,
    max_price: float = 0.0,
) -> bool:
    """是否命中用户的监控条件。"""
    gpu_names = tuple(gpu_names)
    data_centers = tuple(data_centers)
    if gpu_names and status.gpu_name not in gpu_names:
        return False
    if data_centers and status.data_center not in data_centers:
        return False
    if status.idle < min_idle:
        return False
    if max_price and status.price is not None and status.price > max_price:
        return False
    return True


def diff_snapshot(
    current: Snapshot,
    previous: Snapshot | None,
    *,
    gpu_names: Iterable[str] = (),
    data_centers: Iterable[str] = (),
    min_idle: int = 1,
    max_price: float = 0.0,
    alert_on_start: bool = True,
) -> list[Alert]:
    """对比两次快照，产出需要提醒的事件。

    - 上次 0 张、这次 >= min_idle：kind="new"（最值得提醒）
    - 上次已有、这次更多：kind="more"
    - 首次运行（previous 为 None）：alert_on_start=False 时全部静默。
    """
    alerts: list[Alert] = []
    for key, status in current.items.items():
        if not matches(
            status,
            gpu_names=gpu_names,
            data_centers=data_centers,
            min_idle=min_idle,
            max_price=max_price,
        ):
            continue
        prev = previous.get(key) if previous is not None else None
        prev_idle = prev.idle if prev else 0
        if prev is None and not alert_on_start:
            continue
        if prev_idle < min_idle <= status.idle:
            alerts.append(Alert(kind="new", status=status, prev_idle=prev_idle))
        elif prev is not None and status.idle > prev_idle >= min_idle:
            alerts.append(Alert(kind="more", status=status, prev_idle=prev_idle))
    alerts.sort(key=lambda a: (a.kind != "new", -a.status.idle, a.status.gpu_name))
    return alerts
