from __future__ import annotations
import aiohttp


def make_fetch(session: aiohttp.ClientSession, base_url: str):
    base = base_url.rstrip("/")
    async def fetch(method: str, path: str, query: str):
        url = base + path + (("?" + query) if query else "")
        async with session.request(method, url, allow_redirects=False) as resp:
            body = await resp.read()
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            return (resp.status, body, ct)
    return fetch
