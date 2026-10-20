"""终端渲染：等宽对齐（中文按 2 列）、配色、看板与告警行。"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from datetime import datetime
from typing import Iterable, Sequence

from .config import Config
from .models import Alert, GpuStatus, Snapshot

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
BRIGHT_GREEN = "\x1b[92m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def enable_ansi() -> None:
    """让 Windows 控制台支持 ANSI 转义（失败的终端会自动忽略颜色）。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001 - 装饰性功能，失败不影响监控
        pass


def paint(text: str, *styles: str) -> str:
    if not COLOR or not styles:
        return text
    return "".join(styles) + text + RESET


def dwidth(text: str) -> int:
    """字符串在等宽终端里占的列数（CJK 全角算 2 列）。"""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int, align: str = "left") -> str:
    space = max(0, width - dwidth(text))
    if align == "right":
        return " " * space + text
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    return text + " " * space


def format_price(price: float | None) -> str:
    return "-" if price is None else f"¥{price:.2f}/h"


def format_clock(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or datetime.now().timestamp()).strftime("%H:%M:%S")


def select_rows(snapshot: Snapshot, cfg: Config) -> list[GpuStatus]:
    """按配置决定表格里展示哪些行。"""
    rows = list(snapshot.items.values())
    if cfg.gpu:
        watch = set(cfg.gpu)
        rows = [row for row in rows if row.gpu_name in watch]
        if cfg.data_centers:
            centers = set(cfg.data_centers)
            rows = [row for row in rows if row.data_center in centers]
    elif cfg.data_centers:
        centers = set(cfg.data_centers)
        rows = [row for row in rows if row.data_center in centers]
        if not cfg.show_all:
            rows = [row for row in rows if row.idle > 0]
    elif not cfg.show_all:
        rows = [row for row in rows if row.idle > 0]
    rows.sort(key=lambda r: (0 if r.idle > 0 else 1, r.gpu_name, -r.idle, r.price or 99.0))
    return rows


def _table(rows: Sequence[GpuStatus]) -> list[str]:
    headers = ("地区", "型号", "空闲/总数", "最低价", "状态")
    aligns = ("left", "left", "right", "right", "left")
    body: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        body.append(
            (
                row.region_name,
                row.gpu_name,
                f"{row.idle}/{row.total}",
                format_price(row.price),
                "有卡" if row.idle > 0 else "—",
            )
        )

    widths = [max(dwidth(headers[i]), *(dwidth(line[i]) for line in body)) if body else dwidth(headers[i]) for i in range(5)]
    widths = [max(w, 4) for w in widths]

    def render_cells(cells: Sequence[str], cell_aligns: Sequence[str]) -> str:
        return "  ".join(pad(cell, widths[i], cell_aligns[i]) for i, cell in enumerate(cells)).rstrip()

    lines = [paint(render_cells(headers, aligns), BOLD), paint("─" * (sum(widths) + 8), DIM)]
    for row, cells in zip(rows, body):
        text = render_cells(cells, aligns)
        if row.idle > 0:
            lines.append(paint(text, BRIGHT_GREEN))
        else:
            lines.append(paint(text, DIM))
    return lines


def _summary(rows: Sequence[GpuStatus], snapshot: Snapshot, cfg: Config) -> str:
    available = [r for r in rows if r.idle > 0]
    if available:
        models = sorted({r.gpu_name for r in available})
        return f"共 {len(available)} 个地区/型号有卡（{', '.join(models[:3])}{'…' if len(models) > 3 else ''}），空闲合计 {sum(r.idle for r in available)} 张"
    if rows:
        return "筛选的型号当前都没有空闲卡"
    if snapshot.available():
        return "筛选范围内暂时没有空闲卡（其他地区/型号有卡，加 --all 查看全部）"
    return "当前所有地区均无空闲卡"


def render_dashboard(
    snapshot: Snapshot,
    cfg: Config,
    *,
    user: str,
    token_days: int | None,
    poll_no: int,
    next_in: float,
    regions: int,
    recent: Sequence[Alert] = (),
) -> str:
    rows = select_rows(snapshot, cfg)
    watch_desc = ", ".join(cfg.gpu) if cfg.gpu else "全部型号"
    token_desc = "有效期未知" if token_days is None else (
        f"Token 剩余 {token_days} 天" if token_days >= 1 else paint("Token 即将过期", RED)
    )

    lines = [
        paint(" AutoDL 空闲 GPU 监控", BOLD) + "   " + paint(format_clock(), CYAN)
        + paint(f"   第 {poll_no} 次轮询 · {next_in:.0f}s 后刷新", DIM),
        f" 账号 {user} · {token_desc} · 监控 {watch_desc} · {regions} 个地区",
        "",
    ]
    lines.extend(_table(rows))
    lines.append("")

    if recent:
        lines.append(paint(" 最近告警", BOLD))
        for alert in list(recent)[-3:]:
            lines.append(" " + format_alert(alert))
        lines.append("")

    lines.append(paint(f" {_summary(rows, snapshot, cfg)}", GREEN if any(r.idle > 0 for r in rows) else DIM))
    lines.append(
        paint(
            " Ctrl+C 退出 · 日志 alerts.log · 打开创建页：https://www.autodl.com/create",
            DIM,
        )
    )
    return "\n".join(lines)


def format_alert(alert: Alert) -> str:
    st = alert.status
    label = "新上架" if alert.kind == "new" else f"增加 {alert.delta}"
    head = paint(f"[{format_clock(alert.ts)}]", DIM)
    tag = paint("● 有卡", BRIGHT_GREEN)
    price = f"  {format_price(st.price)}" if st.price is not None else ""
    return (
        f"{head} {tag} {paint(label, BOLD)}  {st.gpu_name}  {st.region_name}"
        f"  空闲 {st.idle}/{st.total}{price}"
    )


def render_alerts_plain(alerts: Iterable[Alert]) -> str:
    return "\n".join(format_alert(a) for a in alerts)


def snapshot_to_json(snapshot: Snapshot) -> str:
    payload = [
        {
            "data_center": s.data_center,
            "region_name": s.region_name,
            "gpu_name": s.gpu_name,
            "idle": s.idle,
            "total": s.total,
            "price": s.price,
            "usage": round(s.usage, 4),
        }
        for s in sorted(snapshot.items.values(), key=lambda x: (x.data_center, x.gpu_name))
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)
