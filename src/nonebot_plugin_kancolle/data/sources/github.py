"""GitHub raw / API 客户端封装。

数据源大多托管在 GitHub，统一通过这个模块拉取：
- raw 文件：raw.githubusercontent.com（无速率限制，但无版本指纹）
- commit_sha：api.github.com（匿名限流 60/hr，每周更新场景下足够）

提供 ETag 支持：拉 raw 时记录 ETag，下次 If-None-Match 走 304 不消耗带宽。
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
GITHUB_API_BASE = "https://api.github.com"


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


async def fetch_raw(
    client: httpx.AsyncClient,
    repo: str,
    path: str,
    ref: str = "master",
    if_none_match: str | None = None,
) -> FetchResult:
    """从 raw.githubusercontent.com 拉取文件。

    匿名访问，无速率限制；支持 ETag 条件请求以节省带宽。
    """
    url = f"{GITHUB_RAW_BASE}/{repo}/{ref}/{path}"
    headers = {"If-None-Match": if_none_match} if if_none_match else None
    resp = await client.get(url, headers=headers, follow_redirects=True)
    etag = resp.headers.get("ETag")
    if resp.status_code == 304:
        return FetchResult(body=b"", etag=etag, not_modified=True, status=304)
    resp.raise_for_status()
    return FetchResult(body=resp.content, etag=etag, not_modified=False, status=resp.status_code)


async def fetch_latest_commit_sha(
    client: httpx.AsyncClient,
    repo: str,
    ref: str = "master",
    token: str | None = None,
) -> str:
    """通过 GitHub API 取指定分支最新 commit 的 sha。

    用于为 raw 文件附加版本指纹（写入 sources.version 与 provenance）。
    匿名限流 60/hr；每周更新场景下足够。建议传入 token 以提高限额。
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/commits/{ref}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await client.get(url, headers=headers, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    sha = data.get("sha")
    if not sha:
        raise ValueError(f"unexpected commits response for {repo}: missing sha")
    return sha
