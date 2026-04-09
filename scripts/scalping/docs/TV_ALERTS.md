# TradingView alert setup for Pro V3 → BloFin bridge

Configure 8 alerts on the SOLUSDT.P chart in TradingView, one per Pro V3 condition.
Each alert uses the same webhook URL and a hard-coded JSON body.

## Prereqs

- Chart: `BLOFIN:SOLUSDT.P`
- Indicator: **Pro V3 [SMRT Algo]** applied to the chart
- TradingView plan with webhook alerts (Essential or higher)
- Bridge deployed and healthy at `https://blofin-bridge.srv1370094.hstgr.cloud`
- `BRIDGE_SECRET` from your local `.env` file — you'll paste it into every alert's message body

## Webhook URL (all alerts use this)

```
https://blofin-bridge.srv1370094.hstgr.cloud/webhook/pro-v3
```

## Steps per alert

1. Right-click the chart → **Add alert** (or press the ⏰ icon in the right sidebar)
2. **Condition:** select `Pro V3 [SMRT Algo]` from the first dropdown, then pick the trigger from the second dropdown (see table below)
3. **Options:** `Once Per Bar Close`
4. **Expiration:** "Open-ended" (or "Until stopped")
5. **Notifications tab:**
   - Check **Webhook URL**
   - Paste: `https://blofin-bridge.srv1370094.hstgr.cloud/webhook/pro-v3`
6. **Message tab** (or the "Edit Message" button):
   - Delete the default "Alert Fired!" text
   - Paste the JSON message body from the table below
   - Replace `<BRIDGE_SECRET>` with your actual secret (the value from `.env` on the `BRIDGE_SECRET=` line)
7. Click **Create**

## The 8 alerts

Create one alert per row. Every message body is a single JSON line — do not add line breaks.

| # | Pro V3 condition | Message body (copy verbatim, replace `<BRIDGE_SECRET>`) |
|---|---|---|
| 1 | **Buy** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"buy","source":"pro_v3"}` |
| 2 | **Sell** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"sell","source":"pro_v3"}` |
| 3 | **TP1** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"tp1","source":"pro_v3"}` |
| 4 | **TP2** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"tp2","source":"pro_v3"}` |
| 5 | **TP3** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"tp3","source":"pro_v3"}` |
| 6 | **SL** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"sl","source":"pro_v3"}` |
| 7 | **Reversal Buy** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"reversal_buy","source":"pro_v3"}` |
| 8 | **Reversal Sell** | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"reversal_sell","source":"pro_v3"}` |

**Important:** `symbol` in the message body is `SOL-USDT` (dash, not slash, no `.P` suffix) — that's BloFin's canonical instrument id. TradingView's symbol (`BLOFIN:SOLUSDT.P`) is unrelated to what the bridge uses; the bridge only reads the `symbol` field from the JSON body.

## After creating the alerts

1. Tail the bridge logs to watch webhook arrivals in real time:
   ```bash
   ssh root@46.202.146.30 "docker logs -f blofin-bridge"
   ```
2. Check bridge state anytime:
   ```bash
   curl "https://blofin-bridge.srv1370094.hstgr.cloud/status?secret=<BRIDGE_SECRET>"
   ```
3. Wait for Pro V3 to fire a real signal. First Buy or Sell should:
   - Appear in the bridge logs within seconds of firing
   - Open a position on BloFin demo (visible in the BloFin Demo Trading UI → Futures → Positions)
   - Return an `opened: true` response visible in the logs

## Troubleshooting

- **Webhook doesn't arrive at the bridge:** check the TradingView alert is enabled (the bell icon should be lit) and that your TradingView plan supports webhooks. On free plans, webhook alerts are not available.
- **Bridge returns 401 "invalid secret":** the `<BRIDGE_SECRET>` in the alert's message body doesn't match the `BRIDGE_SECRET` in the bridge's `.env`. Fix the alert, save.
- **Bridge returns 400 "bad payload":** the JSON body is malformed — probably a stray curly quote from the browser or a line break. Re-paste from this doc.
- **Bridge returns 423 "symbol frozen":** startup reconciliation found drift between SQLite and BloFin positions. SSH to the VPS and run:
  ```bash
  ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose stop && rm -f data/bridge.db && docker compose up -d"
  ```
  This wipes the bridge state and restarts. Only safe if BloFin has zero open positions on the symbol.
- **Bridge returns 500 with a BloFin error code:** check `docker logs blofin-bridge --tail 100` for the traceback. The most common issue is the BloFin demo account being in hedge mode; set it to one-way mode in the BloFin Futures settings.

## Going live

After you've seen at least two complete Pro V3 cycles flow through the bridge cleanly on demo:

1. On the VPS, edit `/docker/blofin-bridge/.env`:
   ```
   BLOFIN_ENV=live
   ```
2. Drop the margin temporarily as a safety net — edit `/docker/blofin-bridge/config/blofin_bridge.yaml`:
   ```yaml
   symbols:
     SOL-USDT:
       margin_usdt: 10   # was 100, drop to 10 for first live cycles
   ```
3. Restart:
   ```bash
   ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose up -d"
   ```
4. Wait for one full live cycle to complete. Verify on the BloFin live UI.
5. After two clean live cycles at $10 margin, raise back to `margin_usdt: 100` and restart.
