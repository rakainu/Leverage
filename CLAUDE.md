# Leverage

Crypto leverage trading system combining TradingView/Pine Script signals with execution on BloFin and other DEXes.

## Primary Directive

**Making all trading efforts profitable is the #1 priority.** Be proactive — find opportunities, fixes, edits, and changes that improve profitability across all trading systems. Don't wait to be asked. Analyze performance data, flag losing strategies early, suggest parameter changes, and optimize for profit first.

**No "cheap" fixes.** Never propose band-aid patches, quick hacks, or minimum-viable shortcuts. If something is worth doing, do it properly the first time. Default to the right design, not the smallest diff. Scope/cost tradeoffs only get raised when there's a genuine decision for Rich to make.

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
