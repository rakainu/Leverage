from __future__ import annotations
from dataclasses import dataclass, field
import yaml


@dataclass
class GatewayConfig:
    upstream: str
    host: str
    port: int
    rate_per_s: float
    burst: float
    max_stale_s: float
    cache_capacity: int
    ttl: dict[str, float] = field(default_factory=dict)
    default_ttl: float = 2.0

    def ttl_for(self, path: str) -> float:
        best_len, best_ttl = -1, self.default_ttl
        for prefix, secs in self.ttl.items():
            if path.startswith(prefix) and len(prefix) > best_len:
                best_len, best_ttl = len(prefix), secs
        return best_ttl


def load_config(path: str) -> GatewayConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    listen = raw.get("listen", {})
    rl = raw.get("rate_limit", {})
    ttl = dict(raw.get("ttl", {}))
    default_ttl = float(ttl.pop("default", 2.0))
    return GatewayConfig(
        upstream=raw["upstream"].rstrip("/"),
        host=str(listen.get("host", "0.0.0.0")),
        port=int(listen.get("port", 8060)),
        rate_per_s=float(rl.get("rate_per_s", 4.0)),
        burst=float(rl.get("burst", 8.0)),
        max_stale_s=float(raw.get("max_stale_s", 15.0)),
        cache_capacity=int(raw.get("cache_capacity", 2000)),
        ttl={str(k): float(v) for k, v in ttl.items()},
        default_ttl=default_ttl,
    )
