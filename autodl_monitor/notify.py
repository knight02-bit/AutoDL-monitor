"""通知：Windows 气泡、提示音、Webhook、控制台标题与日志。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Sequence

import requests

from .config import Config
from .models import Alert
from .render import format_alert

log = logging.getLogger("autodl.notify")

CREATE_URL = "https://www.autodl.com/create"

_TOAST_SCRIPT = r"""
param([string]$JsonPath)
$ErrorActionPreference = "Stop"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$data = Get-Content -LiteralPath $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName("text")
$texts.Item(0).AppendChild($template.CreateTextNode($data.title)) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode($data.body)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""

_script_lock = threading.Lock()
_script_path: Path | None = None


def _toast_script() -> Path:
    global _script_path
    with _script_lock:
        if _script_path and _script_path.exists():
            return _script_path
        base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "autodl_monitor"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "toast.ps1"
        path.write_text(_TOAST_SCRIPT, encoding="utf-8-sig")
        _script_path = path
        return path


def windows_toast(title: str, body: str) -> bool:
    """用 PowerShell 的 WinRT 接口弹系统通知，失败静默返回 False。"""
    if os.name != "nt":
        return False
    try:
        script = _toast_script()
        payload = Path(os.environ.get("TEMP") or ".") / "autodl_monitor" / "toast.json"
        payload.write_text(json.dumps({"title": title, "body": body}, ensure_ascii=False), encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                str(payload),
            ],
            capture_output=True,
            timeout=20,
            creationflags=flags,
        )
        if proc.returncode != 0:
            log.debug("toast 失败：%s", proc.stderr.decode("utf-8", "ignore")[:300])
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - 通知失败不应影响监控
        log.debug("toast 异常：%s", exc)
        return False


def beep() -> None:
    """响一声；非 Windows 或没有声音设备时退回终端响铃。"""
    if os.name == "nt":
        try:
            import winsound

            winsound.MessageBeep(0x00000030)  # MB_ICONEXCLAMATION
            return
        except Exception:  # noqa: BLE001
            pass
    sys.stdout.write("\a")
    sys.stdout.flush()


def set_console_title(text: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except Exception:  # noqa: BLE001
        pass


def _webhook_payload(kind: str, title: str, body: str) -> dict:
    text = f"{title}\n{body}\n{CREATE_URL}"
    if kind == "dingtalk":
        return {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"### {title}\n\n{body}\n\n[打开 AutoDL 创建页]({CREATE_URL})"},
        }
    if kind == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if kind == "wecom":
        return {"msgtype": "text", "text": {"content": text}}
    return {"text": text}


class Notifier:
    """把一次轮询里的告警汇总成一条通知发出去。"""

    def __init__(self, cfg: Config, log_file: Path) -> None:
        self.cfg = cfg
        self.log_file = log_file
        self._lock = threading.Lock()

    def notify(self, alerts: Sequence[Alert], available: int) -> None:
        if not alerts:
            return
        title, body = self._compose(alerts)
        self._write_log(alerts)
        self._update_title(available)

        if self.cfg.sound:
            threading.Thread(target=beep, daemon=True).start()
        if self.cfg.toast:
            threading.Thread(target=windows_toast, args=(title, body), daemon=True).start()
        self._send_webhooks(title, body)

    @staticmethod
    def _compose(alerts: Sequence[Alert]) -> tuple[str, str]:
        gpu_names = []
        for alert in alerts:
            if alert.status.gpu_name not in gpu_names:
                gpu_names.append(alert.status.gpu_name)
        if len(gpu_names) <= 2:
            title = f"AutoDL 有卡了 · {' / '.join(gpu_names)}"
        else:
            title = f"AutoDL 有卡了 · {len(alerts)} 处"

        lines = []
        for alert in alerts[:4]:
            st = alert.status
            price = f" {st.price:.2f}元/h" if st.price is not None else ""
            lines.append(f"{st.gpu_name} @ {st.region_name}：{st.idle}/{st.total} 张空闲{price}")
        if len(alerts) > 4:
            lines.append(f"…等 {len(alerts)} 处")
        return title, "\n".join(lines)

    def _write_log(self, alerts: Sequence[Alert]) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = []
        for alert in alerts:
            st = alert.status
            price = f" {st.price:.2f}元/h" if st.price is not None else ""
            kind = "有卡" if alert.kind == "new" else "增加"
            lines.append(
                f"{stamp} [{kind}] {st.gpu_name} @ {st.region_name} ({st.data_center}) "
                f"{st.idle}/{st.total}{price}"
            )
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.log_file.exists() or self.log_file.stat().st_size == 0
            with self.log_file.open("a", encoding="utf-8") as fh:
                if is_new:  # 带 BOM，记事本 / Excel 打开不乱码
                    fh.write("\ufeff")
                fh.write("\n".join(lines) + "\n")
        except OSError as exc:
            log.warning("写告警日志失败：%s", exc)

    def _update_title(self, available: int) -> None:
        set_console_title(f"AutoDL 监控 | 有卡 {available} 处" if available else "AutoDL 监控 | 暂无空闲")

    def _send_webhooks(self, title: str, body: str) -> None:
        targets: list[tuple[str, str]] = []
        if self.cfg.webhook:
            targets.append(("generic", self.cfg.webhook))
        if self.cfg.dingtalk:
            targets.append(("dingtalk", self.cfg.dingtalk))
        if self.cfg.feishu:
            targets.append(("feishu", self.cfg.feishu))
        if self.cfg.wecom:
            targets.append(("wecom", self.cfg.wecom))
        if self.cfg.bark:
            targets.append(("bark", self.cfg.bark))
        if not targets:
            return

        def worker(name: str, url: str) -> None:
            try:
                if name == "bark":
                    payload = {"title": title, "body": body, "url": CREATE_URL, "group": "AutoDL"}
                else:
                    payload = _webhook_payload(name, title, body)
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code >= 300:
                    log.warning("%s webhook 返回 HTTP %s", name, resp.status_code)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s webhook 失败：%s", name, exc)

        for name, url in targets:
            threading.Thread(target=worker, args=(name, url), daemon=True).start()


__all__ = ["Notifier", "windows_toast", "beep", "set_console_title", "format_alert"]
