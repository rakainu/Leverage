from __future__ import annotations
from aiohttp import web
from .core import Gateway


def build_app(gw: Gateway) -> web.Application:
    app = web.Application()

    async def health(_req):
        return web.json_response({"ok": True})

    async def stats(_req):
        return web.json_response(gw.stats())

    async def proxy(req: web.Request):
        path = req.path
        query = req.rel_url.query_string
        resp = await gw.handle(req.method, path, query)
        return web.Response(status=resp.status, body=resp.body,
                            content_type=resp.content_type.split(";")[0],
                            headers={"X-Gateway-Source": resp.source})

    app.router.add_get("/__gw/health", health)
    app.router.add_get("/__gw/stats", stats)
    app.router.add_route("*", "/{tail:.*}", proxy)
    return app
