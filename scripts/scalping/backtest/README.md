# Backtest Harness — canonical leverage strategy testing

Single source of truth for crypto **leverage** strategy testing in this project.
Honest-fill, liquidation-aware simulation + risk-adjusted metrics + optuna search
+ overfit-resistant validation. **Use this, not freqtrade**, for any go/no-go call.
(freqtrade is fine as a fast idea-screening sandbox — but the verdict comes from here.)

## Modules

| file | what |
|---|---|
| `engine.py` | The vetted honest simulator (formerly `btengine.py`). No lookahead, pessimistic fills, funding, isolated-margin leverage + liquidation. Test-pinned. |
| `data.py` | ccxt OHLCV loader (BloFin/Binance) + parquet cache + Pine-matched indicators. |
| `metrics.py` | `extended_metrics()` (CAGR/Sharpe/Sortino/Calmar/MAR/Ulcer/recovery) + `passes_guardrails()` — the risk-profile gate. |
| `optimizer.py` | `optimize()` — optuna over the engine. Objective maximizes a risk-adjusted target; liquidation is a hard kill; DD breach penalized. Searches IS, reports OOS. |
| `validation.py` | `walk_forward()` (the real overfit test), `monte_carlo()` (luck/sequence risk), `param_stability()` (plateau vs knife-edge). |
| `tests/` | engine honesty tests (13) + metrics/guardrail tests. |
| `smoke_pipeline.py` | end-to-end wiring example on SOL 5m. |

## Risk profile (current default: "high return WITH guardrails")

`metrics.GUARDRAILS` — max_dd 25%, min PF 1.3, min 30 trades, min Sharpe 1.0,
**zero liquidation breaches**. Optimizer and reports read the same thresholds, so
"acceptable" has one definition. Tune per hunt.

## Strategy contract

`fn(df, side='both', **params) -> list[Signal]`. Strategy libraries live in
`../analysis/*/strat_lib.py` (~17 families). A `Signal` decides on bar *i*'s close
and fills at *i+1* — the engine enforces no-lookahead regardless of the strategy.

## Run

```bash
cd scripts/scalping/backtest
../venv/Scripts/python.exe tests/test_engine.py     # 13/13 honesty
../venv/Scripts/python.exe tests/test_metrics.py
../venv/Scripts/python.exe smoke_pipeline.py        # full pipeline on SOL 5m
```

venv deps: numpy, pandas, pyarrow, ccxt, optuna, scipy, matplotlib.
