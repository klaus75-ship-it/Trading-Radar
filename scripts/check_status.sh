#!/usr/bin/env bash
set -euo pipefail

cd /Users/klaus/Documents/workspace/tradingRading

echo "Latest scans:"
sqlite3 radar.sqlite3 '
select id, timestamp, symbol, decision, score,
       round(spread_to_atr * 100, 2) as spread_atr_pct,
       round(min_volume_risk_fraction * 100, 2) as min_lot_risk_pct,
       margin_required,
       reasons_json
from scans
order by id desc
limit 10;
'

echo
echo "Bridge state file:"
stat -f "%Sm %N" "/Users/klaus/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/wolve_radar_state.json"

