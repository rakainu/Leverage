# TradingView alert setup for Pro V3 → Scalping bridge

Configure 4 alerts on the SOLUSDT.P chart in TradingView, one per Pro V3 action.
Each alert uses the same webhook URL and a JSON message body with TradingView
placeholders — the bridge parses them tolerantly.

## Prereqs

- Chart: `BLOFIN:SOLUSDT.P` (or `BLOFIN:ZECUSDT.P`)
- Indicator: **Pro V3 [SMRT Algo]** applied to the chart
- TradingView plan with webhook alerts (Essential or higher)
- Bridge deployed and healthy at `https://blofin-bridge.srv1370094.hstgr.cloud`
- `BRIDGE_SECRET` from your local `.env` file — you'll paste it into every alert's message body

## Webhook URL (all alerts use this)

```
https://blofin-bridge.srv1370094.hstgr.cloud/webhook/pro-v3
```

## The action names accepted by the router

These are the **only** action names the bridge currently recognizes. Anything else returns 400.

| action           | purpose                            |
|------------------|------------------------------------|
| `buy`            | open long (queues for EMA retest)  |
| `sell`           | open short (queues for EMA retest) |
| `reversal_buy`   | close current + queue new long     |
| `reversal_sell`  | close current + queue new short    |

There is **no `close` or `sl` action**. Exits are handled entirely by the
bridge's native BloFin stop order (hard $13 SL on entry, promoted to
breakeven / lock / trail as profit grows). Do **not** wire Pro V3's SL
alert — the bridge will reject `{"action":"sl"}` with HTTP 422.

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

## The 4 alert JSON bodies

The bridge uses the extra `price`, `high`, `low`, `timeframe`, and `timestamp`
fields to capture a **signal snapshot** so it can later validate whether the
setup is still intact when price retests EMA(9). These fields are optional —
if TradingView fails to substitute a placeholder, the bridge will fall back
to live market data.

### Buy
```json
{"secret":"<BRIDGE_SECRET>","action":"buy","symbol":"SOL-USDT","timestamp":"{{timenow}}","price":"{{close}}","high":"{{high}}","low":"{{low}}","timeframe":"{{interval}}","source":"pro_v3"}
```

### Sell
```json
{"secret":"<BRIDGE_SECRET>","action":"sell","symbol":"SOL-USDT","timestamp":"{{timenow}}","price":"{{close}}","high":"{{high}}","low":"{{low}}","timeframe":"{{interval}}","source":"pro_v3"}
```

### Reversal Buy
```json
{"secret":"<BRIDGE_SECRET>","action":"reversal_buy","symbol":"SOL-USDT","timestamp":"{{timenow}}","price":"{{close}}","high":"{{high}}","low":"{{low}}","timeframe":"{{interval}}","source":"pro_v3"}
```

### Reversal Sell
```json
{"secret":"<BRIDGE_SECRET>","action":"reversal_sell","symbol":"SOL-USDT","timestamp":"{{timenow}}","price":"{{close}}","high":"{{high}}","low":"{{low}}","timeframe":"{{interval}}","source":"pro_v3"}
```

**Important:** `symbol` in the message body is `SOL-USDT` (dash, not slash, no `.P` suffix) — that's BloFin's canonical instrument id. Change to `ZEC-USDT` on the ZEC chart's alerts.

## Updating existing alerts (migration steps)

If you already have the older alerts (without snapshot fields), you just need to **replace the Message field** on each one:

1. Open the chart in TradingView
2. Click the Alerts panel (top right ⏰ icon)
3. For each existing `buy`/`sell`/`reversal_*` alert:
   a. Right-click → **Edit**
   b. Go to the **Message** tab
   c. Delete the old JSON body
   d. Paste the new JSON body from above (with your `BRIDGE_SECRET` substituted)
   e. Click **Save**
4. The Webhook URL stays the same — no changes needed there.
5. **Delete** any existing `sl` alerts for both SOL and ZEC charts — Pro V3's SL is no longer acted on and the bridge will 422 those payloads. Exits are bridge-managed.

## Signal lifecycle (what the bridge does now)

