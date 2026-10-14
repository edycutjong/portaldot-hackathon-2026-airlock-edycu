#!/usr/bin/env bash
# End-to-end evidence on the seeded demo vault (run scripts/seed.py first), at the current commit:
#   thief announce (scripts/thief.py, no airlock imports) -> watcher NOT YOURS alert -> the alert's own veto command,
#   run as printed (airlock CLI, cold key) -> after the delay the thief's proxy_announced fails Proxy.Unannounced,
#   and a direct proxy.proxy fails Proxy.Unannounced -> vault balance unchanged, the thief's payee got nothing.
# Writes evidence/e2e-<profile>-<time>.{log,jsonl} and evidence/thief-<profile>-<time>.jsonl, then re-fetches the
# thief's and the veto's extrinsics by id over HTTP from a fresh process.
# Dev keys only (//AirlockDemo//…): local interim chains. usage: scripts/e2e_evidence.sh [interim-sdk|interim-1002]
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
VAULT="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['vault'])" "$STATE")"
DELAY="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['delay'])" "$STATE")"
TO="$(python3 -c "import json;print(json.load(open('fixtures/accounts.json'))['accounts']['attacker-3']['address_dev_root'])")"
CODE="$(git rev-parse --short HEAD)"; TS="$(date -u +%Y-%m-%dT%H%M%SZ)"
LOG="evidence/e2e-$P-$TS.log"; J="evidence/e2e-$P-$TS.jsonl"; T="evidence/thief-$P-$TS.jsonl"; DB=".e2e-$P.db"
export AIRLOCK_PROFILE="$P" AIRLOCK_COLD_KEY=//AirlockDemo//cold
WATCHER=""
cleanup() {
  [[ -n "$WATCHER" ]] && kill "$WATCHER" 2>/dev/null || true
  rm -f "$DB" ".thief-$P.json" "$LOG.veto"
}
trap cleanup EXIT
rm -f "$DB"
say() { echo "[driver $(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
waitfor() {
  local f="$1" pat="$2" n=0
  until grep -q "$pat" "$f" 2>/dev/null; do
    sleep 1; n=$((n+1)); [[ $n -gt 240 ]] && { echo "timeout waiting for '$pat' in $f" >&2; cat "$f" >&2; exit 1; }
  done; return 0
}
[[ -z "$(git status --porcelain -- airlock scripts)" ]] || CODE="$CODE-dirty"
echo "code $CODE · profile $P · vault $VAULT · delay $DELAY blocks · thief payee attacker-3 $TO" > "$LOG"

balances() { python3 -c "
import json,sys
from airlock.chain_profile import connect
c=connect(None,'$P'); a=[c.substrate.query('System','Account',[x]).value['data'] for x in sys.argv[1:]]
print(json.dumps({'vault_total': int(a[0]['free'])+int(a[0]['reserved']), 'payee_total': int(a[1]['free'])+int(a[1]['reserved'])}))
c.substrate.close()" "$VAULT" "$TO"; }
say "airlock status before"; airlock status --vault "$VAULT" >> "$LOG" 2>&1
BEFORE="$(balances)"; say "balances before $BEFORE"
python3 -u -m airlock.watcher --db "$DB" --jsonl "$J" --max-alerts 1 --vault "$VAULT" >> "$LOG" 2>&1 &
WATCHER=$!
waitfor "$LOG" "watching"

say "thief announces a sweep of the whole vault (scripts/thief.py, no airlock code, no intent registered)"
python3 scripts/thief.py --url "$WS" --vault "$VAULT" --to "$TO" --amount all --state ".thief-$P.json" | tee -a "$T" "$LOG"
wait "$WATCHER" || { say "watcher exited non-zero"; exit 1; }
WATCHER=""
say "alert delivered; running the veto command exactly as the alert printed it"
CMD="$(python3 -c "import json,sys;print(json.loads(open(sys.argv[1]).readline())['veto_command'])" "$J")"
echo "\$ $CMD" | tee -a "$LOG"
$CMD 2>&1 | tee -a "$LOG" "$LOG.veto"
VETO_ID="$(grep -o 'id [0-9][0-9]*-[0-9][0-9]*' "$LOG.veto" | head -1 | cut -d' ' -f2 || true)"; rm -f "$LOG.veto"
[[ -n "$VETO_ID" ]] || { say "veto produced no extrinsic id"; exit 1; }

ANN_BLOCK="$(python3 -c "import json,sys;print(json.loads(open(sys.argv[1]).readline())['block'])" "$T")"
say "waiting until the delay has passed (announced #$ANN_BLOCK + $DELAY)"
python3 -c "
import time,sys
from substrateinterface import SubstrateInterface
s=SubstrateInterface(url='$WS'); t=int(sys.argv[1]); end=time.monotonic()+int(sys.argv[2])
while s.get_block_number(None) < t:
    if time.monotonic() > end: sys.exit('chain stalled: block %d not reached' % t)
    time.sleep(1)" "$((ANN_BLOCK + DELAY + 1))" "$(( (DELAY + 2) * 30 + 60 ))"
say "thief executes the announced sweep after the delay"
python3 scripts/thief.py --url "$WS" --vault "$VAULT" --to "$TO" --amount all --state ".thief-$P.json" --execute | tee -a "$T" "$LOG"
say "thief tries proxy.proxy directly (no announcement)"
python3 scripts/thief.py --url "$WS" --vault "$VAULT" --to "$TO" --amount 1 --direct | tee -a "$T" "$LOG"
say "airlock status after"; airlock status --vault "$VAULT" >> "$LOG" 2>&1
AFTER="$(balances)"; say "balances after $AFTER"
python3 - "$T" "$BEFORE" "$AFTER" <<'EOF' | tee -a "$LOG"
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
got = [(r["step"], r["outcome"]) for r in rows]
want = [("announce", "ok"), ("execute", "Proxy.Unannounced"), ("direct", "Proxy.Unannounced")]
before, after = json.loads(sys.argv[2]), json.loads(sys.argv[3])
ok = got == want and before == after
print("E2E", "PASS" if ok else "FAIL", got, "· vault total and thief payee total unchanged:", before == after)
sys.exit(0 if ok else 1)
EOF
say "re-fetching the thief's extrinsics by id over HTTP"
python3 scripts/refetch.py "$T" --profile "$P" --url "$HTTP" | tail -2 | tee -a "$LOG"
python3 scripts/refetch.py --id "$VETO_ID" --profile "$P" --url "$HTTP" | tail -3 | tee -a "$LOG"
echo "DONE code $CODE ts $TS"
