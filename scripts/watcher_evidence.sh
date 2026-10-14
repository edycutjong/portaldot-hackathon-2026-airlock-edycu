#!/usr/bin/env bash
# Watcher evidence driver: record the watcher's five live cases into evidence/ at the current commit (local interim chains).
#   1 live (sdk) · 2 announce while the watcher is down, then restart (sdk) · 3 node stopped + restarted mid-watch (sdk)
#   5 node frozen with SIGSTOP (socket open, no headers) -> watchdog reconnects after SIGCONT (sdk)
#   4 live on interim-1002 (legacy decoding)
# The announces come from scripts/raw_announce.py (plain substrate-interface, no airlock imports). Cases 3 and 5 stop
# ONLY the interim-sdk node listening on port 29944 (checked) and restart it with scripts/interim_node.sh.
# Needs both interim nodes running (scripts/interim_node.sh interim-sdk / interim-1002). ONLY_1002=1 runs case 4 alone.
# usage: scripts/watcher_evidence.sh [pure-proxy index]
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
RAW="$B/scripts/raw_announce.py"; SDK=ws://127.0.0.1:29944; P1002=ws://127.0.0.1:19944
INDEX="${1:-41}"
cd "$B"
CODE="$(git rev-parse --short HEAD)"; TS="$(date -u +%Y-%m-%dT%H%M%SZ)"
E="evidence"; J="$E/watcher-interim-sdk-$TS.jsonl"; RA="$E/raw-announce-interim-sdk-$TS.jsonl"
rm -f .watcher-evidence-sdk.db .watcher-evidence-1002.db
waitfor() {
  local f="$1" pat="$2" n=0
  until grep -q "$pat" "$f" 2>/dev/null; do
    sleep 1; n=$((n+1))
    if [[ $n -gt 180 ]]; then echo "timeout waiting for '$pat' in $f" >&2; cat "$f" >&2; exit 1; fi
  done
  return 0
}
blocks() { python3 -c "
import time,sys
from substrateinterface import SubstrateInterface
s=SubstrateInterface(url='$1'); n=s.get_block_number(None)
while s.get_block_number(None)<n+int(sys.argv[1]): time.sleep(1)" "$2"; }
watch() {
  local rc=0
  python3 -u -m airlock.watcher --db "$1" --jsonl "$2" --max-alerts 1 "${@:4}" >> "$3" 2>&1 || rc=$?
  echo "watcher exit $rc" >> "$3"
}

if [[ "${ONLY_1002:-}" != 1 ]]; then
python3 "$RAW" setup "$SDK" "$INDEX" 20 | tee "$RA"
VAULT="$(python3 -c "import json,sys;print(json.loads(open(sys.argv[1]).readline())['vault'])" "$RA")"

# 1 live
L1="$E/watcher-interim-sdk-$TS.log"; echo "code $CODE" > "$L1"
watch .watcher-evidence-sdk.db "$J" "$L1" --profile interim-sdk --vault "$VAULT" & waitfor "$L1" "watching"
python3 "$RAW" announce "$SDK" "$VAULT" 400 | tee -a "$RA"; waitfor "$L1" "watcher exit"
echo "[driver] watcher stopped; announcing while it is down" | tee -a "$L1"

# 2 down -> announce -> restart
L2="$E/watcher-interim-sdk-$TS-restart.log"; echo "code $CODE" > "$L2"
python3 "$RAW" announce "$SDK" "$VAULT" 250 | tee -a "$RA"; blocks "$SDK" 4
watch .watcher-evidence-sdk.db "$J" "$L2" --profile interim-sdk

# 3 node stopped mid-watch -> restarted -> announce
L3="$E/watcher-interim-sdk-$TS-reconnect.log"; echo "code $CODE" > "$L3"
watch .watcher-evidence-sdk.db "$J" "$L3" --profile interim-sdk & waitfor "$L3" "watching"; sleep 4
NODE="$(lsof -nP -iTCP:29944 -sTCP:LISTEN -t | head -1)"; ps -o command= -p "$NODE" | grep -q "rpc-port 29944" || { echo "port 29944 owner is not our node" >&2; exit 1; }
kill "$NODE"; echo "[driver] node stopped (SIGTERM) at $(date -u +%H:%M:%S)" | tee -a "$L3"; waitfor "$L3" "connection lost"; sleep 5
nohup "$B/scripts/interim_node.sh" interim-sdk >> "$HOME/.cache/airlock-interim/node-sdk.log" 2>&1 &
echo "[driver] node restarted at $(date -u +%H:%M:%S)" | tee -a "$L3"
waitfor "$L3" "reconnected"; sleep 3
python3 "$RAW" announce "$SDK" "$VAULT" 150 | tee -a "$RA"; waitfor "$L3" "watcher exit"


# 5 stall: node frozen (SIGSTOP keeps the socket open but sends nothing) -> watchdog; then SIGCONT -> reconnect
L5="$E/watcher-interim-sdk-$TS-stall.log"; echo "code $CODE" > "$L5"
watch .watcher-evidence-sdk.db "$J" "$L5" --profile interim-sdk & waitfor "$L5" "watching"; sleep 4
NODE="$(lsof -nP -iTCP:29944 -sTCP:LISTEN -t | head -1)"; ps -o command= -p "$NODE" | grep -q "rpc-port 29944" || { echo "port 29944 owner is not our node" >&2; exit 1; }
kill -STOP "$NODE"; echo "[driver] node frozen (SIGSTOP) at $(date -u +%H:%M:%S)" | tee -a "$L5"; waitfor "$L5" "no new header"; sleep 3
kill -CONT "$NODE"; echo "[driver] node resumed (SIGCONT) at $(date -u +%H:%M:%S)" | tee -a "$L5"
waitfor "$L5" "reconnected"; sleep 3
python3 "$RAW" announce "$SDK" "$VAULT" 120 | tee -a "$RA"; waitfor "$L5" "watcher exit"
fi

# 4 interim-1002, legacy decoding
TS2="$(date -u +%Y-%m-%dT%H%M%SZ)"; RB="$E/raw-announce-interim-1002-$TS2.jsonl"; L4="$E/watcher-interim-1002-$TS2.log"
echo "code $CODE" > "$L4"
COLD=//Dave python3 "$RAW" setup "$P1002" "$INDEX" 10 | tee "$RB"
V2="$(python3 -c "import json,sys;print(json.loads(open(sys.argv[1]).readline())['vault'])" "$RB")"
watch .watcher-evidence-1002.db "$E/watcher-interim-1002-$TS2.jsonl" "$L4" --profile interim-1002 --vault "$V2" & waitfor "$L4" "watching"
python3 "$RAW" announce "$P1002" "$V2" 100 | tee -a "$RB"; waitfor "$L4" "watcher exit"

# announces re-fetched by extrinsic id over HTTP from a fresh process, compared with what raw_announce recorded
[[ "${ONLY_1002:-}" != 1 ]] && python3 scripts/refetch.py "$RA" --profile interim-sdk --url http://127.0.0.1:29944 | tail -2
python3 scripts/refetch.py "$RB" --profile interim-1002 --url http://127.0.0.1:19933 | tail -2
rm -f .watcher-evidence-sdk.db .watcher-evidence-1002.db
echo "DONE code $CODE ts $TS / $TS2"
