"""项目配置：caseflow.yaml + .env + 系统环境变量。

查找顺序：环境变量 CASEFLOW_PROJECT 指定的目录 → 当前目录向上找第一个含 caseflow.yaml 的目录 → 当前目录。
取值优先级：系统环境变量 > 项目根 .env > caseflow.yaml 里写的值。
caseflow.yaml 里任何字符串都可以写 ${NAME}，运行时从环境变量 / .env 取值（密码、地址不进 git）。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_NAME = "caseflow.yaml"
_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class ConfigError(Exception):
    pass


def project_root() -> Path:
    if env_dir := os.environ.get("CASEFLOW_PROJECT"):
        return Path(env_dir).resolve()
    cwd = Path.cwd().resolve()
    for d in (cwd, *cwd.parents):
        if (d / CONFIG_NAME).is_file():
            return d
    return cwd


@lru_cache(maxsize=None)
def _dotenv(root: str) -> dict[str, str]:
    p = Path(root) / ".env"
    out: dict[str, str] = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                out[key.strip()] = value.strip().strip("\"'")
    return out


def env(name: str, default: str | None = None) -> str | None:
    """系统环境变量优先，其次 .env；空字符串当作没配。"""
    return os.environ.get(name) or _dotenv(str(project_root())).get(name) or default


def require_env(name: str) -> str:
    value = env(name)
    if value is None:
        raise ConfigError(f"缺少配置 {name}：写进项目根 .env（参考 .env.example），或设为系统环境变量")
    return value


def fill_env(value: Any) -> Any:
    """递归把字符串里的 ${NAME} 换成环境变量 / .env 的值。"""
    if isinstance(value, str):
        return _VAR.sub(lambda m: require_env(m.group(1)), value)
    if isinstance(value, dict):
        return {k: fill_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_env(v) for v in value]
    return value


DEFAULTS: dict[str, Any] = {
    "api": {
        "base_url": None,
        "timeout": 15,
        "headers": {},
        # 国内常见「HTTP 一律 200，靠 body.code 判成败」：配了 envelope 才启用 expect_ok / expect_code
        "envelope": None,  # {code: code, ok: 0, msg: msg, data: data}
        "login": None,
        "roles": {},
        "hook": None,  # "模块:函数"，签名 / 加密等：before_request(method, url, kwargs) -> kwargs
    },
    "ui": {
        "base_url": None,
        "auth_dir": ".auth",
        "channel": None,  # chrome / msedge；不填用 Playwright 自带 Chromium
        "timeout": 10000,
    },
    "safety": {
        # 主机名按 . 和 - 切开后命中这些词，就拒绝发非 GET 请求（防误打生产）；设 CASEFLOW_ALLOW_PROD=1 可放行
        "block_host_words": ["prod", "production", "live", "online"],
    },
    "redact": {
        "headers": ["authorization", "cookie", "x-api-key", "x-token", "token"],
        "fields": ["password", "pwd", "passwd", "secret", "token", "access_token", "refresh_token"],
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(base[k], v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return out


@lru_cache(maxsize=None)
def _load(root: str) -> dict:
    p = Path(root) / CONFIG_NAME
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError(f"{p} 顶层必须是字典")
    unknown = set(raw or {}) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"{CONFIG_NAME} 有不认识的顶层配置 {sorted(unknown)}，可用：{sorted(DEFAULTS)}")
    return _merge(DEFAULTS, raw or {})


def config() -> dict:
    """合并默认值后的配置（未替换 ${NAME}；取具体值时用 setting()）。"""
    return _load(str(project_root()))


def setting(*path: str, default: Any = None) -> Any:
    """setting("api", "base_url") → 已替换 ${NAME} 的值。"""
    node: Any = config()
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return default if node is None else fill_env(node)


def reset() -> None:
    """切换项目 / 改了 .env 后清缓存（自检用）。"""
    _load.cache_clear()
    _dotenv.cache_clear()
