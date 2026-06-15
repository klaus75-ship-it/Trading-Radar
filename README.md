# TradingRading MT5 Radar

Mac-first MT5 tradeability radar for `XAUUSD` and `NDX100`.

The design assumes MT5 is running through Wine/CrossOver on the same Mac. The preferred bridge is the read-only `FILE_COMMON` bridge: MT5 writes one JSON state file and Python scans it locally.

This first version is read-only. It does not place trades.

## Architecture

```text
Python on macOS
  - file-state reader with retry/backoff
  - risk/cost filters
  - market-structure classifier
  - prop challenge risk gate
  - position sizing
  - SQLite audit log
  - Telegram alerts

WolveRadarFileEA.mq5 inside MT5
  - broker symbol snapshot
  - recent bars
  - account info
  - margin estimate
  - no trading
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

Use the read-only file bridge for normal operation.

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

The Python side reads `wolve_radar_state.json` with short retry/backoff so a half-written JSON file does not stop the radar. It also ignores the still-forming M15 candle when calculating ATR and market structure.

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

Alerts are deduplicated by symbol, decision, risk bucket, spread bucket, market structure, and prop challenge risk state.

Telegram failures are logged and do not stop scanning.

Note: the development repo keeps Telegram disabled in `config_file.json` by default. The deployed runtime copy at `/Users/klaus/trading-radar-runtime/config_file.json` may intentionally enable Telegram and read credentials from `/Users/klaus/trading-radar-runtime/.env`.

## Run Continuously On macOS

Create `.env` from the example if Telegram is enabled:

```bash
cp .env.example .env
```

Then edit `.env` with your Telegram bot token and chat ID.

Run the radar manually:

```bash
scripts/run_radar.sh
```

Check recent scans:

```bash
scripts/check_status.sh
```

Print a chart-structure report for visual calibration against MT5:

```bash
scripts/structure_report.py --config config_file.json
```

Print a concise daily/market-session brief:

```bash
scripts/daily_brief.py --config config_file.json
```

Send that brief to Telegram:

```bash
scripts/daily_brief.py --config config_file.json --send-telegram
```

## Legacy Socket Bridge

The socket bridge is still available for development, but the file bridge is the preferred production path on macOS/Wine.

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

The audit log stores tradeability, account equity/margin, market structure, prop challenge status, and a config snapshot so later reviews can answer why a signal did or did not fire.

## Configure Symbols

For the file bridge, edit `config_file.json`.

Broker symbol names differ. If your broker uses `NAS100`, `USTEC`, `US100`, or `NDX100.cash`, change the config symbol key from `NDX100` to the exact MT5 Market Watch name.

`max_tick_age_seconds` is intentionally short. On weekends or after market close, the EA may keep writing a fresh state file while the last broker tick is old; those scans are marked stale and should be treated as structure review, not live entry signals.

## Prop Challenge Mode

`config_file.json` includes an optional `prop_challenge` block for the XAUUSD/MGC case:

```json
"prop_challenge": {
  "enabled": true,
  "symbols": ["XAUUSD"],
  "contract": "MGC",
  "dollars_per_point": 10,
  "standard_contracts": 1,
  "aggressive_contracts": 2,
  "max_standard_risk": 150,
  "max_aggressive_risk": 220
}
```

The Telegram message will classify each XAUUSD scan as `PF 加速模式允許`, `PF 標準模式可觀察`, or `PF 暫不允許`.

## TradeSea Futures Bridge

For Lucid/TradeSea futures charts, keep Safari logged in at:

```text
https://app.tradesea.ai/trade
```

Then fetch MGC/MNQ bars from TradeSea's in-page datafeed:

```bash
cd /Users/klaus/Documents/workspace/tradingRading
scripts/fetch_tradesea_state.py --symbols MGC,MNQ --resolution 15
python3 -m trading_radar.app_file --config config_tradesea.json --run-once
scripts/structure_report.py --config config_tradesea.json
```

This writes `tradesea_futures_state.json` and lets the existing radar read native futures bars such as `COMEX-Delayed:MGC` and `CME-Delayed:MNQ`. The current demo/delayed TradeSea chart is useful for validating the data path and structure logic, but final entry/exit decisions should be based on the live Lucid account data once available.

## Current Scope

- No direction prediction
- No auto-order execution
- No ML
- No news blackout yet

The first goal is simpler: measure whether the current trading condition is acceptable for a small account.
