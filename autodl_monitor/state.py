"""运行状态持久化：上次快照、告警冷却时间、价格缓存。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import GpuStatus, Snapshot


@dataclass
class State:
    snapshot: Snapshot = field(default_factory=Snapshot)
    last_alert: dict[str, float] = field(default_factory=dict)
    prices: dict[str, dict] = field(default_factory=dict)  # key -> {"price": float, "ts": float}
    updated: float = 0.0


def load_state(path: Path) -> State:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return State()

    snap = Snapshot()
    for key, item in (raw.get("snapshot") or {}).items():
        try:
            snap.items[key] = GpuStatus(
                data_center=str(item["data_center"]),
                region_name=str(item.get("region_name") or item["data_center"]),
                gpu_name=str(item["gpu_name"]),
                idle=int(item["idle"]),
                total=int(item["total"]),
                price=item.get("price"),
            )
        except (KeyError, TypeError, ValueError):
            continue

    last_alert = {}
    for key, ts in (raw.get("last_alert") or {}).items():
        if isinstance(ts, (int, float)):
            last_alert[key] = float(ts)

    prices = {}
    for key, item in (raw.get("prices") or {}).items():
        if isinstance(item, dict) and isinstance(item.get("price"), (int, float)):
            prices[key] = {"price": float(item["price"]), "ts": float(item.get("ts") or 0)}

    return State(
        snapshot=snap,
        last_alert=last_alert,
        prices=prices,
        updated=float(raw.get("updated") or 0),
    )


def save_state(path: Path, state: State) -> None:
    payload = {
        "version": 1,
        "updated": time.time(),
        "snapshot": {
            key: {
                "data_center": item.data_center,
                "region_name": item.region_name,
                "gpu_name": item.gpu_name,
                "idle": item.idle,
                "total": item.total,
                "price": item.price,
            }
            for key, item in state.snapshot.items.items()
        },
        "last_alert": state.last_alert,
        "prices": state.prices,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
