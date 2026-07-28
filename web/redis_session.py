"""[P1] Redis 服务端会话：会话数据存 Redis，Cookie 仅存随机 SID。

相比 Flask 默认的客户端签名 Cookie 会话：
    1. 登出 / 强制下线时可在服务端立即失效（吊销被盗 Cookie）；
    2. 多实例部署共享会话，无需粘性负载均衡；
    3. 会话内容不出服务器，Cookie 体积恒定。

启用方式：设置 OA_REDIS_URL（如 redis://:pass@127.0.0.1:6379/0），
由 web/app.py 在启动时调用 enable_redis_sessions()。
Redis 不可用时自动回退 Flask 默认 Cookie 会话（不阻断启动）。
"""

from __future__ import annotations

import json
import logging
import os
import secrets

from flask.sessions import SessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict

logger = logging.getLogger(__name__)

_KEY_PREFIX = "oa:sess:"


class RedisSession(CallbackDict, SessionMixin):
    def __init__(self, initial=None, sid: str = ""):
        def on_update(_self):
            self.modified = True

        super().__init__(initial, on_update)
        self.sid = sid
        self.modified = False


class RedisSessionInterface(SessionInterface):
    def __init__(self, redis_client, ttl_seconds: int):
        self._redis = redis_client
        self._ttl = ttl_seconds

    def open_session(self, app, request):
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        sid = request.cookies.get(cookie_name)
        if sid:
            try:
                raw = self._redis.get(_KEY_PREFIX + sid)
                if raw:
                    return RedisSession(json.loads(raw), sid=sid)
            except Exception:  # pragma: no cover - Redis 瞬断
                logger.warning("读取 Redis 会话失败，视为未登录")
        return RedisSession(sid=secrets.token_urlsafe(32))

    def save_session(self, app, session, response):
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        key = _KEY_PREFIX + session.sid
        if not session:
            # 会话被清空（登出）→ 服务端立即删除，Cookie 一并清除
            try:
                self._redis.delete(key)
            except Exception:  # pragma: no cover
                pass
            if session.modified:
                response.delete_cookie(cookie_name, domain=domain, path=path)
            return
        try:
            self._redis.setex(key, self._ttl, json.dumps(dict(session), ensure_ascii=False))
        except Exception:  # pragma: no cover - Redis 瞬断时保持已有 Cookie
            logger.warning("写入 Redis 会话失败")
            return
        response.set_cookie(
            cookie_name,
            session.sid,
            max_age=self._ttl,
            httponly=self.get_cookie_httponly(app),
            secure=self.get_cookie_secure(app),
            samesite=self.get_cookie_samesite(app),
            domain=domain,
            path=path,
        )


def enable_redis_sessions(app, redis_url: str, ttl_seconds: int | None = None) -> bool:
    """接入 Redis 会话；连接失败时返回 False 并保持默认 Cookie 会话。"""
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(redis_url, socket_timeout=2)
        client.ping()
    except Exception as e:
        logger.warning("Redis 连接失败（%s），回退 Cookie 会话", e)
        return False
    ttl = ttl_seconds or int(os.environ.get("OA_SESSION_ABS_MIN", "480")) * 60
    app.session_interface = RedisSessionInterface(client, ttl)
    return True
