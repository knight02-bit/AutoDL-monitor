"""监控主循环：拉取 → 比对 → 通知 → 渲染。"""

from __future__ import annotations

import logging
import random
import sys
import time
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .api import ApiError, AuthError, AutoDLClient, Region, token_expiry
from .config import Config
from .models import Alert, GpuStatus, Snapshot, diff_snapshot
from .notify import Notifier, set_console_title
from .render import (
    dwidth,
    enable_ansi,
    pad,
    render_dashboard,
    snapshot_to_json,
)
from .state import State, load_state, save_state

log = logging.getLogger("autodl.monitor")

CLEAR = "\x1b[2J\x1b[H"


class Monitor:
    def __init__(self, cfg: Config, client: AutoDLClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or AutoDLClient(cfg.token, timeout=cfg.timeout)
        self.state: State = load_state(cfg.resolve(cfg.state_file))
        self.state_file: Path = cfg.resolve(cfg.state_file)
        self.notifier = Notifier(cfg, cfg.resolve(cfg.log_file))
        self.regions: list[Region] = []
        self.regions_ts = 0.0
        self.user = ""
        self.poll_no = 0
        self.recent: deque[Alert] = deque(maxlen=5)
        self._stop = False

    # ------------------------------------------------------------- 数据获取

    def ensure_regions(self, force: bool = False) -> list[Region]:
        if not force and self.regions and time.time() - self.regions_ts < 1800:
            return self.regions
        regions = self.client.regions()
        if not regions:
            raise ApiError("地区列表为空，接口可能改版了")
        self.regions = regions
        self.regions_ts = time.time()
        return regions

    def _fetch_counts(self) -> Snapshot:
        regions = self.ensure_regions()
        groups: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        for region in regions:
            groups.setdefault(region.data_center, []).append(region.sign)
            names[region.data_center] = region.name

        results: dict[str, dict[str, dict[str, int]]] = {}
        errors: list[tuple[str, Exception]] = []
        with ThreadPoolExecutor(max_workers=min(6, len(groups))) as pool:
            futures = {
                pool.submit(self.client.gpu_counts, signs): data_center
                for data_center, signs in groups.items()
            }
            for future in as_completed(futures):
                data_center = futures[future]
                try:
                    results[data_center] = future.result()
                except Exception as exc:  # noqa: BLE001 - 汇总后统一处理
                    errors.append((data_center, exc))

        auth_errors = [exc for _, exc in errors if isinstance(exc, AuthError)]
        if auth_errors:
            raise auth_errors[0]
        if errors:
            detail = "; ".join(f"{dc}: {exc}" for dc, exc in errors)
            raise ApiError(f"{len(errors)} 个地区拉取失败：{detail}")

        snapshot = Snapshot()
        for data_center, counts in results.items():
            for gpu_name, info in counts.items():
                snapshot.add(
                    GpuStatus(
                        data_center=data_center,
                        region_name=names[data_center],
                        gpu_name=gpu_name,
                        idle=int(info.get("idle", 0)),
                        total=int(info.get("total", 0)),
                    )
                )
        return snapshot

    def _price_targets(self, snapshot: Snapshot) -> list[GpuStatus]:
        if not self.cfg.fetch_prices:
            return []
        if self.cfg.gpu:
            models = set(self.cfg.gpu)
            targets = [s for s in snapshot.items.values() if s.gpu_name in models]
        elif self.cfg.show_all:
            targets = list(snapshot.items.values())
        else:
            # 没指定型号时只给「当前有卡」的型号取价，避免一次打上百个请求
            targets = [s for s in snapshot.items.values() if s.idle > 0]
        if self.cfg.data_centers:
            centers = set(self.cfg.data_centers)
            targets = [s for s in targets if s.data_center in centers]
        return targets

    def _apply_prices(self, snapshot: Snapshot) -> None:
        now = time.time()
        targets = self._price_targets(snapshot)
        missing = []
        for status in targets:
            cached = self.state.prices.get(status.key)
            if cached and now - float(cached.get("ts", 0)) < self.cfg.price_ttl:
                continue
            missing.append(status)

        if missing:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(self.client.min_price, s.gpu_name, s.data_center): s
                    for s in missing[:24]
                }
                for future in as_completed(futures):
                    status = futures[future]
                    try:
                        price = future.result()
                    except Exception as exc:  # noqa: BLE001 - 价格是附加信息
                        log.debug("取价失败 %s %s: %s", status.data_center, status.gpu_name, exc)
                        continue
                    if price is not None:
                        self.state.prices[status.key] = {"price": price, "ts": now}

        for key, cached in self.state.prices.items():
            status = snapshot.items.get(key)
            if status is not None:
                snapshot.items[key] = status.with_price(float(cached["price"]))

    def fetch_snapshot(self) -> Snapshot:
        snapshot = self._fetch_counts()
        self._apply_prices(snapshot)
        return snapshot

    # ------------------------------------------------------------- 比对告警

    def _previous(self) -> Snapshot | None:
        """上次快照；停跑超过 15 分钟就作废，避免用隔夜数据误判「新上架」。"""
        if not self.state.snapshot.items:
            return None
        if time.time() - self.state.updated > 900:
            return None
        return self.state.snapshot

    def process(self, snapshot: Snapshot, *, notify: bool = True) -> list[Alert]:
        self.poll_no += 1
        alerts = diff_snapshot(
            snapshot,
            self._previous(),
            gpu_names=self.cfg.gpu,
            data_centers=self.cfg.data_centers,
            min_idle=self.cfg.min_idle,
            max_price=self.cfg.max_price,
            alert_on_start=self.cfg.alert_on_start,
        )

        now = time.time()
        fresh: list[Alert] = []
        for alert in alerts:
            key = alert.status.key
            last = self.state.last_alert.get(key, 0.0)
            if now - last < self.cfg.cooldown:
                continue
            self.state.last_alert[key] = now
            fresh.append(alert)

        # 卡被占满后清掉冷却，下次再来卡能立刻提醒
        for key, status in snapshot.items.items():
            if status.idle < self.cfg.min_idle:
                self.state.last_alert.pop(key, None)

        self.recent.extend(fresh)
        # 只保留最近半小时的告警，避免看板一直挂着隔夜记录
        self.recent = deque((a for a in self.recent if now - a.ts < 1800), maxlen=5)
        if fresh and notify:
            self.notifier.notify(fresh, len(snapshot.available()))
            if self.cfg.open_browser:
                try:
                    webbrowser.open("https://www.autodl.com/create")
                except Exception:  # noqa: BLE001 - 打不开浏览器不是致命错误
                    log.debug("打开浏览器失败")

        self.state.snapshot = snapshot
        self.state.updated = now
        try:
            save_state(self.state_file, self.state)
        except OSError as exc:
            log.warning("保存状态失败：%s", exc)
        return fresh

    # ------------------------------------------------------------- 展示/循环

    def _region_count(self) -> int:
        if self.cfg.data_centers:
            return len(self.cfg.data_centers)
        return len({r.data_center for r in self.regions})

    def _draw(self, snapshot: Snapshot, next_in: float) -> None:
        expiry = token_expiry(self.cfg.token)
        days = None
        if expiry is not None:
            days = max(0, (expiry - datetime.now().astimezone()).days)
        text = render_dashboard(
            snapshot,
            self.cfg,
            user=self.user or "未验证",
            token_days=days,
            poll_no=self.poll_no,
            next_in=next_in,
            regions=self._region_count(),
            recent=self.recent,
        )
        if sys.stdout.isatty():
            sys.stdout.write(CLEAR)
        print(text, flush=True)

    def run_forever(self) -> int:
        enable_ansi()
        try:
            info = self.client.user()
            self.user = info.username or f"uid {info.uid}"
        except AuthError as exc:
            self._auth_help(exc)
            return 2
        except Exception as exc:  # noqa: BLE001
            log.warning("账号信息获取失败：%s", exc)

        print(f"AutoDL 监控已启动（{self.user}），Ctrl+C 停止\n", flush=True)
        failures = 0
        while not self._stop:
            started = time.time()
            try:
                snapshot = self.fetch_snapshot()
            except AuthError as exc:
                self._auth_help(exc)
                return 2
            except Exception as exc:  # noqa: BLE001 - 网络抖动继续重试
                failures += 1
                log.error("第 %d 次拉取失败：%s", failures, exc)
                if failures >= 10:
                    log.error("连续失败 10 次，退出。请检查网络或 token。")
                    return 3
                time.sleep(min(30 * failures, 180))
                continue

            failures = 0
            self.process(snapshot)
            sleep_for = self.cfg.interval * (1 + random.uniform(-self.cfg.jitter, self.cfg.jitter))
            sleep_for = max(5.0, sleep_for)
            self._draw(snapshot, sleep_for)
            set_console_title(
                f"AutoDL 监控 | 有卡 {len(snapshot.available())} 处"
                if snapshot.available()
                else "AutoDL 监控 | 暂无空闲"
            )
            elapsed = time.time() - started
            time.sleep(max(1.0, sleep_for - elapsed))
        return 0

    def run_once(self, as_json: bool = False) -> int:
        snapshot = self.fetch_snapshot()
        if as_json:
            selected = Snapshot()
            for key, status in snapshot.items.items():
                if self.cfg.gpu and status.gpu_name not in self.cfg.gpu:
                    continue
                if self.cfg.data_centers and status.data_center not in self.cfg.data_centers:
                    continue
                selected.items[key] = status
            print(snapshot_to_json(selected))
            return 0
        try:
            info = self.client.user()
            self.user = info.username or f"uid {info.uid}"
        except Exception:  # noqa: BLE001 - 只是展示用
            pass
        self.poll_no = 1
        expiry = token_expiry(self.cfg.token)
        days = None if expiry is None else max(0, (expiry - datetime.now().astimezone()).days)
        # 单次模式不写入状态、不弹通知，作为一次性查询用
        print(render_dashboard(
            snapshot,
            self.cfg,
            user=self.user or "已连接",
            token_days=days,
            poll_no=1,
            next_in=0,
            regions=self._region_count(),
        ))
        return 0

    def check(self) -> int:
        info = self.client.user()
        expiry = token_expiry(self.cfg.token)
        regions = self.ensure_regions()
        counts = self.client.gpu_counts([r.sign for r in regions])
        idle_models = [(name, item) for name, item in counts.items() if item["idle"] > 0]
        print("登录状态：有效")
        print(f"账号：{info.username}（uid {info.uid}）")
        if expiry:
            days = (expiry - datetime.now().astimezone()).days
            print(f"Token 过期时间：{expiry:%Y-%m-%d %H:%M}（剩余 {days} 天）")
        print(f"地区：{len({r.data_center for r in regions})} 个数据中心 / {len(regions)} 个分区")
        print(f"GPU 型号：{len(counts)} 个，当前有卡型号：{len(idle_models)} 个")
        for name, item in sorted(idle_models, key=lambda x: -x[1]["idle"]):
            print(f"  {name}: {item['idle']}/{item['total']} 空闲")
        if not idle_models:
            print("  （所有型号均为 0 空闲）")
        return 0

    def models(self) -> int:
        """列出所有 GPU 型号及当前空闲数，方便确定 --gpu 该填什么。"""
        regions = self.ensure_regions()
        counts = self.client.gpu_counts([r.sign for r in regions])
        rows = sorted(counts.items(), key=lambda kv: (-kv[1]["idle"], -kv[1]["total"], kv[0]))
        width = max((dwidth(name) for name in counts), default=10) + 4
        print(pad("型号", width) + " " + pad("空闲", 6, "right") + " " + pad("总数", 6, "right"))
        print("─" * (width + 14))
        for name, info in rows:
            print(pad(name, width) + " " + pad(str(info["idle"]), 6, "right") + " " + pad(str(info["total"]), 6, "right"))
        print(f"\n共 {len(rows)} 个型号；用 --gpu \"型号1,型号2\" 精确匹配（区分大小写和空格）。")
        return 0

    def demo(self) -> int:
        """离线演示：构造一个有卡的快照，验证表格、气泡和提示音。"""
        snapshot = Snapshot()
        snapshot.add(GpuStatus("westDC3", "西北B区", "RTX 4090", 2, 1776, 1.98))
        snapshot.add(GpuStatus("beijingDC2", "北京B区", "RTX 4090", 0, 654, 2.09))
        snapshot.add(GpuStatus("westDC3", "西北B区", "RTX 5090", 1, 2048, 2.58))
        alerts = [
            Alert("new", snapshot.items["westDC3|RTX 4090"], 0),
            Alert("new", snapshot.items["westDC3|RTX 5090"], 0),
        ]
        self.poll_no = 1
        self.user = "demo"
        self.notifier.notify(alerts, 2)
        print(render_dashboard(
            snapshot,
            self.cfg,
            user="demo",
            token_days=30,
            poll_no=1,
            next_in=self.cfg.interval,
            regions=9,
            recent=alerts,
        ))
        return 0

    def _auth_help(self, exc: Exception) -> None:
        print("\n登录态失效：%s" % exc, file=sys.stderr)
        print(
            "请重新获取 token：登录 www.autodl.com → F12 → Network → 任意 api/v1 请求 →\n"
            "右键 Copy as cURL，把请求头里 authorization 的值（eyJ 开头）填到 .env 的 AUTODL_TOKEN。\n"
            "详细步骤见 README.md「获取 token」。",
            file=sys.stderr,
        )

    def stop(self) -> None:
        self._stop = True
