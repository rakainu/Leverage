"""Flip the live cooldown from (2 losses, 360m) to (3 losses, 180m)."""
P = "/docker/scalper-paper/config.scalper.yaml"
s = open(P, encoding="utf-8").read()

edits = [
    ("# Backtest cooldown(2,360m) on the validated 5-coin set: pooled PF 1.49->1.67,\n"
     "# OOS 1.42->1.66, walk-forward floor 1.42->1.52, max-DD -61%, ~13% fewer trades,",
     "# Backtest cooldown(3,180m) on the validated 5-coin set: pooled PF 1.49->1.58,\n"
     "# OOS 1.42->1.47, walk-forward floor 1.42->1.44, max-DD -52%, ~4% fewer trades,"),
    ("  consec_losses: 2", "  consec_losses: 3"),
    ("  minutes: 360", "  minutes: 180"),
]
for a, b in edits:
    assert a in s, f"anchor not found: {a[:40]}"
    assert s.count(a) == 1, f"anchor not unique: {a[:40]}"
    s = s.replace(a, b)

open(P, "w", encoding="utf-8").write(s)

import yaml
cd = yaml.safe_load(s)["cooldown"]
assert cd == {"enabled": True, "consec_losses": 3, "minutes": 180}, cd
print("OK cooldown ->", cd)
