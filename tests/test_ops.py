"""Vault operations against a fake chain: the pre-checks that refuse before signing, and the read-backs that refuse to
call an included extrinsic a success when the state does not show it."""

from types import SimpleNamespace

import pytest

from airlock.callhash import Announcement, call_hash
from airlock.chain_profile import INTERIM_SDK, Chain
from airlock.ops import (NotConfirmed, Pending, Refused, Sender, TxFailed, announce, blocks_for, definitions, delay_of,
                         execute, find_pending, is_pending, panic, pending_for, veto, withdraw)
from substrateinterface import Keypair

COLD = Keypair.create_from_uri("//Alice")
HOT = Keypair.create_from_uri("//Bob")
VAULT = "5Hb37SqmVJSzREN7ziBw1ZQJWLgDS7DLaEjKisf8a6KUYsLv"
HOT_HEX = "0x" + HOT.public_key.hex()
H = "0x" + "ab" * 32


class FakeCall:
    def __init__(self, module, function, params):
        self.call_module = {"name": module}
        self.call_function = {"name": function}
        self.value = {"call_module": module, "call_function": function, "call_args": params}
        self.params = params
        self.data = SimpleNamespace(data=f"{module}.{function}:{sorted(params)}".encode())

    def get_used_bytes(self):
        return self.data.data


class FakeSubstrate:
    """Proxies + Announcements in memory; each submitted extrinsic applies its effect (or a scripted failure)."""

    def __init__(self, head=100):
        self.head = head
        self.proxies = {VAULT: [{"delegate": HOT.ss58_address, "proxy_type": "Any", "delay": 5},
                                {"delegate": COLD.ss58_address, "proxy_type": "Any", "delay": 0}]}
        self.announcements: dict[str, list[dict]] = {}
        self.sent: list[FakeCall] = []
        self.fail_next: str | None = None
        self.ignore_effect = False

    def get_metadata(self):
        return SimpleNamespace(pallets=[])

    def query(self, module, storage, params, block_hash=None):
        if (module, storage) == ("Proxy", "Proxies"):
            return SimpleNamespace(value=(self.proxies.get(params[0], []), 0))
        if (module, storage) == ("Proxy", "Announcements"):
            return SimpleNamespace(value=(self.announcements.get(params[0], []), 0))
        raise KeyError((module, storage))

    def get_block_number(self, block_hash):
        return self.head

    def compose_call(self, module, function, params):
        return FakeCall(module, function, params)

    def create_signed_extrinsic(self, call, keypair):
        return (call, keypair)

    def submit_extrinsic(self, xt, wait_for_inclusion):
        call, keypair = xt
        self.sent.append(call)
        self.head += 1
        failed = self.fail_next
        self.fail_next = None
        if not failed and not self.ignore_effect:
            self._apply(call, keypair)
        events = []
        if failed:
            events.append(SimpleNamespace(value={"module_id": "System", "event_id": "ExtrinsicFailed",
                                                 "attributes": {"dispatch_error": {"Other": failed}}}))
        return SimpleNamespace(is_success=not failed, triggered_events=events, block_hash=f"0xblock{self.head}",
                               extrinsic_hash="0x" + "ee" * 32, get_extrinsic_identifier=lambda: f"{self.head}-1")

    def _apply(self, call, keypair):
        name = (call.call_module["name"], call.call_function["name"])
        if name == ("Proxy", "announce"):
            self.announcements.setdefault(keypair.ss58_address, []).append(
                {"real": call.params["real"], "call_hash": call.params["call_hash"], "height": self.head})
        elif name == ("Proxy", "proxy"):
            inner = call.params["call"]
            if inner.call_function["name"] == "reject_announcement":
                d = inner.params["delegate"]
                self.announcements[d] = [a for a in self.announcements.get(d, [])
                                         if a["call_hash"] != inner.params["call_hash"]]
            elif inner.call_function["name"] == "remove_proxy":
                self.proxies[VAULT] = [p for p in self.proxies[VAULT] if p["delegate"] != inner.params["delegate"]]
        elif name in (("Proxy", "proxy_announced"), ("Proxy", "remove_announcement")):
            h = call_hash(call.params["call"]) if "call" in call.params else call.params["call_hash"]
            self.announcements[keypair.ss58_address] = [a for a in self.announcements.get(keypair.ss58_address, [])
                                                        if a["call_hash"] != h]


def make(head=100):
    s = FakeSubstrate(head)
    chain = Chain(substrate=s, profile=INTERIM_SDK, url="ws://fake", genesis_hash=INTERIM_SDK.genesis_hash,
                  code_hash=INTERIM_SDK.code_hashes[0], spec_name="node", spec_version=268, decimals=12, symbol="UNIT",
                  block_time_ms=3000, block_time_source="test", has_revive=True, existential_deposit=10)
    return s, Sender(chain)


def spend(s):
    return s.compose_call("Balances", "transfer_keep_alive", {"dest": COLD.ss58_address, "value": 5})


# pure helpers -------------------------------------------------------------------------------------------------------

def test_definitions_all_shapes():
    named = ([{"delegate": "a", "proxy_type": "Any", "delay": 3}], 7)
    positional = [("b", {"Any": None}, 0)]
    assert definitions(named) == definitions(named[0]) and definitions(named)[0].delay == 3
    assert definitions(positional)[0].proxy_type == "Any"
    assert definitions(None) == [] and definitions(([], 0)) == []


