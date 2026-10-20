#!/usr/bin/env python3
"""AutoDL 空闲 GPU 监控入口。

用法示例：
    python monitor.py                     # 按 config.toml 持续监控
    python monitor.py --once              # 只看一次
    python monitor.py --json              # 输出 JSON（便于脚本消费）
    python monitor.py --gpu "RTX 4090" --min-idle 2 --interval 20
    python monitor.py --check             # 验证 token、看账号和当前库存
    python monitor.py --demo              # 离线演示通知效果
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

if sys.version_info < (3, 11):
    sys.exit("需要 Python 3.11+（用到内置 tomllib），当前是 %s" % sys.version.split()[0])


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文和表格符号会乱码，统一切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001 - 非控制台环境忽略
            pass


_force_utf8()

from autodl_monitor import __version__
from autodl_monitor.api import AuthError, token_expiry
from autodl_monitor.config import Config, load_config
from autodl_monitor.runner import Monitor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor.py",
        description="实时监控 AutoDL 空闲 GPU，有卡时弹窗/响铃/推送。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python monitor.py --gpu 'RTX 4090,RTX 5090' --interval 20\n"
            "  python monitor.py --once --all\n"
            "  python monitor.py --check\n"
        ),
    )
    parser.add_argument("--config", default="config.toml", help="配置文件路径（默认 config.toml）")
    parser.add_argument("--env", default=".env", help="存放 AUTODL_TOKEN 的文件（默认 .env）")
    parser.add_argument("--token", help="直接指定网页登录 token（覆盖配置文件）")
    parser.add_argument("--interval", type=float, help="轮询间隔秒数（默认 30，最小 5）")
    parser.add_argument("--gpu", help="只监控这些型号，逗号分隔，如 'RTX 4090,RTX 5090'")
    parser.add_argument("--dc", help="只监控这些地区（data_center），逗号分隔，如 westDC3,beijingDC2")
    parser.add_argument("--min-idle", type=int, help="至少多少张空闲才提醒（默认 1）")
    parser.add_argument("--max-price", type=float, help="可接受的最高单价（元/小时），0 表示不限")
    parser.add_argument("--all", action="store_true", help="展示全部型号（含 0 空闲）")
    parser.add_argument("--once", action="store_true", help="只拉取并展示一次，然后退出")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（隐含 --once）")
    parser.add_argument("--check", action="store_true", help="验证登录态并汇总当前库存")
    parser.add_argument("--models", action="store_true", help="列出所有 GPU 型号和当前空闲数")
    parser.add_argument("--demo", action="store_true", help="离线演示表格与通知（不需要 token）")
    parser.add_argument("--no-toast", action="store_true", help="关闭系统气泡通知")
    parser.add_argument("--no-sound", action="store_true", help="关闭提示音")
    parser.add_argument("--open", action="store_true", help="有卡时自动打开创建页")
    parser.add_argument("--log", help="告警日志路径（默认 alerts.log）")
    parser.add_argument("--state", help="状态文件路径（默认 state.json）")
    parser.add_argument("--version", action="version", version=f"AutoDL 监控 {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    try:
        cfg: Config = load_config(args.config, args.env)
    except SystemExit:
        raise

    if args.token:
        cfg.token = args.token.strip()
    if args.interval:
        cfg.interval = args.interval
    if args.gpu:
        cfg.gpu = [part.strip() for part in args.gpu.replace("，", ",").split(",") if part.strip()]
    if args.dc:
        cfg.data_centers = [part.strip() for part in args.dc.replace("，", ",").split(",") if part.strip()]
    if args.min_idle:
        cfg.min_idle = args.min_idle
    if args.max_price is not None:
        cfg.max_price = args.max_price
    if args.all:
        cfg.show_all = True
    if args.no_toast:
        cfg.toast = False
    if args.no_sound:
        cfg.sound = False
    if args.open:
        cfg.open_browser = True
    if args.log:
        cfg.log_file = args.log
    if args.state:
        cfg.state_file = args.state

    cfg.interval = max(5.0, cfg.interval)
    cfg.min_idle = max(1, cfg.min_idle)

    if args.demo:
        return Monitor(cfg).demo()

    if not cfg.token:
        print(
            "没有找到 token。\n\n"
            "1) 浏览器登录 https://www.autodl.com 并打开 /create\n"
            "2) F12 → Network → 点任意 api/v1 请求 → 右键 Copy as cURL (bash)\n"
            "3) 复制请求头 authorization 后面那串（eyJ 开头）\n"
            "4) 填到 .env：AUTODL_TOKEN=eyJ...\n"
            "或直接运行：python monitor.py --token 'eyJ...'\n",
            file=sys.stderr,
        )
        return 1

    expiry = token_expiry(cfg.token)
    if expiry is not None and expiry <= datetime.now().astimezone():
        print(f"token 已过期（{expiry:%Y-%m-%d %H:%M}），请重新获取。", file=sys.stderr)
        return 2
    if expiry is not None:
        days = (expiry - datetime.now().astimezone()).days
        if days <= 3:
            print(f"提醒：token 还有 {days} 天过期，记得及时更新。", file=sys.stderr)

    monitor = Monitor(cfg)
    try:
        if args.check:
            return monitor.check()
        if args.models:
            return monitor.models()
        if args.json:
            return monitor.run_once(as_json=True)
        if args.once:
            return monitor.run_once()
        return monitor.run_forever()
    except AuthError as exc:
        monitor._auth_help(exc)  # noqa: SLF001 - 同包内的提示
        return 2
    except KeyboardInterrupt:
        print("\n已停止监控。", file=sys.stderr)
        return 0
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    try:
        code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        code = 0
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)