1. Pro V3 fires `buy` or `sell` → TV POSTs the JSON body to the bridge
2. Bridge captures a **signal snapshot**: signal price, candle high/low, EMA(9), EMA slope, ATR, bar timestamp — using your payload values or falling back to market data
3. Signal becomes a **pending candidate** (not yet a trade)
4. Poller checks every 2s (all of these can cancel the signal before any trade is placed):
   - `expired_time_limit` — signal older than `max_signal_age_seconds` (default 1800s / 30 min)
   - `expired_bar_limit` — more than `max_signal_bars` bars elapsed since signal (default 6)
   - `invalidated_structure_break` — any bar closed below (long) or above (short) the signal candle extreme
   - `invalidated_slope_flip` — EMA slope flipped against the trade direction
   - `invalidated_price_drift` — price drifted > 0.35% from signal price, or more than 0.5× ATR
   - `invalidated_position_open` — you already have a position on this symbol
5. Once price retests EMA(9), the bridge revalidates one more time (slope, structure, drift, confirmation candle)
6. Only if **everything** still checks out does the bridge fire the entry — logged as `executed_retest_validated`

## After creating the alerts

1. Tail the bridge logs:
   ```bash
   ssh root@46.202.146.30 "docker logs -f scalping"
   ```
2. Check bridge state:
   ```bash
   curl "https://blofin-bridge.srv1370094.hstgr.cloud/status?secret=<BRIDGE_SECRET>"
   ```
3. Watch for these structured log lines:
   - `signal_created id=N buy SOL-USDT ...` — webhook received, snapshot captured
   - `pending_invalidated id=N ... reason=invalidated_*` — signal killed before entry
   - `pending_expired id=N ... reason=expired_*` — signal timed out
   - `pending_retest_seen id=N ...` — price touched EMA, revalidating
   - `pending_revalidation_failed id=N ... reason=retest_failed_*` — retest came but setup has broken
   - `pending_revalidation_passed id=N ...` — all checks passed
   - `executed_retest_validated id=N ...` — entry placed

## Troubleshooting

- **Webhook doesn't arrive at the bridge:** check the TradingView alert is enabled (the bell icon should be lit) and that your TradingView plan supports webhooks. On free plans, webhook alerts are not available.
- **Bridge returns 401 "invalid secret":** the `<BRIDGE_SECRET>` in the alert's message body doesn't match the `BRIDGE_SECRET` in the bridge's `.env`. Fix the alert, save.
- **Bridge returns 400 "bad payload":** the JSON body is malformed — probably a stray curly quote from the browser or a line break. Re-paste from this doc.
- **Bridge returns 422 "unknown action":** the `action` field has a value the router doesn't recognize. Only `buy`, `sell`, `sl`, `reversal_buy`, `reversal_sell` are accepted.
- **Bridge returns 423 "symbol frozen":** startup reconciliation found drift between SQLite and BloFin positions. SSH to the VPS and run:
  ```bash
  ssh root@46.202.146.30 "cd /docker/scalping && docker compose stop && rm -f data/bridge.db && docker compose up -d"
  ```
  This wipes the bridge state and restarts. Only safe if BloFin has zero open positions on the symbol.
- **Signals being cancelled too aggressively:** check logs for the reason code. If `invalidated_price_drift` fires too often, raise `max_price_drift_percent` in `config/blofin_bridge.yaml`. If `retest_failed_confirmation` blocks too many good trades, set `require_retest_confirmation_candle: false`.

## Going live

After you've seen at least two complete clean cycles on demo where a long and a short each ran through `signal_created → pending_retest_seen → executed_retest_validated → SL/trail → closed`:

1. On the VPS, edit `/docker/scalping/.env`:
   ```
   BLOFIN_ENV=live
   ```
2. Drop the margin temporarily as a safety net — edit `/docker/scalping/config/blofin_bridge.yaml`:
   ```yaml
   symbols:
     SOL-USDT:
       margin_usdt: 10   # was 100, drop to 10 for first live cycles
   ```
3. Restart:
   ```bash
   ssh root@46.202.146.30 "cd /docker/scalping && docker compose up -d"
   ```
4. Wait for one full live cycle. Verify on BloFin live UI.
5. After two clean live cycles at $10 margin, raise back to `margin_usdt: 100` and restart.
