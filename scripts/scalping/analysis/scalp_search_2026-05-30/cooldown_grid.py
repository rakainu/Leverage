"""Focused cooldown grid (reuses news_rip_sweep engine). Center on (3 losses, 180m)."""
import common as K
import news_rip_sweep as N

coins = K.COINS
weeks = K.weeks_of(K.load(coins[0], "15m"), "15m")
print(f"DATA {coins} 15m ~{weeks:.1f}wk  fixed-notional ${N.NREF}@{N.LREF}x   (cd = consec_losses, minutes)")
print(f"{'config':26}{'n':>6}{'t/wk':>7}{'PF':>6}{'WR':>5}{'exp%':>8}"
      f"{'oPF':>7}{'oWR':>5}{'wfPF':>7}{'hsPF':>8}{'strk':>7}{'net$':>8}{'DD$':>8}{'liq':>5}")

configs = [
    ("baseline (no cd)", dict(N.BASE), None),
    ("cd(2,360m) [LIVE now]", dict(N.BASE), (2, 360)),
    ("cd(3,180m) [you asked]", dict(N.BASE), (3, 180)),
    ("cd(3,90m)", dict(N.BASE), (3, 90)),
    ("cd(3,120m)", dict(N.BASE), (3, 120)),
    ("cd(3,240m)", dict(N.BASE), (3, 240)),
    ("cd(3,360m)", dict(N.BASE), (3, 360)),
    ("cd(4,120m)", dict(N.BASE), (4, 120)),
    ("cd(4,180m)", dict(N.BASE), (4, 180)),
    ("cd(2,120m)", dict(N.BASE), (2, 120)),
    ("cd(2,180m)", dict(N.BASE), (2, 180)),
    ("cd(5,180m)", dict(N.BASE), (5, 180)),
]
for label, params, cd in configs:
    N.run_config(label, params, cd, coins, weeks)
