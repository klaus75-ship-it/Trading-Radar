from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "trading_radar.app", "--config", "config.json", "--run-once"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Give the Python server a moment to bind before the mock MT5 bridge connects.
    time.sleep(0.5)
    thread = threading.Thread(target=mock_bridge, daemon=True)
    thread.start()

    stdout, stderr = process.communicate(timeout=20)
    print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)
    raise SystemExit(process.returncode)


def mock_bridge() -> None:
    # The real EA initiates the connection, so the mock does the same.
    deadline = time.time() + 10
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    while True:
        try:
            sock.connect(("127.0.0.1", 9001))
            break
        except OSError:
            if time.time() > deadline:
                return
            time.sleep(0.05)

    fileobj = sock.makefile("r", encoding="utf-8", newline="\n")
    for line in fileobj:
        request = json.loads(line)
        response = handle_request(request)
        sock.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


def handle_request(request: dict) -> dict:
    request_type = request["type"]
    base = {"id": request["id"], "ok": True, "type": request_type}
    if request_type == "account":
        return {**base, "balance": 5000, "equity": 5000, "margin_free": 4800, "currency": "USD"}
    if request_type == "snapshot":
        return {**base, **snapshot(request["symbol"])}
    if request_type == "bars":
        return {**base, "bars": bars(request["symbol"], int(request["count"]))}
    if request_type == "order_check":
        return {**base, "check_ok": True, "retcode": 0, "margin": 42.5, "comment": "mock ok"}
    return {"id": request["id"], "ok": False, "error": f"unknown request type: {request_type}"}


def snapshot(symbol: str) -> dict:
    now = time.time()
    if symbol == "XAUUSD":
        bid, ask, point = 2350.10, 2350.25, 0.01
        return common_snapshot(symbol, bid, ask, point, 0.01, 0.01, 100, now)
    bid, ask, point = 19000.0, 19003.0, 0.1
    return common_snapshot(symbol, bid, ask, point, 0.1, 0.01, 10, now)


def common_snapshot(symbol, bid, ask, point, tick_size, volume_min, volume_max, tick_time):
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "spread": ask - bid,
        "tick_time": tick_time,
        "point": point,
        "digits": 2,
        "volume_min": volume_min,
        "volume_step": volume_min,
        "volume_max": volume_max,
        "tick_value": 1.0,
        "tick_size": tick_size,
        "contract_size": 100,
        "trade_stops_level": 50,
        "trade_freeze_level": 0,
        "swap_long": -10,
        "swap_short": 5,
        "trade_mode": 4,
    }


def bars(symbol: str, count: int) -> list[dict]:
    price = 2350.0 if symbol == "XAUUSD" else 19000.0
    step = 0.8 if symbol == "XAUUSD" else 20.0
    output = []
    start = int(time.time()) - count * 900
    for index in range(count):
        open_price = price + index * step * 0.05
        high = open_price + step
        low = open_price - step
        close = open_price + step * 0.2
        output.append(
            {
                "time": start + index * 900,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": 100,
                "spread": 0,
            }
        )
    return output


if __name__ == "__main__":
    main()
