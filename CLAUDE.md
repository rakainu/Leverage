# Leverage

Crypto leverage trading system combining TradingView/Pine Script signals with execution on BloFin and other DEXes.

## Stack

- **Signals:** TradingView alerts via Pine Script v6 strategies
- **Execution:** BloFin API, additional DEXes TBD
- **Language:** Python (execution bridge), Pine Script (strategies)

## Project Structure

```
pinescripts/       # TradingView Pine Script v6 strategies & indicators
scripts/           # Python execution bridges & utilities
config/            # Exchange configs, pair lists, risk parameters
docs/              # Strategy notes, research, trade logs
```

## Git

- GitHub: rakainu/Leverage
- Commit meaningful units of work immediately, push after each commit.
- Descriptive commit messages explaining what and why.
