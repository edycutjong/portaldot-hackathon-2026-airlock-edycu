#!/usr/bin/env bash
# Live evidence for the owner intent registry on the seeded demo vault (run scripts/seed.py first), at the current
# commit. Real processes on a local interim chain: the registry API (python -m airlock.api) and the watcher share one
# SQLite file.
#   1. airlock enroll (cold key signs the owner token's digest; the registry checks it is the vault's delay-0 delegate)
#   2. owner: airlock announce -> intent registered first, then proxy.announce -> watcher alert YOURS (decoded)
#   3. thief: scripts/thief.py --post-intent (preimage, no token) -> HTTP 401; its announce -> watcher alert NOT YOURS;
#      the cold key vetoes it
#   4. registry stalled mid-request (SIGSTOP): airlock announce -> exit 2 after the timeout, nothing announced
#   5. registry killed (SIGKILL): airlock announce -> exit 2, nothing announced
#   6. no new alert for 4./5. (the owner's spend never alerted red), owner's spend executed after the delay
# Writes evidence/intents-<profile>-<time>.{log,jsonl} (+ thief JSON lines), then re-fetches the extrinsics by id over
# HTTP from a fresh process. Dev keys only (//AirlockDemo//…). usage: scripts/intent_evidence.sh [interim-sdk|interim-1002]
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"; cd "$B"
P="${1:-interim-sdk}"
case "$P" in
  interim-sdk)  WS=ws://127.0.0.1:29944; HTTP=http://127.0.0.1:29944 ;;
  interim-1002) WS=ws://127.0.0.1:19944; HTTP=http://127.0.0.1:19933 ;;
  *) echo "unknown profile $P" >&2; exit 2 ;;