def test_delay_of_matches_any_ss58_prefix_and_takes_the_shortest():
    defs = definitions([{"delegate": HOT_HEX, "proxy_type": "Any", "delay": 9},
                        {"delegate": HOT.ss58_address, "proxy_type": "Transfer", "delay": 4}])
    assert delay_of(defs, HOT.ss58_address) == 4
    assert delay_of(defs, COLD.ss58_address) is None


def test_blocks_for_rounds_up():
    assert blocks_for(60, 3000) == 20
    assert blocks_for(61, 3000) == 21
    assert blocks_for(0.1, 6000) == 1
    with pytest.raises(Refused, match="block time is unknown"):
        blocks_for(60, None)


def test_release_block_and_dead_announcement():
    assert Pending(HOT.ss58_address, H, 100, 5).release_block == 105
    assert Pending(HOT.ss58_address, H, 100, None).release_block is None


def test_is_pending_needs_hash_and_vault():
    entries = [Announcement(real=VAULT, call_hash=H, height=1)]
    assert is_pending(entries, VAULT, H.upper().replace("0X", "0x"))
    assert is_pending(entries, COLD.ss58_address, H) is None
    assert is_pending(entries, VAULT, "0x" + "cd" * 32) is None


# announce / execute -------------------------------------------------------------------------------------------------

def test_announce_records_height_and_release():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    assert (p.height, p.delay, p.release_block) == (101, 5, 106)
    assert tx.rows[0]["call"] == "Proxy.announce" and tx.rows[0]["profile"] == "interim-sdk — not V3"


def test_announce_refused_for_a_non_delegate():
    s, tx = make()
    with pytest.raises(Refused, match="NotProxy"):
        announce(tx, Keypair.create_from_uri("//Eve"), VAULT, spend(s))
    assert s.sent == []


def test_announce_not_confirmed_when_storage_lacks_it():
    s, tx = make()
    s.ignore_effect = True
    with pytest.raises(NotConfirmed, match="I3"):
        announce(tx, HOT, VAULT, spend(s))


def test_execute_refused_before_delay_without_wait():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    with pytest.raises(Refused, match="executable from #106"):
        execute(tx, HOT, VAULT, spend(s), wait=False)
    assert len(s.sent) == 1  # only the announce


def test_execute_after_delay_clears_the_announcement():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    s.head = 106
    execute(tx, HOT, VAULT, spend(s), wait=False)
    assert tx.rows[-1]["call"] == "Proxy.proxy_announced(Balances.transfer_keep_alive)" and not find_pending(tx, VAULT, HOT.ss58_address,
                                                                                 call_hash(spend(s)))


def test_execute_refused_when_not_pending():
    s, tx = make()
    with pytest.raises(Refused, match="Unannounced"):
        execute(tx, HOT, VAULT, spend(s), wait=False)


def test_execute_refused_after_panic():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    panic(tx, COLD, VAULT, HOT.ss58_address)
    s.head = 200
    with pytest.raises(Refused, match="no longer a delegate"):
        execute(tx, HOT, VAULT, spend(s), wait=False)


# veto / panic / withdraw --------------------------------------------------------------------------------------------

def test_veto_removes_the_announcement():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    veto(tx, COLD, VAULT, HOT.ss58_address, p.call_hash.upper().replace("0X", "0x"))
    assert tx.rows[-1]["call"] == "Proxy.proxy(Proxy.reject_announcement)"
    assert find_pending(tx, VAULT, HOT.ss58_address, p.call_hash) is None


def test_veto_refusals():
    s, tx = make()
    with pytest.raises(Refused, match="64 hex"):
        veto(tx, COLD, VAULT, HOT.ss58_address, "0x1234")
    with pytest.raises(Refused, match="nothing to veto"):
        veto(tx, COLD, VAULT, HOT.ss58_address, H)
    with pytest.raises(Refused, match="not a delay-0 delegate"):
        veto(tx, HOT, VAULT, HOT.ss58_address, H)  # the delayed key cannot veto as the vault
    assert s.sent == []


def test_veto_failed_on_chain_is_reported_with_the_row():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    s.fail_next = "BadOrigin"
    with pytest.raises(TxFailed) as e:
        veto(tx, COLD, VAULT, HOT.ss58_address, p.call_hash)
    assert e.value.row["outcome"] != "ok" and e.value.row["extrinsic_id"]


def test_veto_included_but_still_pending_is_not_a_success():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    s.ignore_effect = True
    with pytest.raises(NotConfirmed, match="still pending"):
        veto(tx, COLD, VAULT, HOT.ss58_address, p.call_hash)


def test_panic_removes_the_delegate_and_its_announcements_die():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    panic(tx, COLD, VAULT, HOT.ss58_address)
    assert delay_of(tx.delegates(VAULT), HOT.ss58_address) is None
    assert [p.release_block for p in pending_for(tx, VAULT)] == []  # pending_for lists current delegates only
    assert find_pending(tx, VAULT, HOT.ss58_address, call_hash(spend(s))).release_block is None


def test_panic_refusals():
    s, tx = make()
    with pytest.raises(Refused, match="cold key itself"):
        panic(tx, COLD, VAULT, COLD.ss58_address)
    with pytest.raises(Refused, match="nothing to remove"):
        panic(tx, COLD, VAULT, Keypair.create_from_uri("//Eve").ss58_address)
    assert s.sent == []


def test_withdraw_own_announcement():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    withdraw(tx, HOT, VAULT, p.call_hash)
    assert tx.rows[-1]["call"] == "Proxy.remove_announcement" and not pending_for(tx, VAULT)
