"""The watcher's block loop against a fake chain: decode, verdict, exactly-once alerts (I4), catch-up, retries."""

from types import SimpleNamespace

import pytest

from airlock.alerts import NOT_YOURS, TelegramError
from airlock.chain_profile import INTERIM_1002, INTERIM_SDK, Chain
from airlock.store import GenesisChanged, Store
from airlock.watcher import Watcher, decode_announced, delegate_delay

VAULT = "5Hb37SqmVJSzREN7ziBw1ZQJWLgDS7DLaEjKisf8a6KUYsLv"
OTHER = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"  # //Alice
HOT = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"  # //Bob
HOT_HEX = "0x8eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a48"  # //Bob public key
H1 = "0x" + "11" * 32
H2 = "0x" + "22" * 32


def announced(real, proxy, call_hash, idx=2):
    return SimpleNamespace(value={"module_id": "Proxy", "event_id": "Announced", "extrinsic_idx": idx,
                                  "attributes": {"real": real, "proxy": proxy, "call_hash": call_hash}})


def other_event():
    return SimpleNamespace(value={"module_id": "System", "event_id": "ExtrinsicSuccess", "extrinsic_idx": 0,
                                  "attributes": {"dispatch_info": {}}})


class FakeSubstrate:
    def __init__(self, head=100):
        self.head = head
        self.events = {}
        self.proxies = {VAULT: ([{"delegate": HOT, "proxy_type": "Any", "delay": 5},
                                 {"delegate": OTHER, "proxy_type": "Any", "delay": 0}], 198)}
        self.account = {"free": 1_000_000, "reserved": 0, "frozen": 0, "flags": 0}

    def get_block_hash(self, n):
        return None if n > self.head else f"0x{n:064x}"

    def get_block_number(self, _):
        return self.head

    def get_events(self, block_hash):
        return self.events.get(int(block_hash, 16), [])

    def query(self, module, storage, params, block_hash=None):
        if (module, storage) == ("Proxy", "Proxies"):
            return SimpleNamespace(value=self.proxies.get(params[0]))
        if (module, storage) == ("System", "Account"):
            return SimpleNamespace(value={"data": self.account})
        raise KeyError((module, storage))


def chain(fake, profile=INTERIM_SDK, genesis="0xgen"):
    return Chain(substrate=fake, profile=profile, url="ws://fake", genesis_hash=genesis, code_hash="0xcode",
                 spec_name="node", spec_version=268, decimals=12, symbol="UNIT", block_time_ms=3000,
                 block_time_source="consensus constant", has_revive=True, existential_deposit=1)


class Recorder:
    def __init__(self, name="stdout", fail_times=0):
        self.name, self.fail_times, self.sent = name, fail_times, []

    def send(self, alert, text):
        if self.fail_times:
            self.fail_times -= 1
            raise TelegramError("boom")
        self.sent.append((alert, text))


@pytest.fixture
def setup(tmp_path):
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    out = Recorder()
    w = Watcher(chain(fake), store, [out], log=lambda m: None, clock=lambda: 1.0)
    return SimpleNamespace(fake=fake, store=store, out=out, w=w)


# -- decoding --------------------------------------------------------------------------------------------------------

def test_decode_named_attributes_and_skip_other_events():
    got = decode_announced([other_event(), announced(VAULT, HOT, H1.upper().replace("0X", "0x"))])
    assert len(got) == 1 and got[0].real == VAULT and got[0].delegate == HOT and got[0].call_hash == H1
    assert got[0].extrinsic_index == 2


def test_decode_legacy_positional_attributes():
    legacy = SimpleNamespace(value={"module_id": "Proxy", "event_id": "Announced", "extrinsic_idx": 1, "attributes": [
        {"type": "AccountId", "value": VAULT}, {"type": "AccountId", "value": HOT}, {"type": "Hash", "value": H2}]})
    got = decode_announced([legacy])
    assert (got[0].real, got[0].delegate, got[0].call_hash) == (VAULT, HOT, H2)


def test_decode_bytes_call_hash():
    assert decode_announced([announced(VAULT, HOT, bytes.fromhex("11" * 32))])[0].call_hash == H1


@pytest.mark.parametrize("value,who,want", [
    (([{"delegate": HOT, "proxy_type": "Any", "delay": 5}], 1), HOT, 5),
    ([{"delegate": HOT, "proxy_type": "Any", "delay": 5}], HOT, 5),
    (([{"delegate": HOT, "delay": 9}, {"delegate": HOT, "delay": 4}], 1), HOT, 4),
    (([{"delegate": HOT_HEX, "delay": 7}], 1), HOT, 7),
    (([{"delegate": OTHER, "delay": 0}], 1), HOT, None),
    (None, HOT, None),
])
def test_delegate_delay_shapes(value, who, want):
    assert delegate_delay(value, who) == want


# -- the loop --------------------------------------------------------------------------------------------------------

def test_announcement_on_watched_vault_alerts_not_yours(setup):
    setup.fake.events[100] = [announced(VAULT, HOT, H1)]
    alerts = setup.w.catch_up(100)
    assert len(alerts) == 1 and len(setup.out.sent) == 1
    a, text = setup.out.sent[0]
    assert a.verdict == NOT_YOURS and a.release_block == 105 and a.blocks_left == 5 and a.at_risk == "0.0000 UNIT"
    assert text.startswith("NOT YOURS — VETO") and f"--hash {H1}" in text
    row = setup.store.announcements()[0]
    assert row["alert_sent_at"] == 1.0 and row["delivered_via"] == "stdout" and row["verdict"] == NOT_YOURS


