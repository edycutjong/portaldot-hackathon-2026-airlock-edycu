"""I1 and I2 on a live chain, driven through the CLI's own operations (airlock.ops), not a hand-rolled script.

These test the Proxy pallet's guarantees Airlock relies on, on the current chain profile; Airlock's own logic is tested
offline in the other test files. Skipped unless AIRLOCK_IT=1, because they need a running node:

  scripts/interim_node.sh interim-sdk &                  # or interim-1002 with PORTALDOT_DEV set
  AIRLOCK_IT=1 AIRLOCK_PROFILE=interim-sdk pytest -q tests/integration

Signers are public dev keys, so only interim dev chains are accepted. With AIRLOCK_IT_EVIDENCE=<dir> every extrinsic
sent is written there as JSON (same row shape as the spike's evidence, re-fetchable with scripts/refetch.py).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("AIRLOCK_IT") != "1", reason="live-chain test: set AIRLOCK_IT=1")

DELAY = 3  # blocks; short so the suite runs in about a minute
# 1002's //Alice is a staking validator with little unlocked balance; //Dave is a plain funded dev account there
COLD_URI = {"interim-sdk": "//Alice", "interim-1002": "//Dave"}


@pytest.fixture(scope="module")
def env(request):
    from substrateinterface import Keypair

    from airlock.chain_profile import connect
    from airlock.ops import Sender, code_version, transfer

    chain = connect(None, os.environ.get("AIRLOCK_PROFILE"))
    if not chain.profile.is_interim:
        pytest.skip(f"{chain.profile.id}: these tests sign with public dev keys; interim dev chains only")
    tx = Sender(chain)
    cold = Keypair.create_from_uri(COLD_URI[chain.profile.id])
    hot = Keypair.create_from_uri("//AirlockIT//hot")
    payee = Keypair.create_from_uri("//AirlockIT//payee")
    # the hot key pays announcement deposits + fees: top it up from the cold key when low
    d = int(chain.substrate.get_constant("Proxy", "AnnouncementDepositBase").value) + \
        int(chain.substrate.get_constant("Proxy", "AnnouncementDepositFactor").value)
    need = 4 * d + 2 * chain.existential_deposit
    if tx.free(hot.ss58_address) < need:
        tx.send("fund hot (test setup)", "cold", cold, transfer(chain, hot.ss58_address, need))
    if tx.free(payee.ss58_address) < chain.existential_deposit:
        tx.send("fund payee (test setup)", "cold", cold, transfer(chain, payee.ss58_address, chain.existential_deposit))
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    yield {"chain": chain, "tx": tx, "cold": cold, "hot": hot, "payee": payee}
    out = os.environ.get("AIRLOCK_IT_EVIDENCE")
    if out:
        status = "passed" if request.session.testsfailed == 0 else "failed"
        path = Path(out) / f"integration-{chain.profile.id}-{started}{'' if status == 'passed' else '-FAILED'}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"started": started, "status": status, "code": code_version(), "profile": chain.profile.id,
                                    "label": chain.profile.label, "endpoint": chain.url,
                                    "genesis_hash": chain.genesis_hash, "delay_blocks": DELAY, "rows": tx.rows},
                                   indent=2) + "\n")
    chain.substrate.close()


_index = iter(range(100, 10_000))


@pytest.fixture
def vault(env):
    from airlock.ops import create_vault

    chain = env["chain"]
    # enough for the hot-key deposit the vault reserves and a few spends, in this chain's own units
    fund = 20 * chain.existential_deposit + 2 * int(chain.substrate.get_constant("Proxy", "ProxyDepositFactor").value)
    return create_vault(env["tx"], env["cold"], env["hot"].ss58_address, DELAY, next(_index), fund)


def theft(env, amount=None):
    from airlock.ops import transfer

    chain = env["chain"]
    return transfer(chain, env["payee"].ss58_address, amount or chain.existential_deposit * 3)


def test_I1_delayed_hot_key_cannot_spend_unannounced(env, vault):
    from airlock.ops import as_vault

    tx, chain = env["tx"], env["chain"]
    before = (tx.free(vault), tx.free(env["payee"].ss58_address))
    tx.send("I1 hot spends unannounced", "hot", env["hot"], as_vault(chain, vault, theft(env)), "Proxy.Unannounced")
    assert (tx.free(vault), tx.free(env["payee"].ss58_address)) == before


def test_I2_vetoed_announcement_never_executes(env, vault):
    from airlock.ops import announce, veto

    tx, chain, hot = env["tx"], env["chain"], env["hot"]
    call = theft(env)
    p = announce(tx, hot, vault, call, step="I2 announce")
    veto(tx, env["cold"], vault, hot.ss58_address, p.call_hash, step="I2 veto")
    tx.wait_until_block(p.release_block)
    before = (tx.free(vault), tx.free(env["payee"].ss58_address))
    executed = chain.substrate.compose_call("Proxy", "proxy_announced", {"delegate": hot.ss58_address, "real": vault,
                                                                        "force_proxy_type": None, "call": call})
    tx.send("I2 execute after veto", "hot", hot, executed, "Proxy.Unannounced", note="after the delay")
    assert (tx.free(vault), tx.free(env["payee"].ss58_address)) == before


def test_control_unvetoed_announcement_executes_after_the_delay_only(env, vault):
    """The control for I2: same delegate, same delay, no veto. Early execution fails; on time it moves the amount."""
    from airlock.ops import announce, execute

    tx, chain, hot = env["tx"], env["chain"], env["hot"]
    amount = chain.existential_deposit * 2
    call = theft(env, amount)
    p = announce(tx, hot, vault, call, step="control announce")
    early = chain.substrate.compose_call("Proxy", "proxy_announced", {"delegate": hot.ss58_address, "real": vault,
                                                                     "force_proxy_type": None, "call": call})
    r = tx.send("control execute early", "hot", hot, early, "Proxy.Unannounced")
    assert chain.substrate.get_block_number(r.block_hash) < p.release_block
    payee_before = tx.free(env["payee"].ss58_address)
    execute(tx, hot, vault, call, step="control execute on time")
    assert tx.free(env["payee"].ss58_address) - payee_before == amount


def test_panic_retires_the_hot_key(env, vault):
    from airlock.ops import announce, panic

    tx, chain, hot = env["tx"], env["chain"], env["hot"]
    call = theft(env)
    p = announce(tx, hot, vault, call, step="panic: pending announce")
    panic(tx, env["cold"], vault, hot.ss58_address, step="panic")
    tx.wait_until_block(p.release_block)
    executed = chain.substrate.compose_call("Proxy", "proxy_announced", {"delegate": hot.ss58_address, "real": vault,
                                                                        "force_proxy_type": None, "call": call})
    tx.send("panic: execute after removal", "hot", hot, executed, "Proxy.NotProxy")
    announce_again = chain.substrate.compose_call("Proxy", "announce", {"real": vault, "call_hash": p.call_hash})
    tx.send("panic: announce after removal", "hot", hot, announce_again, "Proxy.NotProxy")
