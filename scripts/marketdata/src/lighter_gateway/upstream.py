from __future__ import annotations
from typing import TYPE_CHECKING
import aiohttp

if TYPE_CHECKING:
    from .core import FetchFn


def make_fetch(session: aiohttp.ClientSession, base_url: str) -> FetchFn:
    base = base_url.rstrip("/")
    async def fetch(method: str, path: str, query: str):
        url = base + path + (("?" + query) if query else "")
        async with session.request(method, url, allow_redirects=False) as resp:
            body = await resp.read()
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            return (resp.status, body, ct)
    return fetch
