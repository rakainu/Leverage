"""Read-only diagnostic for Polymarket wallet recovery.
Reports gas at signer EOA, USDC.e cash at proxy, and full positions list."""
import json
import subprocess

PROXY = "0xeE7172d97Bc6ac47A6020826842Ed5ccF8FBee57"
SIGNER = "0xF5714690f35F056EA5b0DF795111C6298704DB06"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
RPC_URL = "https://polygon-bor-rpc.publicnode.com"


def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    out = subprocess.run(
        ["curl", "-sS", "-X", "POST", "-H", "Content-Type: application/json", "-d", payload, RPC_URL],
        capture_output=True, text=True, timeout=20,
    )
    data = json.loads(out.stdout)
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


def main():
    gas_wei = int(rpc("eth_getBalance", [SIGNER, "latest"]), 16)
    gas = gas_wei / 1e18
    usdc_raw = int(rpc("eth_call", [{"to": USDC_E, "data": "0x70a08231000000000000000000000000" + PROXY[2:]}, "latest"]), 16)
    usdc = usdc_raw / 1e6
    print(f"Signer EOA POL gas: {gas:.4f} POL")
    print(f"Proxy USDC.e cash:  ${usdc:.4f}")

    url = f"https://data-api.polymarket.com/positions?user={PROXY}&sizeThreshold=0.01&limit=100"
    out = subprocess.run(
        ["curl", "-sS", "-A", "Mozilla/5.0", url],
        capture_output=True, text=True, timeout=20,
    )
    positions = json.loads(out.stdout)
    print(f"\nPositions: {len(positions)}")
    total_redeem = 0.0
    for p in positions:
        title = (p.get("title") or p.get("slug") or "?")[:60]
        out = p.get("outcome")
        sz = p.get("size")
        rd = bool(p.get("redeemable"))
        val = float(p.get("currentValue") or 0)
        tag = "REDEEM" if rd else "open  "
        if rd:
            total_redeem += val
        print(f"  [{tag}] {title} | {out} | shares={sz} | val=${val:.2f}")
    print(f"\nTotal redeemable value: ${total_redeem:.2f}")
    print(f"Grand total recoverable: ${total_redeem + usdc:.2f}")


if __name__ == "__main__":
    main()
