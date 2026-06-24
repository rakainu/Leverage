from __future__ import annotations
import os
import aiohttp
from aiohttp import web
from lighter_gateway.config import load_config
from lighter_gateway.core import Gateway
from lighter_gateway.upstream import make_fetch
from lighter_gateway.server import build_app


async def _make_app() -> web.Application:
    cfg = load_config(os.environ.get("GATEWAY_CONFIG", "config.yaml"))
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    gw = Gateway(cfg, make_fetch(session, cfg.upstream))
    app = build_app(gw)
    app["_session"] = session
    async def _close(_app):
        await session.close()
    app.on_cleanup.append(_close)
    app["_cfg"] = cfg
    return app


def main():
    cfg = load_config(os.environ.get("GATEWAY_CONFIG", "config.yaml"))
    web.run_app(_make_app(), host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
