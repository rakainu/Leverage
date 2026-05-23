"""Extract per-position condition IDs and neg-risk flags for the locked proxy.
Used to pick the right redeem command (ctf redeem vs ctf redeem-neg-risk)."""
import json
import subprocess

PROXY = "0xeE7172d97Bc6ac47A6020826842Ed5ccF8FBee57"


def curl_json(url):
    out = subprocess.run(
        ["curl", "-sS", "-A", "Mozilla/5.0", url],
        capture_output=True, text=True, timeout=20,
    )
    return json.loads(out.stdout)


def main():
    positions = curl_json(
        f"https://data-api.polymarket.com/positions?user={PROXY}&sizeThreshold=0.01&limit=100"
    )
    print(f"{'#':>2}  {'value':>8}  {'neg_risk':>8}  {'shares':>10}  outcome  condition_id  title")
    print("-" * 120)
    for i, p in enumerate(positions):
        title = (p.get("title") or p.get("slug") or "?")[:50]
        cond = p.get("conditionId") or p.get("condition_id") or "?"
        neg = p.get("negativeRisk") if "negativeRisk" in p else p.get("negRisk")
        out = p.get("outcome")
        sz = p.get("size")
        val = float(p.get("currentValue") or 0)
        print(f"{i:>2}  ${val:>7.2f}  {str(neg):>8}  {sz:>10}  {out}  {cond}  {title}")


if __name__ == "__main__":
    main()
