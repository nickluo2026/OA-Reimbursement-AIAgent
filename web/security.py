"""生产安全基座 [P0]：安全响应头 / 登录限流 / JSON 结构化日志格式器。

环境变量（均可选）：
    OA_ENV                   production（默认）/ development
    OA_ENABLE_HSTS           1（生产默认）/ 0 — 是否下发 HSTS（仅 HTTPS 请求时生效）
    OA_CSP_DESKTOP           覆盖桌面端 CSP 策略字符串
    OA_CSP_MOBILE            覆盖移动端(/m) CSP 策略字符串
    OA_LOGIN_MAX_ATTEMPTS    登录失败锁定阈值（默认 5）
    OA_LOGIN_WINDOW_SEC      失败计数窗口（默认 900 秒）
    OA_LOGIN_LOCKOUT_SEC     锁定时长（默认 900 秒）
    OA_REDIS_URL             提供时限流计数存入 Redis（多实例共享），否则进程内存
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

_TRUE = ("1", "true", "yes", "on")


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


# ═══════════════════════════════════════════════
# 安全响应头 [P0/P2]
# ═══════════════════════════════════════════════
# 移动端 /m：mobile.js / mobile.html 已去除内联事件（P2），可执行严格 CSP（script-src 'self'）。
# 桌面端：模板仍含内联事件处理器，暂放行 'unsafe-inline'，后续迭代收紧。
_CSP_MOBILE_DEFAULT = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
    "form-action 'self'; object-src 'none'"
)
_CSP_DESKTOP_DEFAULT = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'self'; "
    "frame-ancestors 'none'; form-action 'self'; object-src 'none'"
)


def apply_security_headers(app) -> None:
    """为所有响应注入安全头：CSP / X-Frame-Options / nosniff / HSTS 等。"""
    from flask import request

    oa_env = os.environ.get("OA_ENV", "production")
    hsts_enabled = env_flag("OA_ENABLE_HSTS", oa_env == "production")
    csp_mobile = os.environ.get("OA_CSP_MOBILE", _CSP_MOBILE_DEFAULT)
    csp_desktop = os.environ.get("OA_CSP_DESKTOP", _CSP_DESKTOP_DEFAULT)

    @app.after_request
    def _set_security_headers(resp):  # pragma: no cover - 逐头断言见测试
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        is_mobile = request.path == "/m" or request.path.startswith("/m/")
        h.setdefault("Content-Security-Policy", csp_mobile if is_mobile else csp_desktop)
        # HSTS 仅在 HTTPS（含反代 X-Forwarded-Proto）下下发，避免误伤本地 HTTP 调试
        forwarded_https = request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        if hsts_enabled and (request.is_secure or forwarded_https):
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp


# ═══════════════════════════════════════════════
# 登录限流 / 账号锁定 [P0]
# ═══════════════════════════════════════════════
class LoginRateLimiter:
    """登录失败限流器：窗口内失败 N 次 → 锁定 M 秒。

    默认进程内存实现；设置 OA_REDIS_URL 后计数存入 Redis（多实例共享）。
    key 建议为 "账号|IP" 组合，兼顾撞库与单点爆破两类场景。
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 900,
        lockout_seconds: int = 900,
        redis_url: str | None = None,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._redis = None
        if redis_url:
            try:  # pragma: no cover - 依赖外部 Redis
                import redis  # type: ignore

                self._redis = redis.Redis.from_url(
                    redis_url, socket_timeout=2, decode_responses=True
                )
                self._redis.ping()
            except Exception:
                logging.getLogger(__name__).warning("Redis 不可用，登录限流回退为进程内存实现")
                self._redis = None

    @classmethod
    def from_env(cls) -> LoginRateLimiter:
        return cls(
            max_attempts=int(os.environ.get("OA_LOGIN_MAX_ATTEMPTS", "5")),
            window_seconds=int(os.environ.get("OA_LOGIN_WINDOW_SEC", "900")),
            lockout_seconds=int(os.environ.get("OA_LOGIN_LOCKOUT_SEC", "900")),
            redis_url=os.environ.get("OA_REDIS_URL"),
        )

    # ── 查询是否锁定，返回剩余秒数（0 = 未锁定） ──
    def locked_for(self, key: str) -> int:
        now = time.time()
        if self._redis is not None:  # pragma: no cover
            try:
                ttl = self._redis.ttl(f"oa:login:lock:{key}")
                return max(0, int(ttl or 0))
            except Exception:
                pass
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until > now:
                return int(until - now) + 1
            self._locked_until.pop(key, None)
            return 0

    # ── 记录一次失败；达到阈值则锁定，返回是否已锁定 ──
    def record_failure(self, key: str) -> bool:
        now = time.time()
        if self._redis is not None:  # pragma: no cover
            try:
                rk = f"oa:login:fail:{key}"
                n = self._redis.incr(rk)
                if n == 1:
                    self._redis.expire(rk, self.window_seconds)
                if int(n) >= self.max_attempts:
                    self._redis.setex(f"oa:login:lock:{key}", self.lockout_seconds, "1")
                    self._redis.delete(rk)
                    return True
                return False
            except Exception:
                pass
        with self._lock:
            arr = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
            arr.append(now)
            self._failures[key] = arr
            if len(arr) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout_seconds
                self._failures.pop(key, None)
                return True
            return False

    # ── 登录成功后清空计数 ──
    def reset(self, key: str) -> None:
        if self._redis is not None:  # pragma: no cover
            try:
                self._redis.delete(f"oa:login:fail:{key}", f"oa:login:lock:{key}")
                return
            except Exception:
                pass
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


# ═══════════════════════════════════════════════
# JSON 结构化日志格式器 [P1]
# ═══════════════════════════════════════════════
class JsonLogFormatter(logging.Formatter):
    """stdlib logging → 单行 JSON（便于 ELK / Loki 采集）。

    通过 OA_LOG_FORMAT=json 在 web/app.py 中启用。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "unknown"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
