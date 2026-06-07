# TradingRading MT5 Radar

Mac-first MT5 tradeability radar for `XAUUSD` and `NDX100`.

The design assumes MT5 is running through Wine/CrossOver on the same Mac. Python runs locally and listens on `127.0.0.1:9001`; an MQL5 Expert Advisor connects back to Python and acts as the broker bridge.

This first version is read-only. It does not place trades.

## Architecture

```text
Python on macOS
  - TCP JSONL server
  - risk/cost filters
  - position sizing
  - SQLite audit log

MT5BridgeEA.mq5 inside MT5
  - broker symbol snapshot
  - recent bars
  - account info
  - order check / margin check
```

## Quick Start Without MT5

Run a smoke test with the mock bridge:

```bash
cd /Users/klaus/Documents/workspace/tradingRading
python3 scripts/smoke_test.py
```

This starts the Python server, connects a fake MT5 bridge, scans `XAUUSD` and `NDX100`, and writes `radar.sqlite3`.

## Read From Existing WolveBridgeEA

Your existing `WolveBridgeEA.mq5` writes files under MT5 `FILE_COMMON`, for example:

```text
/Users/klaus/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/wolve_mt5_prices.json
```

Run the file-based adapter:

```bash
cd /Users/klaus/Documents/workspace/tradingRading
python3 -m trading_radar.app_wolve --run-once
```

Important: `WolveBridgeEA` currently writes the symbol of the chart it is attached to. To read both `XAUUSD` and `NDX100`, attach two EA instances and give the second one unique output file names such as:

```text
InpPricesFile = wolve_mt5_prices_ndx100.json
InpStatsFile = wolve_mt5_stats_ndx100.json
InpHistOutFile = wolve_mt5_history_ndx100.csv
```

Then enable `NDX100` in `config_wolve.json`.

## Stable File Bridge

If the socket bridge is noisy or unstable under Wine, use the read-only file bridge instead.

1. Compile and attach `MQL5/Experts/Advisors/WolveRadarFileEA.mq5` to one chart.
2. Use these defaults:

```text
InpSymbols = XAUUSD,NDX100
InpOutputFile = wolve_radar_state.json
InpTimeframe = M15
InpBars = 200
InpTimerSeconds = 5
InpVerbose = false
```

3. Run:

```bash
cd /Users/klaus/Documents/workspace/tradingRading
python3 -m trading_radar.app_file --run-once
```

This mode does not use sockets, does not read signal files, and does not trade.

## Telegram Alerts

Telegram is off by default. Enable it in `config_file.json`:

```json
"telegram": {
  "enabled": true,
  "bot_token_env": "TELEGRAM_BOT_TOKEN",
  "chat_id_env": "TELEGRAM_CHAT_ID",
  "state_path": "telegram_state.json",
  "min_repeat_seconds": 3600
}
```

Then run with environment variables:

```bash
export TELEGRAM_BOT_TOKEN="123456:abc..."
export TELEGRAM_CHAT_ID="123456789"
python3 -m trading_radar.app_file --run-once
```

Alerts are deduplicated by symbol, decision, risk bucket, spread bucket, and reasons.

## Run With MT5

1. Open `MQL5/Experts/Advisors/WolveRadarBridgeEA.mq5` in MetaEditor.
2. Compile it in MetaEditor.
3. Attach it to one chart only. One bridge EA can serve both `XAUUSD` and `NDX100`.
4. Keep these EA inputs:

```text
InpHost = 127.0.0.1
InpPort = 9001
InpTimerSeconds = 1
InpVerbose = false
```

5. In MT5, open `Tools > Options > Expert Advisors`, enable socket/web requests for:

```text
127.0.0.1
localhost
```

6. Start the Python app:

```bash
cd /Users/klaus/Documents/workspace/tradingRading
python3 -m trading_radar.app
```

The EA file has also been written to:

```text
/Users/klaus/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Advisors/WolveRadarBridgeEA.mq5
```

## Output

The app prints one line per symbol:

```text
XAUUSD OBSERVE score=100 spread/ATR=4.20% volume=0.03 reasons=[]
NDX100 REJECT score=20 spread/ATR=22.00% volume=0 reasons=['spread/ATR too high: 22.00%']
```

Every scan is also saved to SQLite in `radar.sqlite3`.

## Configure Symbols

Edit `config.json`.

Broker symbol names differ. If your broker uses `NAS100`, `USTEC`, `US100`, or `NDX100.cash`, change the config symbol key from `NDX100` to the exact MT5 Market Watch name.

## Current Scope

- No direction prediction
- No auto-order execution
- No ML
- No Telegram yet
- No news blackout yet

The first goal is simpler: measure whether the current trading condition is acceptable for a small account.