def test_at_risk_excludes_locked_funds(setup):
    setup.fake.account = {"free": 5 * 10**12, "reserved": 0, "frozen": 2 * 10**12, "flags": 0}
    setup.fake.events[100] = [announced(VAULT, HOT, H1)]
    a, text = setup.out.sent[0] if setup.w.catch_up(100) else (None, None)
    assert a.at_risk == "3.0000 UNIT" and a.locked == "2.0000 UNIT"
    assert "(2.0000 UNIT locked, not movable)" in text


def test_unwatched_vault_is_ignored(setup):
    setup.fake.events[100] = [announced(OTHER, HOT, H1)]
    assert setup.w.catch_up(100) == [] and setup.out.sent == []
    assert setup.store.last_block == 100


def test_same_announcement_alerts_exactly_once_on_reprocessing(setup):
    """I4: a block processed twice (crash before the cursor moved, reconnect) never alerts twice."""
    setup.fake.events[100] = [announced(VAULT, HOT, H1)]
    setup.w.catch_up(100)
    setup.w.process_block(100, 100)
    assert len(setup.out.sent) == 1 and len(setup.store.announcements()) == 1


def test_two_announcements_in_one_block_alert_separately(setup):
    setup.fake.events[100] = [announced(VAULT, HOT, H1, idx=2), announced(VAULT, HOT, H2, idx=3)]
    setup.w.catch_up(100)
    assert [a.call_hash for a, _ in setup.out.sent] == [H1, H2]


def test_resume_scans_the_blocks_missed_while_offline(setup):
    setup.w.catch_up(100)  # first run starts at the head
    setup.fake.head = 106
    setup.fake.events[102] = [announced(VAULT, HOT, H1)]
    alerts = setup.w.catch_up(106)
    assert [a.announced_block for a in alerts] == [102]
    a, text = setup.out.sent[0]
    assert a.late_by == 4 and "Seen 4 blocks late" in text and a.blocks_left == 1
    assert setup.store.last_block == 106


def test_first_run_from_block(setup):
    setup.fake.events[97] = [announced(VAULT, HOT, H1)]
    assert [a.announced_block for a in setup.w.catch_up(100, from_block=95)] == [97]


def test_cursor_persists_across_restarts(tmp_path):
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    Watcher(chain(fake), store, [Recorder()], log=lambda m: None).catch_up(100)
    store.close()
    reopened = Store(tmp_path / "w.db")
    assert reopened.last_block == 100 and VAULT in reopened.vaults("interim-sdk")
    assert Watcher(chain(fake), reopened, [Recorder()], log=lambda m: None).start_from(130, None) == 101


def test_vaults_are_scoped_to_the_profile(setup):
    setup.store.watch(OTHER, "interim-1002")
    assert list(setup.store.vaults("interim-sdk")) == [VAULT]


def test_another_genesis_stops_the_watcher(setup):
    with pytest.raises(GenesisChanged):
        Watcher(chain(setup.fake, genesis="0xother"), setup.store, [Recorder()], log=lambda m: None)


def test_failed_channel_is_retried_next_block_without_repeating_the_others(tmp_path):
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    stdout, telegram, log = Recorder("stdout"), Recorder("telegram", fail_times=1), Recorder("jsonl")
    w = Watcher(chain(fake), store, [stdout, telegram, log], log=lambda m: None, clock=lambda: 7.0)
    fake.events[100] = [announced(VAULT, HOT, H1)]
    w.catch_up(100)
    row = store.announcements()[0]
    assert row["alert_sent_at"] is None and row["delivered_via"] == "jsonl,stdout" and w.alerts_sent == 0
    fake.head = 101
    w.catch_up(101)
    row = store.announcements()[0]
    assert row["alert_sent_at"] == 7.0 and row["delivered_via"] == "jsonl,stdout,telegram" and row["attempts"] == 2
    assert len(stdout.sent) == 1 and len(telegram.sent) == 1 and len(log.sent) == 1
    assert telegram.sent[0][0].head == 101 and telegram.sent[0][0].blocks_left == 4  # countdown recomputed


def test_retries_stop_after_max_attempts(tmp_path, monkeypatch):
    import airlock.watcher as watcher_mod
    monkeypatch.setattr(watcher_mod, "MAX_ATTEMPTS", 2)
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    telegram = Recorder("telegram", fail_times=99)
    w = Watcher(chain(fake), store, [telegram], log=lambda m: None)
    fake.events[100] = [announced(VAULT, HOT, H1)]
    for head in (100, 101, 102, 103):
        fake.head = head
        w.catch_up(head)
    assert store.announcements()[0]["attempts"] == 2 and telegram.fail_times == 97


def test_legacy_1002_profile_end_to_end(tmp_path):
    fake = FakeSubstrate()
    fake.account = {"free": 10 * 10**14, "reserved": 0, "misc_frozen": 4 * 10**14, "fee_frozen": 0}
    fake.proxies = {VAULT: [[HOT, "Any", 5]]}  # positional definitions
    fake.events[100] = [SimpleNamespace(value={"module_id": "Proxy", "event_id": "Announced", "extrinsic_idx": 1,
                                               "attributes": [{"type": "AccountId", "value": VAULT},
                                                              {"type": "AccountId", "value": HOT},
                                                              {"type": "Hash", "value": H1}]})]
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-1002")
    out = Recorder()
    c = chain(fake, profile=INTERIM_1002)
    c.decimals, c.symbol = 14, "POT"
    Watcher(c, store, [out], log=lambda m: None).catch_up(100)
    a, text = out.sent[0]
    assert a.at_risk == "6.0000 POT" and a.locked == "4.0000 POT" and a.release_block == 105
    assert "Chain: interim — not V3 (Portaldot dev node, runtime spec 1002)" in text
