"""配置加载：config.toml + .env + 环境变量。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Config:
    # 登录态
    token: str = ""

    # 监控
    interval: float = 30.0
    jitter: float = 0.15
    min_idle: int = 1
    gpu: list[str] = field(default_factory=list)
    data_centers: list[str] = field(default_factory=list)
    max_price: float = 0.0
    show_all: bool = False
    fetch_prices: bool = True
    open_browser: bool = False
    alert_on_start: bool = True

    # 通知
    toast: bool = True
    sound: bool = True
    cooldown: int = 600
    webhook: str = ""
    dingtalk: str = ""
    feishu: str = ""
    wecom: str = ""
    bark: str = ""

    # 高级
    timeout: float = 15.0
    price_ttl: int = 6 * 3600
    state_file: str = "state.json"
    log_file: str = "alerts.log"

    def resolve(self, path: str | Path) -> Path:
        """相对路径按当前工作目录解析。"""
        p = Path(path)
        return p if p.is_absolute() else Path.cwd() / p


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"配置文件 {path} 格式错误：{exc}") from exc


def _read_env(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，忽略注释和空行。"""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _clean_token(token: str) -> str:
    token = (token or "").strip().strip('"').strip("'")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def load_config(path: str | Path = "config.toml", env_file: str | Path = ".env") -> Config:
    """读取配置；优先级：环境变量 > .env > config.toml > 默认值。"""
    cfg = Config()
    data = _read_toml(Path(path))

    auth = data.get("auth") or {}
    monitor = data.get("monitor") or {}
    notify = data.get("notify") or {}
    advanced = data.get("advanced") or {}

    cfg.token = str(auth.get("token") or "")
    cfg.interval = float(monitor.get("interval", cfg.interval))
    cfg.jitter = float(monitor.get("jitter", cfg.jitter))
    cfg.min_idle = int(monitor.get("min_idle", cfg.min_idle))
    cfg.gpu = _as_list(monitor.get("gpu"))
    cfg.data_centers = _as_list(monitor.get("data_center"))
    cfg.max_price = float(monitor.get("max_price", cfg.max_price) or 0)
    cfg.show_all = bool(monitor.get("show_all", cfg.show_all))
    cfg.fetch_prices = bool(monitor.get("fetch_prices", cfg.fetch_prices))
    cfg.open_browser = bool(monitor.get("open_browser", cfg.open_browser))
    cfg.alert_on_start = bool(monitor.get("alert_on_start", cfg.alert_on_start))

    cfg.toast = bool(notify.get("toast", cfg.toast))
    cfg.sound = bool(notify.get("sound", cfg.sound))
    cfg.cooldown = int(notify.get("cooldown", cfg.cooldown))
    cfg.webhook = str(notify.get("webhook") or "")
    cfg.dingtalk = str(notify.get("dingtalk") or "")
    cfg.feishu = str(notify.get("feishu") or "")
    cfg.wecom = str(notify.get("wecom") or "")
    cfg.bark = str(notify.get("bark") or "")

    cfg.timeout = float(advanced.get("timeout", cfg.timeout))
    cfg.price_ttl = int(advanced.get("price_ttl", cfg.price_ttl))
    cfg.state_file = str(advanced.get("state_file", cfg.state_file))
    cfg.log_file = str(advanced.get("log_file", cfg.log_file))

    # .env 与进程环境变量覆盖
    env_path = Path(env_file)
    env = _read_env(env_path)
    token = os.environ.get("AUTODL_TOKEN") or env.get("AUTODL_TOKEN") or ""
    if token:
        cfg.token = token
    if os.environ.get("AUTODL_INTERVAL"):
        cfg.interval = float(os.environ["AUTODL_INTERVAL"])

    cfg.token = _clean_token(cfg.token)
    cfg.interval = max(5.0, cfg.interval)
    cfg.jitter = min(max(cfg.jitter, 0.0), 0.8)
    cfg.min_idle = max(1, cfg.min_idle)
    return cfg
