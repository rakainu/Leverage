.mode column
.headers on
.print
.print === outcome by wallet_count ===
SELECT cs.wallet_count AS wc, COUNT(*) AS n,
       SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
       ROUND(AVG(p.pnl_pct),2) AS avg_pnl,
       ROUND(SUM(p.pnl_sol),3) AS pnl_sol
FROM convergence_signals cs JOIN positions p ON p.id=cs.position_id
WHERE p.mode='paper' AND p.status='closed'
GROUP BY cs.wallet_count ORDER BY cs.wallet_count;

.print
.print === outcome by convergence speed (signal_at - first_buy_at) ===
SELECT
  CASE
    WHEN (julianday(cs.signal_at)-julianday(cs.first_buy_at))*1440<1 THEN 'a_lt1m'
    WHEN (julianday(cs.signal_at)-julianday(cs.first_buy_at))*1440<5 THEN 'b_1to5m'
    WHEN (julianday(cs.signal_at)-julianday(cs.first_buy_at))*1440<15 THEN 'c_5to15m'
    ELSE 'd_15mplus'
  END AS bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
  ROUND(AVG(p.pnl_pct),2) AS avg_pnl,
  ROUND(SUM(p.pnl_sol),3) AS pnl_sol
FROM convergence_signals cs JOIN positions p ON p.id=cs.position_id
WHERE p.mode='paper' AND p.status='closed'
GROUP BY bucket ORDER BY bucket;

.print
.print === outcome by hour UTC ===
SELECT strftime('%H',cs.signal_at) AS h, COUNT(*) AS n,
       SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
       ROUND(AVG(p.pnl_pct),2) AS avg_pnl,
       ROUND(SUM(p.pnl_sol),3) AS pnl_sol
FROM convergence_signals cs JOIN positions p ON p.id=cs.position_id
WHERE p.mode='paper' AND p.status='closed'
GROUP BY h ORDER BY h;

.print
.print === outcome by total_amount_sol of converging buys ===
SELECT
  CASE
    WHEN cs.total_amount_sol < 5 THEN 'a_lt5sol'
    WHEN cs.total_amount_sol < 20 THEN 'b_5to20sol'
    WHEN cs.total_amount_sol < 50 THEN 'c_20to50sol'
    ELSE 'd_50plus'
  END AS bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
  ROUND(AVG(p.pnl_pct),2) AS avg_pnl,
  ROUND(SUM(p.pnl_sol),3) AS pnl_sol
FROM convergence_signals cs JOIN positions p ON p.id=cs.position_id
WHERE p.mode='paper' AND p.status='closed'
GROUP BY bucket ORDER BY bucket;

.print
.print === outcome by avg_amount_sol per converging wallet ===
SELECT
  CASE
    WHEN cs.avg_amount_sol < 0.5 THEN 'a_lt0.5sol'
    WHEN cs.avg_amount_sol < 2 THEN 'b_0.5to2sol'
    WHEN cs.avg_amount_sol < 10 THEN 'c_2to10sol'
    ELSE 'd_10plus'
  END AS bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
  ROUND(AVG(p.pnl_pct),2) AS avg_pnl,
  ROUND(SUM(p.pnl_sol),3) AS pnl_sol
FROM convergence_signals cs JOIN positions p ON p.id=cs.position_id
WHERE p.mode='paper' AND p.status='closed'
GROUP BY bucket ORDER BY bucket;

.print
.print === stop_loss avg pnl_pct (slippage check) ===
SELECT close_reason, COUNT(*) AS n,
       ROUND(MIN(pnl_pct),2) AS worst,
       ROUND(MAX(pnl_pct),2) AS best,
       ROUND(AVG(pnl_pct),2) AS avg,
       ROUND(AVG(high_watermark_pct),2) AS avg_hwm
FROM positions
WHERE mode='paper' AND status='closed'
GROUP BY close_reason ORDER BY n DESC;

.print
.print === HWM distribution for stop-outs (did trade ever go green?) ===
SELECT
  CASE
    WHEN high_watermark_pct < 0 THEN 'a_never_green'
    WHEN high_watermark_pct < 5 THEN 'b_lt5pct'
    WHEN high_watermark_pct < 15 THEN 'c_5to15pct'
    WHEN high_watermark_pct < 30 THEN 'd_15to30pct'
    ELSE 'e_30plus'
  END AS hwm_bucket,
  COUNT(*) AS n,
  ROUND(AVG(pnl_pct),2) AS avg_close_pnl
FROM positions
WHERE mode='paper' AND status='closed' AND close_reason='stop_loss'
GROUP BY hwm_bucket ORDER BY hwm_bucket;

.print
.print === outcome by wallet source mix (multi-row per position) ===
SELECT tw.source, COUNT(*) AS appearances,
       SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS in_winners,
       ROUND(AVG(p.pnl_pct),2) AS avg_pnl_when_present
FROM convergence_signals cs
JOIN positions p ON p.id=cs.position_id
JOIN buy_events be ON be.token_mint=p.token_mint
                  AND be.timestamp <= cs.signal_at
                  AND be.timestamp >= cs.first_buy_at
JOIN tracked_wallets tw ON tw.address = be.wallet_address
WHERE p.mode='paper' AND p.status='closed'
GROUP BY tw.source ORDER BY appearances DESC;

.print
.print === top 20 wallets touching closed positions ===
SELECT be.wallet_address AS wallet, tw.source, COALESCE(tw.label,'') AS label,
       COUNT(DISTINCT p.id) AS positions_touched,
       SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS in_winners,
       ROUND(AVG(p.pnl_pct),2) AS avg_pnl_when_present,
       ROUND(SUM(DISTINCT p.pnl_sol),3) AS sum_distinct_pnl
FROM convergence_signals cs
JOIN positions p ON p.id=cs.position_id
JOIN buy_events be ON be.token_mint=p.token_mint
                  AND be.timestamp <= cs.signal_at
                  AND be.timestamp >= cs.first_buy_at
JOIN tracked_wallets tw ON tw.address = be.wallet_address
WHERE p.mode='paper' AND p.status='closed'
GROUP BY be.wallet_address
ORDER BY positions_touched DESC
LIMIT 20;
