from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


DEFAULT_SYMBOLS = {
    "MGC": {
        "ticker": "COMEX-Delayed:MGC",
        "exchange": "COMEX",
        "dollars_per_point": 10.0,
        "min_tick": 0.1,
        "precision": 1,
        "volume_max": 40,
    },
    "MNQ": {
        "ticker": "CME-Delayed:MNQ",
        "exchange": "CME",
        "dollars_per_point": 2.0,
        "min_tick": 0.25,
        "precision": 2,
        "volume_max": 40,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="tradesea_futures_state.json")
    parser.add_argument("--resolution", default="15")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--count-back", type=int, default=300)
    parser.add_argument("--symbols", default="MGC,MNQ")
    parser.add_argument("--account-size", type=float, default=50000.0)
    args = parser.parse_args()

    requested = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    specs = {symbol: DEFAULT_SYMBOLS[symbol] for symbol in requested}
    raw = _fetch_from_safari(specs, args.resolution, args.days, args.count_back)
    state = _to_state(raw, specs, args.account_size, args.resolution)

    output = Path(args.output)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(output)
    print(f"wrote {output} with {len(state['symbols'])} symbols")


def _fetch_from_safari(
    specs: dict[str, dict[str, Any]],
    resolution: str,
    days: int,
    count_back: int,
) -> dict[str, Any]:
    request_id = f"codexTradeSea{int(time.time() * 1000)}"
    payload = {
        "requestId": request_id,
        "symbols": {symbol: spec["ticker"] for symbol, spec in specs.items()},
        "resolution": resolution,
        "days": days,
        "countBack": count_back,
    }
    start_js = _build_start_js(payload)
    _run_safari_js(start_js)

    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(0.5)
        result_text = _run_safari_js(f"JSON.stringify(window.{request_id} || null)")
        result = json.loads(result_text)
        if result and result.get("status") in {"ok", "error"}:
            if result.get("status") == "error":
                raise RuntimeError(json.dumps(result, ensure_ascii=False))
            return result
    raise TimeoutError("Timed out waiting for TradeSea datafeed response")


def _build_start_js(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"""
(() => {{
  const cfg = {encoded};
  const outKey = cfg.requestId;
  window[outKey] = {{status: 'pending', results: {{}}}};
  const df = window.__hybridDatafeed;
  if (!df) {{
    window[outKey] = {{status: 'error', error: 'window.__hybridDatafeed is unavailable'}};
    return 'STARTED';
  }}
  const now = Math.floor(Date.now() / 1000);
  const from = now - cfg.days * 86400;
  const entries = Object.entries(cfg.symbols);
  let remaining = entries.length;
  const done = () => {{
    remaining -= 1;
    if (remaining === 0) window[outKey].status = 'ok';
  }};
  for (const [symbol, ticker] of entries) {{
    df.resolveSymbol(
      ticker,
      (symbolInfo) => {{
        try {{
          df.getBars(
            symbolInfo,
            cfg.resolution,
            {{from, to: now, countBack: cfg.countBack, firstDataRequest: true}},
            (bars, meta) => {{
              window[outKey].results[symbol] = {{
                ok: true,
                ticker,
                meta,
                symbolInfo: {{
                  name: symbolInfo.name,
                  ticker: symbolInfo.ticker,
                  description: symbolInfo.description,
                  exchange: symbolInfo.exchange,
                  pricescale: symbolInfo.pricescale,
                  minmov: symbolInfo.minmov
                }},
                bars
              }};
              done();
            }},
            (error) => {{
              window[outKey].results[symbol] = {{ok: false, ticker, error: String(error)}};
              done();
            }}
          );
        }} catch (error) {{
          window[outKey].results[symbol] = {{ok: false, ticker, error: String(error)}};
          done();
        }}
      }},
      (error) => {{
        window[outKey].results[symbol] = {{ok: false, ticker, error: String(error)}};
        done();
      }}
    );
  }}
  return 'STARTED';
}})()
"""


def _run_safari_js(js_code: str) -> str:
    script = f"""
tell application "Safari"
  repeat with wi from 1 to (count of windows)
    repeat with ti from 1 to (count of tabs of window wi)
      set tabUrl to (URL of tab ti of window wi) as string
      if tabUrl contains "app.tradesea.ai/trade" then
        set jsCode to {json.dumps(js_code)}
        tell tab ti of window wi
          return do JavaScript jsCode
        end tell
      end if
    end repeat
  end repeat
  return "NO_TRADESEA_TAB"
end tell
"""
    completed = subprocess.run(["osascript"], input=script, text=True, capture_output=True, check=True)
    output = completed.stdout.strip()
    if output == "NO_TRADESEA_TAB":
        raise RuntimeError("TradeSea tab is not open in Safari")
    return output


def _to_state(
    raw: dict[str, Any],
    specs: dict[str, dict[str, Any]],
    account_size: float,
    resolution: str,
) -> dict[str, Any]:
    now = int(time.time())
    symbols = []
    for symbol, spec in specs.items():
        item = raw["results"].get(symbol, {})
        if not item.get("ok"):
            symbols.append({"symbol": symbol, "ok": False, "error": item.get("error", "unknown")})
            continue
        bars = [_bar_from_tradesea(bar) for bar in item.get("bars", [])]
        last = bars[-1] if bars else None
        min_tick = float(spec["min_tick"])
        dollars_per_point = float(spec["dollars_per_point"])
        symbols.append(
            {
                "symbol": symbol,
                "source": "tradesea_safari",
                "ticker": spec["ticker"],
                "exchange": spec["exchange"],
                "resolution": resolution,
                "ok": bool(last),
                "bid": last["close"] if last else 0.0,
                "ask": last["close"] if last else 0.0,
                "last": last["close"] if last else 0.0,
                "spread": 0.0,
                "tick_time": int(last["time"]) if last else 0,
                "point": min_tick,
                "digits": int(spec["precision"]),
                "volume_min": 1.0,
                "volume_step": 1.0,
                "volume_max": float(spec["volume_max"]),
                "tick_value": dollars_per_point * min_tick,
                "tick_size": min_tick,
                "contract_size": 1.0,
                "trade_stops_level": 0,
                "trade_freeze_level": 0,
                "swap_long": 0.0,
                "swap_short": 0.0,
                "trade_mode": 4,
                "margin_buy_min": 0.0,
                "bars": bars,
            }
        )
    return {
        "schema": "wolve.radar.tradesea.v1",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "server_time": now,
        "account": {
            "login": 0,
            "trade_mode": 0,
            "balance": account_size,
            "equity": account_size,
            "margin": 0.0,
            "margin_free": account_size,
            "margin_level": 0.0,
            "currency": "USD",
        },
        "positions": [],
        "symbols": symbols,
    }


def _bar_from_tradesea(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": int(int(payload["time"]) / 1000),
        "open": float(payload["open"]),
        "high": float(payload["high"]),
        "low": float(payload["low"]),
        "close": float(payload["close"]),
        "tick_volume": float(payload.get("volume", 0.0) or 0.0),
        "spread": 0.0,
    }


if __name__ == "__main__":
    main()
