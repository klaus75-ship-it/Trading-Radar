#!/usr/bin/env bash
set -euo pipefail

cd "$(cd "$(dirname "$0")/.." && pwd)"

sqlite3 radar.sqlite3 '
create table if not exists health_events (
  id integer primary key autoincrement,
  timestamp text not null,
  component text not null,
  status text not null,
  message text not null,
  details_json text
);
'

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
echo "Latest health events:"
sqlite3 radar.sqlite3 '
select id, timestamp, component, status, message
from health_events
order by id desc
limit 10;
'

echo
echo "Bridge state file:"
stat -f "%Sm %N" "/Users/klaus/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/wolve_radar_state.json"
