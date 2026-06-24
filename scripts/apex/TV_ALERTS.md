# Apex — TradingView Alerts (SMRT Pro V3)

Create 3 alerts (HYPE, SOL, ZEC), one per coin, from the SMRT Algo Pro V3 indicator.

- Condition: SMRT Pro V3 buy/sell signal
- Webhook URL: `https://apex.agentneo.cloud/webhook/apex`
- Message (JSON):
  ```json
  {"secret": "<BRIDGE_SECRET>", "symbol": "{{ticker}}", "action": "buy", "source": "pro_v3"}
  ```
  (and a matching `"action": "sell"` alert)
- `{{ticker}}` resolves to e.g. `ZECUSDT.P`; the webhook maps it to the Lighter market (`ZEC`).

Gotcha: TradingView alerts silently expire (plan tier / inactivity). If Apex stops
filling, check the TV alerts panel FIRST before touching the bridge.
