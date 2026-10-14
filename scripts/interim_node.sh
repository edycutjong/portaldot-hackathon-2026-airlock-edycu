#!/usr/bin/env bash
# Run the labelled interim chain Airlock develops against while the Portaldot V3 testnet is offline.
#
#   interim-sdk  (default) upstream polkadot-sdk `substrate-node` dev chain — a current runtime with Proxy
#                (create_pure/announce) and Revive. Prebuilt release binary, sha256-checked.
#   interim-1002 the sponsor's `portaldot_dev` binary (runtime spec 1002, Proxy only, x86_64 → Rosetta on
#                Apple Silicon). Point PORTALDOT_DEV at it.
#
# Evidence from either chain is labelled "interim — not V3" (see airlock/chain_profile.py).
# Both keep every block's state (archive), so a recorded extrinsic's events can be re-read later; a pruned node
# discards state after 256 blocks and old receipts stop resolving.
# The interim-sdk binary is pinned for macOS arm64 only (the platform this was built on); elsewhere, build
# substrate-node from the same polkadot-sdk tag or use interim-1002.
#
# usage: scripts/interim_node.sh [interim-sdk|interim-1002] [--fresh]
set -euo pipefail

PROFILE="${1:-interim-sdk}"
CACHE="${AIRLOCK_CACHE:-$HOME/.cache/airlock-interim}"
SDK_TAG="polkadot-stable2606-3"
SDK_SHA256="e687db4993973bd83922cfa9f01e29bb549af16560511b1fd51ff883b996207f"  # aarch64-apple-darwin

case "$PROFILE" in
  interim-sdk)
    os="$(uname -s)-$(uname -m)"
    case "$os" in
      Darwin-arm64) asset="substrate-node-aarch64-apple-darwin" ;;
      *) echo "no prebuilt substrate-node for $os in $SDK_TAG; build it or use interim-1002" >&2; exit 1 ;;
    esac
    bin="$CACHE/$SDK_TAG/$asset"
    if [[ ! -x "$bin" ]]; then
      mkdir -p "$CACHE/$SDK_TAG"
      curl -fsSL --retry 3 -o "$bin" "https://github.com/paritytech/polkadot-sdk/releases/download/$SDK_TAG/$asset"
      chmod +x "$bin"
    fi
    echo "$SDK_SHA256  $bin" | shasum -a 256 -c - >/dev/null || { echo "sha256 mismatch: $bin" >&2; exit 1; }
    base="$CACHE/chain-sdk"
    # --fresh starts a new chain (same genesis); needed once when switching an existing pruned database to archive
    [[ "${2:-}" == "--fresh" ]] && rm -rf "$base"
    echo "interim-sdk: ws://127.0.0.1:29944 (data: $base)"
    exec "$bin" --dev --base-path "$base" --state-pruning archive --blocks-pruning archive --rpc-port 29944 --port 30533 --no-prometheus --no-telemetry
    ;;
  interim-1002)
    bin="${PORTALDOT_DEV:?set PORTALDOT_DEV to the portaldot_dev binary from portaldotVolunteer/Portaldot-node}"
    runner=()
    [[ "$(uname -s)-$(uname -m)" == "Darwin-arm64" ]] && runner=(arch -x86_64)
    # On this older node --rpc-port is HTTP only; websocket is --ws-port.
    echo "interim-1002: ws://127.0.0.1:19944 (--tmp, state resets on restart)"
    exec ${runner[@]+"${runner[@]}"} "$bin" --dev --tmp --pruning archive --ws-port 19944 --rpc-port 19933 --port 30433 --no-prometheus --no-telemetry
    ;;
  *) echo "unknown profile: $PROFILE (interim-sdk | interim-1002)" >&2; exit 2 ;;
esac
