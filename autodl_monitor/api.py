"""AutoDL 网站接口封装。

所有接口都来自 https://www.autodl.com 的前端实际调用，使用网页登录态
（请求头 `authorization` 里的 JWT，aud=website）。开发者 Token 无法访问这些
接口，只能用网页登录的 token。

主要接口：
    GET  /api/v1/region/list                       地区列表（公开）
    POST /api/v1/machine/region/gpu_type           按地区统计各型号空闲/总数
    POST /api/v1/instance/schedule/order/price/preview   最低/最高价格
    GET  /api/v1/user/detail                       当前登录用户
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

import requests

BASE_URL = "https://www.autodl.com"
APP_VERSION = "v6.51.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
)


class AutoDLError(RuntimeError):
    """接口调用失败的基类。"""


class AuthError(AutoDLError):
    """登录态失效（token 过期 / 复制不完整 / 账号被登出）。"""


class ApiError(AutoDLError):
    """接口返回了非 Success 的业务错误。"""


@dataclass(frozen=True)
class Region:
    """一个可创建实例的机房分区（region_sign 是接口里的地区代号）。"""

    sign: str
    name: str
    data_center: str


@dataclass(frozen=True)
class UserInfo:
    uid: int | None
    username: str


def decode_jwt(token: str) -> dict[str, Any]:
    """不验签地取出 JWT payload，只用来读 uid / exp 做提示。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001 - 任何解析失败都当作「不是 JWT」
        return {}


def token_expiry(token: str) -> datetime | None:
    """token 的过期时间（带本地时区），解析不出来返回 None。"""
    exp = decode_jwt(token).get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()


class AutoDLClient:
    """带重试和错误翻译的 AutoDL HTTP 客户端。"""

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 15.0,
        retries: int = 2,
        app_version: str = APP_VERSION,
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, retries)
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Authorization": token,
                "appversion": app_version,
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/create",
            }
        )

    # ------------------------------------------------------------------ 底层

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{BASE_URL}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:  # 网络层错误，重试
                last_exc = exc
            else:
                if resp.status_code >= 500:
                    last_exc = ApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                elif resp.status_code != 200:
                    raise ApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    try:
                        payload = resp.json()
                    except ValueError as exc:
                        raise ApiError(f"返回的不是 JSON：{resp.text[:200]}") from exc
                    return self._unwrap(payload)
            if attempt < self.retries:
                time.sleep(0.8 * (attempt + 1))
        raise ApiError(f"请求失败：{last_exc}") from last_exc

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> Any:
        code = payload.get("code")
        if code == "Success":
            return payload.get("data")
        msg = str(payload.get("msg") or "未知错误")
        if code == "AuthorizeFailed":
            raise AuthError(msg)
        raise ApiError(f"{code}: {msg}")

    # ------------------------------------------------------------------ 业务

    def regions(self) -> list[Region]:
        """全部可创建实例的地区。"""
        data = self._request("GET", "/api/v1/region/list") or []
        result: list[Region] = []
        for item in data:
            if item.get("used_for") != "all" or item.get("visible") != "all":
                continue
            sign = str(item.get("region_sign") or "")
            dc = str(item.get("data_center") or "")
            if not sign or not dc:
                continue
            result.append(Region(sign=sign, name=str(item.get("region_name") or dc), data_center=dc))
        return result

    def gpu_counts(self, region_signs: Sequence[str]) -> dict[str, dict[str, int]]:
        """指定地区（可多个，接口会做汇总）各 GPU 型号的空闲数与总数。

        返回 {型号: {"idle": int, "total": int}}。
        """
        data = self._request(
            "POST",
            "/api/v1/machine/region/gpu_type",
            json={"region_sign_list": list(region_signs)},
        ) or []
        counts: dict[str, dict[str, int]] = {}
        for group in data:
            if not isinstance(group, dict):
                continue
            for gpu_name, info in group.items():
                if not isinstance(info, dict):
                    continue
                counts[str(gpu_name)] = {
                    "idle": int(info.get("idle_gpu_num") or 0),
                    "total": int(info.get("total_gpu_num") or 0),
                }
        return counts

    def min_price(self, gpu_name: str, data_center: str, num: int = 1) -> float | None:
        """某地区某型号 1 张卡按量计费的最低单价（元/小时）。"""
        data = self._request(
            "POST",
            "/api/v1/instance/schedule/order/price/preview",
            json={
                "num": num,
                "gpu_type": [gpu_name],
                "data_center": data_center,
                "expand_data_disk": 0,
            },
        ) or {}
        info = data.get("min_price_info") or {}
        raw = info.get("payg_price")
        if not isinstance(raw, (int, float)) or raw <= 0:
            return None
        return round(raw / 1000, 2)

    def user(self) -> UserInfo:
        """当前 token 对应的账号，用来验证登录态是否有效。"""
        data = self._request("GET", "/api/v1/user/detail") or {}
        uid = data.get("id")
        return UserInfo(uid=int(uid) if isinstance(uid, int) else None, username=str(data.get("username") or ""))

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "AutoDLClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
