import sys, numpy as np, pandas as pd
sys.path.insert(0, "/c/Users/rakai/Leverage/scripts/z-fade-bridge/src".replace("/c/","C:/"))
# import check
from lighter_bridge.config import ZFadeConfig, load_config
from lighter_bridge.signals import prepare as bprepare, evaluate_entry
print("IMPORT OK: bridge modules load")

from engine import load_symbol, calc_ema, calc_atr
from strat_vwaprsi import rsi as bt_rsi
from strat_bbmr import adx as bt_adx

cfg = ZFadeConfig()  # defaults match config.yaml
df = load_symbol("SOL","5m",days_back=180)

# --- Bridge side: prepare + evaluate_entry per bar ---
enr = bprepare(df, cfg)
bridge_sig = []
for i in range(len(enr)):
    bridge_sig.append(evaluate_entry(enr.iloc[i], cfg))

# --- Backtest side: independent recompute with sweep engine funcs ---
c = df["Close"]
z = ((c - c.rolling(cfg.window).mean())/c.rolling(cfg.window).std(ddof=1)).values
rsiv = bt_rsi(c, cfg.rsi_len)
bbw = ((2*cfg.bb_mult*c.rolling(cfg.bb_len).std(ddof=0))/c.rolling(cfg.bb_len).mean()).values
adxv = bt_adx(df, cfg.adx_len)
atr = calc_atr(df, cfg.atr_len).values
clv = c.values
bt_sig=[]
for i in range(len(df)):
    if any(np.isnan(x) for x in (z[i],rsiv[i],bbw[i],adxv[i],atr[i])):
        bt_sig.append(None); continue
    bb_ok = bbw[i] > cfg.bb_width_min
    regime_ok = adxv[i] <= cfg.adx_max
    s=None
    if bb_ok and regime_ok:
        if z[i] < -cfg.z_thresh and rsiv[i] < cfg.rsi_os: s="long"
        elif z[i] > cfg.z_thresh and rsiv[i] > cfg.rsi_ob: s="short"
    bt_sig.append(s)

disagree = sum(1 for a,b in zip(bridge_sig, bt_sig) if a!=b)
bl = sum(1 for x in bridge_sig if x=="long"); bs=sum(1 for x in bridge_sig if x=="short")
tl = sum(1 for x in bt_sig if x=="long"); ts=sum(1 for x in bt_sig if x=="short")
print(f"Bridge signals: long={bl} short={bs}")
print(f"Backtest gate : long={tl} short={ts}")
print(f"Per-bar disagreements: {disagree} / {len(df)}")
print("PARITY OK" if disagree==0 else "PARITY MISMATCH")
