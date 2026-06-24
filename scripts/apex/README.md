# Apex — Rich's V3 (Lighter paper)

Standalone copy of the V3.1 pipeline: SMRT Pro V3 TV alert -> 9 EMA (5m) retest ->
fixed $250 @ 30x -> 3-stage trail exit (SL -$30 -> BE +$20 -> at +$35 lock +$20 &
trail $15). Coins: HYPE, SOL, ZEC. 3-loss/60-min cooldown breaker. Telegram pause/
stop via @apexbot. Fully isolated: package `apex_bridge`, DB `apex.db`, container
`apex-bridge`, webhook apex.agentneo.cloud/webhook/apex.

## Run (local)
    cd scripts/apex
    cp .env.example .env   # fill TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / BRIDGE_SECRET
    python -m pytest -q
    python run_bridge.py --config config.apex.yaml

## Deploy (VPS, operator step)
    docker compose -f docker-compose.apex.yml up -d --build

## Knobs
All in config.apex.yaml. Entry filters (slope/ATR band) are ON at proven values;
tuning is deliberately deferred. Revert cooldown: cooldown.enabled=false.

## TV alerts
See TV_ALERTS.md — 3 alerts (HYPE/SOL/ZEC) to /webhook/apex.
