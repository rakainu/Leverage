# Deploy: Pro V3 SOL scale-out paper bridge

Forward-tests the validated SOL config (see `analysis/pro_v3_real/FINDINGS.md`):
real Pro V3 [SMRT Algo] webhooks → EMA9 retest → ATR scale-out exits, paper on
Lighter (zero-fee). Runs as a **separate container** (`pro-v3-scaleout`) next to
the existing replica/trail `lighter-bridge` — that one is untouched.

## What it does
- Listens for Pro V3 buy/sell webhooks on `:8080` (`/webhook/pro-v3`).
- Same EMA9-retest entry gate as the live BloFin bridge.
- Exits: SL 3.5×ATR, TP1/2/3 = 1/2/3 ATR, scale 34/33/33, **breakeven after TP1**.
- SOL only (ZEC had no edge). Paper PnL + signals logged to `data/pro_v3_scaleout.db`.

## Prerequisites (decisions needed)
1. **Webhook secret** — set `BRIDGE_SECRET=<value>` in `/docker/lighter-paper/.env`
   (or wherever this bridge is deployed). Used to authenticate TradingView alerts.
2. **Public webhook URL** — pick a host for Traefik to route to `127.0.0.1:8094`,
   e.g. `https://pro-v3.agentneo.cloud/webhook/pro-v3` (same Traefik file-provider
   pattern as the Lighter dashboard). Needs to be reachable by TradingView.

## Steps (on the VPS)
```bash
cd /docker/lighter-paper            # repo dir holding this bridge
git pull
echo 'BRIDGE_SECRET=<your-secret>' >> .env     # if not already set
docker compose -f docker-compose.scaleout.yml up -d --build
docker logs -f pro-v3-scaleout --tail 50       # expect: webhook listener on 0.0.0.0:8080
```
Add a Traefik file-provider route mapping the chosen host → `127.0.0.1:8094`
(copy the dashboard's route block, change host + port). Confirm:
```bash
curl https://<your-host>/health        # -> {"ok": true, "symbols": ["SOL"]}
```

## TradingView alerts (the real signal source)
On the **SOLUSDT.P** chart with **Pro V3 [SMRT Algo]** applied, create 2 alerts,
`Once Per Bar Close`, webhook URL = the public URL above, messages:

| Pro V3 condition | Message body |
|---|---|
| Buy  | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"buy","source":"pro_v3"}` |
| Sell | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"sell","source":"pro_v3"}` |

(Only buy/sell are needed — the bridge owns the scale-out exits. I can create these
via the TradingView MCP once the URL + secret are set.)

## Promote to live (only if paper tests well)
Swap the `PaperExecutor` for the real Lighter order path in `executor.py` (same SDK),
flip nothing else. The strategy/exit logic is identical.

## Known limitation
On restart with an open scale-out position, exit state is not yet re-armed from the
DB (the trail bridge restores; scale-out restore is a TODO). Low impact for paper.
