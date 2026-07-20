"""GitHub raw / API 客户端封装。

数据源大多托管在 GitHub，统一通过这个模块拉取：
- raw 文件：优先 jsDelivr CDN（国内可达性好），失败回退 raw.githubusercontent.com
- commit_sha：api.github.com（匿名限流 60/hr，每周更新场景下足够）

瞬时网络错误（ReadError / ConnectError / 超时 / 代理错误等）自动重试。

注：ETag 条件请求接口保留（if_none_match 参数），但 jsDelivr 不支持 304，故
仅在 raw.githubusercontent.com 兜底分支生效。当前所有调用方均不传 if_none_match。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
JSDELIVR_BASE = "https://cdn.jsdelivr.net/gh"
GITHUB_API_BASE = "https://api.github.com"

# 视为瞬时性、值得重试的网络错误
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
)
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE = 0.5  # 秒；第 n 次重试前等待 0.5 * 2^n
_PER_REQUEST_TIMEOUT = 10.0  # 单次请求超时（覆盖 client 默认 30s，避免最坏情况下重试堆积过久）


@dataclass
class FetchResult:
    """HTTP 抓取结果。

    body 为字节数组；not_modified=True 表示远端未变化（304），调用方应使用本地缓存。
    etag 是本次响应的 ETag（可空），下次请求应作为 if_none_match 传入。
    """

    body: bytes
    etag: str | None
    not_modified: bool
    status: int


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = _PER_REQUEST_TIMEOUT,
) -> httpx.Response:
    """对单个 URL 重试 ``_MAX_ATTEMPTS`` 次，仅重试瞬时网络错误；4xx/5xx 立即返回。

    退避：第 n 次失败后等待 ``_RETRY_BACKOFF_BASE * 2**n`` 秒再重试。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return await client.get(url, headers=headers, follow_redirects=True, timeout=timeout)
        except _RETRYABLE_ERRORS as e:
            last_exc = e
            if attempt == _MAX_ATTEMPTS - 1:
                break
            await asyncio.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


async def fetch_raw(
    client: httpx.AsyncClient,
    repo: str,
    path: str,
    ref: str = "master",
    if_none_match: str | None = None,
) -> FetchResult:
    """从 GitHub 拉取 raw 文件。

    优先 jsDelivr CDN（国内可达性好），失败时回退 raw.githubusercontent.com。
    对瞬时网络错误自动重试 ``_MAX_ATTEMPTS`` 次。

    ETag 条件请求仅在 raw.githubusercontent.com 兜底分支生效（jsDelivr 不支持 304）。
    """
    # 候选 (url, supports_etag)，按优先级排序
    candidates: list[tuple[str, bool]] = [
        (f"{JSDELIVR_BASE}/{repo}@{ref}/{path}", False),
        (f"{GITHUB_RAW_BASE}/{repo}/{ref}/{path}", True),
    ]

    errors: list[str] = []
    for url, supports_etag in candidates:
        headers: dict[str, str] | None = None
        if if_none_match and supports_etag:
            headers = {"If-None-Match": if_none_match}
        try:
            resp = await _get_with_retry(client, url, headers)
        except Exception as e:
            errors.append(f"{url} -> {type(e).__name__}: {e!r}")
            continue
        etag = resp.headers.get("ETag") if supports_etag else None
        if resp.status_code == 304:
            return FetchResult(body=b"", etag=etag, not_modified=True, status=304)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            errors.append(f"{url} -> HTTP {resp.status_code}")
            continue
        return FetchResult(
            body=resp.content, etag=etag, not_modified=False, status=resp.status_code
        )

    raise RuntimeError(
        f"all fetch attempts failed for {repo}/{ref}/{path}: " + "; ".join(errors)
    )


async def fetch_latest_commit_sha(
    client: httpx.AsyncClient,
    repo: str,
    ref: str = "master",
    token: str | None = None,
) -> str:
    """通过 GitHub API 取指定分支最新 commit 的 sha。

    用于为 raw 文件附加版本指纹（写入 sources.version 与 provenance）。
    匿名限流 60/hr；每周更新场景下足够。建议传入 token 以提高限额。

    对瞬时网络错误自动重试 ``_MAX_ATTEMPTS`` 次。
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/commits/{ref}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await _get_with_retry(client, url, headers)
    resp.raise_for_status()
    data = resp.json()
    sha = data.get("sha")
    if not sha:
        raise ValueError(f"unexpected commits response for {repo}: missing sha")
    return sha