esac
STATE="$(ls -t "${AIRLOCK_STATE_DIR:-$HOME/.local/share/airlock}"/seed-"$P"-*.json 2>/dev/null | head -1 || true)"
[[ -n "$STATE" ]] || { echo "no seed state for $P: run scripts/seed.py --profile $P first" >&2; exit 2; }
j() { python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(eval(sys.argv[2]))" "$@"; }
VAULT="$(j "$STATE" "d['vault']")"; DELAY="$(j "$STATE" "d['delay']")"
acct() { j fixtures/accounts.json "d['accounts']['$1']['address_dev_root']"; }
PAYEE="$(acct payee-rent)"; THIEF_TO="$(acct attacker-2)"
PORT="${AIRLOCK_EVIDENCE_PORT:-18787}"; API="http://127.0.0.1:$PORT"
CODE="$(git rev-parse --short HEAD)"; TS="$(date -u +%Y-%m-%dT%H%M%SZ)"
[[ -z "$(git status --porcelain -- airlock scripts)" ]] || CODE="$CODE-dirty"
LOG="evidence/intents-$P-$TS.log"; J="evidence/intents-$P-$TS.jsonl"; T="evidence/thief-intents-$P-$TS.jsonl"
TMP="$(mktemp -d)"; DB="$TMP/shared.db"; TOKEN="$TMP/owner.token"   # the token never enters the repo
export AIRLOCK_PROFILE="$P" AIRLOCK_COLD_KEY=//AirlockDemo//cold AIRLOCK_HOT_KEY=//AirlockDemo//hot
APIPID=""; WATCHER=""
cleanup() {
  [[ -f "$LOG" ]] && python3 -c "import sys,pathlib;p=pathlib.Path(sys.argv[1]);p.write_text(p.read_text().replace(sys.argv[2],'<tmp>'))" "$LOG" "$TMP"
  [[ -n "$APIPID" ]] && { kill -CONT "$APIPID" 2>/dev/null; kill -9 "$APIPID" 2>/dev/null; } || true
  [[ -n "$WATCHER" ]] && kill "$WATCHER" 2>/dev/null || true
  rm -rf "$TMP" ".thief-intents-$P.json"
}
trap cleanup EXIT
say() { echo "[driver $(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
waitfor() {
  local f="$1" pat="$2" n=0
  until grep -q "$pat" "$f" 2>/dev/null; do
    sleep 1; n=$((n+1)); [[ $n -gt 240 ]] && { echo "timeout waiting for '$pat' in $f" >&2; cat "$f" >&2; exit 1; }
  done; return 0
}
alerts() { [[ -f "$J" ]] && grep -c . "$J" || echo 0; }
echo "code $CODE · profile $P · vault $VAULT · delay $DELAY blocks · registry $API · payee payee-rent $PAYEE · thief payee attacker-2 $THIEF_TO" > "$LOG"

python3 -u -m airlock.api --db "$DB" --port "$PORT" >> "$LOG" 2>&1 &
APIPID=$!
waitfor "$LOG" "intent registry on"
python3 -u -m airlock.watcher --db "$DB" --jsonl "$J" --vault "$VAULT" >> "$LOG" 2>&1 &
WATCHER=$!
waitfor "$LOG" "watching"

say "1. enrol: the cold key signs the owner token's digest"
airlock enroll --vault "$VAULT" --api "$API" --token-file "$TOKEN" 2>&1 | sed "s#$TMP#<tmp>#g" | tee -a "$LOG"

say "2. owner announces a spend: register first, then proxy.announce"
airlock announce --vault "$VAULT" --to "$PAYEE" --amount 1.5 --api "$API" --token "file:$TOKEN" 2>&1 | tee -a "$LOG" "$TMP/own"
OWN_HASH="$(grep -o 'announced 0x[0-9a-f]*' "$TMP/own" | cut -d' ' -f2)"
[[ -n "$OWN_HASH" ]] || { say "owner announce failed"; exit 1; }
until [[ "$(alerts)" -ge 1 ]]; do sleep 1; done
say "alert 1: $(sed -n 1p "$J" | python3 -c 'import json,sys;a=json.load(sys.stdin);print(a["verdict"],a["call_hash"],a["extra"].get("call"))')"

say "3. thief posts the sweep's preimage without the owner token, then announces it (scripts/thief.py, no airlock code)"
python3 scripts/thief.py --url "$WS" --vault "$VAULT" --to "$THIEF_TO" --amount all --post-intent "$API" \
  --state ".thief-intents-$P.json" | tee -a "$T" "$LOG"
until [[ "$(alerts)" -ge 2 ]]; do sleep 1; done
say "alert 2: $(sed -n 2p "$J" | python3 -c 'import json,sys;a=json.load(sys.stdin);print(a["verdict"],a["call_hash"])')"
CMD="$(sed -n 2p "$J" | python3 -c 'import json,sys;print(json.load(sys.stdin)["veto_command"])')"
echo "\$ $CMD" | tee -a "$LOG"
$CMD 2>&1 | tee -a "$LOG" "$TMP/veto"
VETO_ID="$(grep -o 'id [0-9][0-9]*-[0-9][0-9]*' "$TMP/veto" | head -1 | cut -d' ' -f2)"

pending_count() { airlock --json status --vault "$VAULT" 2>/dev/null | tail -1 | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["result"]["pending"]))'; }
BEFORE_PENDING="$(pending_count)"
say "4. registry stalled mid-request (SIGSTOP $APIPID): owner announce must refuse after the timeout"
kill -STOP "$APIPID"
set +e
airlock announce --vault "$VAULT" --to "$PAYEE" --amount 2.5 --api "$API" --token "file:$TOKEN" >> "$LOG" 2>&1
RC_STALL=$?
say "   exit $RC_STALL"
say "5. registry killed (SIGKILL): owner announce must refuse"
kill -9 "$APIPID"; kill -CONT "$APIPID" 2>/dev/null; wait "$APIPID" 2>/dev/null; APIPID=""
airlock announce --vault "$VAULT" --to "$PAYEE" --amount 3.5 --api "$API" --token "file:$TOKEN" >> "$LOG" 2>&1
RC_DEAD=$?
set -e
say "   exit $RC_DEAD"
AFTER_PENDING="$(pending_count)"
say "pending announcements on the vault: $BEFORE_PENDING before 4./5., $AFTER_PENDING after"

say "6. owner's spend executes after the delay"
airlock execute --vault "$VAULT" --to "$PAYEE" --amount 1.5 2>&1 | grep -v '^  head' | tee -a "$LOG" "$TMP/exec"
sleep 8  # a few more blocks: any stray announcement would have alerted by now
kill "$WATCHER"; wait "$WATCHER" 2>/dev/null || true; WATCHER=""

python3 - "$J" "$T" "$OWN_HASH" "$RC_STALL" "$RC_DEAD" "$BEFORE_PENDING" "$AFTER_PENDING" <<'EOF' | tee -a "$LOG"
import json, sys
alerts = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
thief = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
own, rc_stall, rc_dead, before, after = sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6], sys.argv[7]
post = next(t for t in thief if t["step"] == "post-intent")
ann = next(t for t in thief if t["step"] == "announce")
checks = {
    "owner spend alerted YOURS": alerts[0]["verdict"] == "YOURS" and alerts[0]["call_hash"] == own,
    "thief preimage POST answered 401": post["http_status"] == 401,
    "thief announce alerted NOT YOURS": alerts[1]["verdict"] == "NOT YOURS" and alerts[1]["call_hash"] == ann["call_hash"],
    "registry stalled -> announce refused (exit 2)": rc_stall == 2,
    "registry killed -> announce refused (exit 2)": rc_dead == 2,
    "nothing announced while the registry was down": before == after,
    "exactly 2 alerts (none for the refused spends)": len(alerts) == 2,
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
ok = all(checks.values())
print("INTENTS", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
EOF
say "re-fetching the thief's extrinsics and the owner's/veto's by id over HTTP"
python3 scripts/refetch.py "$T" --profile "$P" --url "$HTTP" | tail -2 | tee -a "$LOG"
IDS=$(grep -o 'id [0-9][0-9]*-[0-9][0-9]*' "$TMP/own" "$TMP/exec" | cut -d' ' -f2 | sed 's/^/--id /' | tr '\n' ' ')
python3 scripts/refetch.py $IDS --id "$VETO_ID" --profile "$P" --url "$HTTP" | tail -6 | tee -a "$LOG"
echo "DONE code $CODE ts $TS"
