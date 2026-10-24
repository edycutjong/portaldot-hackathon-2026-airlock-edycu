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
        self.fork = {}  # block number -> replacement hash (a reorg or a reset chain)
        self.events = {}
        self.removed = set()  # (vault, call_hash) vetoed or executed
        self.proxies = {VAULT: ([{"delegate": HOT, "proxy_type": "Any", "delay": 5},
                                 {"delegate": OTHER, "proxy_type": "Any", "delay": 0}], 198)}
        self.account = {"free": 1_000_000, "reserved": 0, "frozen": 0, "flags": 0}

    def get_block_hash(self, n):
        return None if n > self.head else self.fork.get(n, f"0x{n:064x}")

    def get_block_number(self, _):
        return self.head

    def get_events(self, block_hash):
        if block_hash in self.fork.values():
            return self.events.get(block_hash, [])
        return self.events.get(int(block_hash, 16), [])

    def announcements(self, delegate):
        """Proxy.Announcements(delegate) as the chain would hold it: every Announced event so far, minus removed."""
        out = []
        for block, evs in self.events.items():
            number = block if isinstance(block, int) else next(n for n, h in self.fork.items() if h == block)
            for e in evs:
                v = e.value
                if v["event_id"] != "Announced" or number > self.head:
                    continue
                a = v["attributes"]
                real, proxy, h = (a["real"], a["proxy"], a["call_hash"]) if isinstance(a, dict) else \
                    (a[0]["value"], a[1]["value"], a[2]["value"])
                h = "0x" + bytes(h).hex() if isinstance(h, (bytes, bytearray)) else h.lower()
                if proxy == delegate and (real, h) not in self.removed:
                    out.append({"real": real, "call_hash": h, "height": number})
        return (out, 1) if out else None

    def query(self, module, storage, params, block_hash=None):
        if (module, storage) == ("Proxy", "Announcements"):
            return SimpleNamespace(value=self.announcements(params[0]))
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
    assert "(2.0000 UNIT of the free balance cannot move" in text


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
    ticks = iter(range(1, 10**6))
    w = Watcher(chain(fake), store, [stdout, telegram, log], log=lambda m: None, clock=lambda: float(next(ticks)))
    fake.events[100] = [announced(VAULT, HOT, H1)]
    w.catch_up(100)
    row = store.announcements()[0]
    assert row["alert_sent_at"] is None and row["delivered_via"] == "jsonl,stdout" and w.alerts_sent == 0
    assert row["attempts"] == 1  # not re-tried inside the pass that just failed
    fake.head = 101
    w.catch_up(101)
    row = store.announcements()[0]
    assert row["alert_sent_at"] is not None and row["delivered_via"] == "jsonl,stdout,telegram" and row["attempts"] == 2
    assert len(stdout.sent) == 1 and len(telegram.sent) == 1 and len(log.sent) == 1
    assert telegram.sent[0][0].head == 101 and telegram.sent[0][0].blocks_left == 4  # countdown recomputed


def test_each_channel_is_recorded_as_soon_as_it_succeeds(tmp_path):
    """A crash after stdout succeeded but before telegram was tried must not repeat stdout on restart."""
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")

    class Crash(Exception):
        pass

    class Dies(Recorder):
        def send(self, alert, text):
            raise Crash()

    w = Watcher(chain(fake), store, [Recorder("stdout"), Dies("telegram")], log=lambda m: None)
    fake.events[100] = [announced(VAULT, HOT, H1)]
    with pytest.raises(Crash):
        w.catch_up(100)
    assert store.announcements()[0]["delivered_via"] == "stdout"
    stdout2, telegram2 = Recorder("stdout"), Recorder("telegram")
    w2 = Watcher(chain(fake), store, [stdout2, telegram2], log=lambda m: None)
    w2.catch_up(100)  # restart: the block is re-scanned (cursor never moved) and the alert retried
    assert stdout2.sent == [] and len(telegram2.sent) == 1 and w2.alerts_sent == 1


def test_retries_give_up_loudly_after_the_window(tmp_path):
    import airlock.watcher as watcher_mod
    now = [1000.0]
    logs = []

    def clock():
        now[0] += 0.001  # time moves on between passes
        return now[0]
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    telegram = Recorder("telegram", fail_times=10**6)
    w = Watcher(chain(fake), store, [telegram], log=logs.append, clock=clock)
    fake.events[100] = [announced(VAULT, HOT, H1)]
    w.catch_up(100)
    for head in range(101, 106):
        fake.head = head
        w.catch_up(head)
    assert store.announcements()[0]["attempts"] == 6
    now[0] += watcher_mod.RETRY_WINDOW_S + 1
    fake.head = 106
    w.catch_up(106)
    row = store.announcements()[0]
    assert row["gave_up"] == 1 and row["attempts"] == 7 and row["alert_sent_at"] is None  # one last try, then give up
    assert any(m.startswith("GAVE UP") and "telegram" in m for m in logs)
    fake.head = 107
    w.catch_up(107)
    assert store.announcements()[0]["attempts"] == 7  # no more retries


def test_a_last_attempt_after_the_window_can_still_deliver(tmp_path):
    """A forward clock jump (laptop sleep) must not give up an alert without one more try."""
    import airlock.watcher as watcher_mod
    now = [1000.0]
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    telegram = Recorder("telegram", fail_times=1)
    w = Watcher(chain(fake), store, [telegram], log=lambda m: None, clock=lambda: now[0])
    fake.events[100] = [announced(VAULT, HOT, H1)]
    w.catch_up(100)
    now[0] += watcher_mod.RETRY_WINDOW_S + 10
    fake.head = 101
    w.catch_up(101)
    row = store.announcements()[0]
    assert row["alert_sent_at"] is not None and row["gave_up"] == 0 and len(telegram.sent) == 1


def test_reorg_of_processed_blocks_is_rescanned(setup):
    setup.w.catch_up(100)
    setup.fake.head = 103
    setup.w.catch_up(103)
    # blocks 102-103 are replaced by a fork that carries an announcement in its #102
    setup.fake.fork = {102: "0x" + "aa" * 32, 103: "0x" + "bb" * 32}
    setup.fake.events["0x" + "aa" * 32] = [announced(VAULT, HOT, H2)]
    setup.fake.head = 104
    alerts = setup.w.catch_up(104)
    assert [a.announced_block for a in alerts] == [102] and alerts[0].call_hash == H2
    assert setup.store.last_block == 104


def test_reset_chain_with_the_same_genesis_stops(setup):
    """A --tmp dev chain restarted from genesis: same genesis hash, but the processed blocks are gone."""
    from airlock.store import ChainReset
    setup.w.catch_up(100)
    setup.fake.head = 5
    with pytest.raises(ChainReset):
        setup.w.catch_up(5)


def test_replaced_history_beyond_the_kept_hashes_stops(setup):
    from airlock.store import ChainReset
    setup.w.catch_up(100)
    setup.fake.head = 120
    setup.fake.fork = {n: "0x" + f"{n:064x}"[::-1] for n in range(0, 121)}
    with pytest.raises(ChainReset):
        setup.w.catch_up(120)


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


def test_stall_timeout_scales_with_block_time():
    from airlock.watcher import stall_seconds
    assert stall_seconds(3000) == 30.0 and stall_seconds(12000) == 60.0 and stall_seconds(None) == 30.0


# -- round-2 paths ---------------------------------------------------------------------------------------------------

def test_watchdog_fires_without_headers_and_not_while_beating():
    import threading
    import time as _time

    from airlock.watcher import Watchdog
    fired = threading.Event()
    dog = Watchdog(0.3, fired.set, tick_s=0.05).start()
    assert fired.wait(3) and dog.fired
    calm = threading.Event()
    dog2 = Watchdog(0.3, calm.set, tick_s=0.05).start()
    for _ in range(12):  # 0.6 s of regular beats
        dog2.beat()
        _time.sleep(0.05)
    dog2.stop()
    assert not calm.is_set() and not dog2.fired


def test_first_run_sweep_alerts_an_announcement_made_before_the_watcher_started(setup):
    setup.fake.events[97] = [announced(VAULT, HOT, H1)]
    alerts = setup.w.catch_up(100, sweep=True)  # first run starts at the head; block 97 is never scanned
    assert [(a.announced_block, a.extra["source"]) for a in alerts] == [(97, "pending sweep")]
    assert alerts[0].late_by == 3 and alerts[0].pending is True
    setup.fake.head = 101
    assert setup.w.catch_up(101, sweep=True) == []  # recorded: never swept twice
    assert len(setup.out.sent) == 1


def test_an_announcement_already_gone_is_reported_as_no_longer_pending(setup):
    setup.w.catch_up(100)
    setup.fake.events[102] = [announced(VAULT, HOT, H1)]
    setup.fake.removed.add((VAULT, H1))  # vetoed or executed while the watcher was down
    setup.fake.head = 104
    a, text = setup.out.sent[0] if setup.w.catch_up(104) else (None, None)
    assert a.pending is False and text.startswith("NOT YOURS — NO LONGER PENDING")
    assert "airlock veto" not in text and "vetoed, removed, or already executed" in text


def test_unreadable_events_are_a_logged_gap_covered_by_the_sweep(tmp_path):
    from substrateinterface.exceptions import SubstrateRequestException

    class Pruned(FakeSubstrate):
        def get_events(self, block_hash):
            if int(block_hash, 16) == 102:
                raise SubstrateRequestException({"code": 4003, "message": "State already discarded"})
            return super().get_events(block_hash)

    fake, logs = Pruned(), []
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    out = Recorder()
    w = Watcher(chain(fake), store, [out], log=logs.append)
    w.catch_up(100)
    fake.events[102] = [announced(VAULT, HOT, H1)]
    fake.head = 103
    assert w.catch_up(103) == [] and store.last_block == 101  # refused: the cursor stays, retried on the next header
    assert w.catch_up(103) == [] and store.last_block == 101
    alerts = w.catch_up(103)  # third refusal: a GAP the sweep covers
    assert any(m.startswith("GAP: events of #102 refused 3 times") for m in logs)
    assert [(a.announced_block, a.extra["source"]) for a in alerts] == [(102, "pending sweep")]
    assert store.last_block == 103


def test_a_refused_events_read_that_recovers_is_not_a_gap(tmp_path):
    from substrateinterface.exceptions import SubstrateRequestException

    class Busy(FakeSubstrate):
        refusals = 2

        def get_events(self, block_hash):
            if int(block_hash, 16) == 102 and self.refusals:
                self.refusals -= 1
                raise SubstrateRequestException({"code": -32000, "message": "rate limited"})
            return super().get_events(block_hash)

    fake, logs = Busy(), []
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    w = Watcher(chain(fake), store, [Recorder()], log=logs.append)
    w.catch_up(100)
    fake.events[102] = [announced(VAULT, HOT, H1)]
    fake.head = 102
    assert w.catch_up(102) == [] and w.pass_incomplete  # a pass that stopped short says so (--once exits 1 on it)
    assert w.catch_up(102) == [] and w.pass_incomplete
    alerts = w.catch_up(102)
    assert not w.pass_incomplete
    assert [(a.announced_block, a.extra["source"]) for a in alerts] == [(102, "block scan")]
    assert not any(m.startswith("GAP") for m in logs) and store.last_block == 102


def test_a_decoder_bug_is_a_logged_gap_not_a_lagging_node_or_a_crash_loop(setup):
    """A KeyError inside decoding is neither 'block not available yet' (that would wedge on the block) nor fatal (the
    cursor would not move, so every restart would crash on the same block): it is a logged GAP the sweep covers."""
    logs = []
    setup.w.log = logs.append
    setup.w.catch_up(100)
    setup.fake.events[101] = [SimpleNamespace(value={"module_id": "Proxy", "event_id": "Announced",
                                                     "attributes": {"real": VAULT}})]  # fields missing
    setup.fake.head = 101
    setup.w.catch_up(101)
    assert setup.store.last_block == 101
    assert any(m.startswith("GAP: Proxy.Announced in #101 undecodable (KeyError") for m in logs)
    assert not any("retrying on the next header" in m for m in logs)


def test_an_undecodable_block_is_a_gap_not_a_wedge(tmp_path):
    class Undecodable(FakeSubstrate):
        def get_events(self, block_hash):
            if int(block_hash, 16) == 101:
                raise NotImplementedError("decoder cannot read this event type")
            return super().get_events(block_hash)

    logs = []
    fake = Undecodable()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    w = Watcher(chain(fake), store, [Recorder()], log=logs.append)
    w.catch_up(100)
    fake.head = 102
    w.catch_up(102)
    assert store.last_block == 102 and any(m.startswith("GAP: events of #101") for m in logs)


def test_old_databases_are_migrated(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.executescript("""CREATE TABLE announcement (vault TEXT NOT NULL, delegate TEXT NOT NULL, call_hash TEXT NOT NULL,
        announced_block INTEGER NOT NULL, block_hash TEXT NOT NULL, extrinsic_index INTEGER, release_block INTEGER,
        verdict TEXT NOT NULL, at_risk TEXT, state TEXT NOT NULL DEFAULT 'held', alert_text TEXT, alert_sent_at REAL,
        PRIMARY KEY (vault, delegate, call_hash, announced_block));""")
    old.commit()
    old.close()
    store = Store(db)
    store.watch(VAULT, "interim-sdk")
    out = Recorder()
    fake = FakeSubstrate()
    fake.events[100] = [announced(VAULT, HOT, H1)]
    Watcher(chain(fake), store, [out], log=lambda m: None).catch_up(100)
    assert len(out.sent) == 1 and store.announcements()[0]["attempts"] == 1


def test_reads_node_lagging_the_subscription_stops_the_pass_without_moving_the_cursor(setup):
    setup.w.catch_up(100)
    logs = []
    setup.w.log = logs.append
    setup.w.catch_up(103)  # the header says 103, but the reads node only has up to 100
    assert setup.store.last_block == 100 and any("retrying on the next header" in m for m in logs)


def test_retries_respect_the_time_budget(tmp_path):
    """Past the budget no new retry starts — but one always does, so the oldest alert cannot block a pass forever."""
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    telegram = Recorder("telegram", fail_times=10**6)
    ticks = iter(range(1, 10**6))
    w = Watcher(chain(fake), store, [telegram], log=lambda m: None, clock=lambda: float(next(ticks)))
    fake.events[100] = [announced(VAULT, HOT, H1)]
    fake.events[101] = [announced(VAULT, HOT, H2)]
    fake.head = 101
    w.catch_up(101, from_block=100)  # both alerts attempted once, both failed
    def attempts():
        return {r["announced_block"]: r["attempts"] for r in store.announcements()}

    assert attempts() == {100: 1, 101: 1}
    w._attempted = set()  # a new pass
    assert w.retry_unsent(101, budget_s=-1) == 0  # budget already spent: exactly one retry started (and failed)
    assert sorted(attempts().values()) == [1, 2]
    telegram.fail_times = 0
    w._attempted = set()
    assert w.retry_unsent(101, budget_s=-1) == 1  # the other row's turn now
    assert attempts() == {100: 2, 101: 2}
    assert w.retry_unsent(101) == 1 and not store.unsent()  # same pass: only the row not yet tried in it is retried


def test_validate_vaults_refuses_typos_and_warns_on_vaults_without_delegates():
    from airlock.watcher import validate_vaults
    logs = []
    bad = validate_vaults(chain(FakeSubstrate()), [VAULT, OTHER, "5Hb37Sqm-typo"], logs.append)
    assert bad == ["5Hb37Sqm-typo"]
    assert any(OTHER in m and "no proxy delegates" in m for m in logs) and not any(VAULT in m for m in logs)


def test_misconfigured_telegram_refuses_to_start(monkeypatch, capsys):
    from airlock import watcher as watcher_mod
    monkeypatch.setenv("AIRLOCK_TELEGRAM_TOKEN", "not-a-token")
    monkeypatch.setattr(watcher_mod, "run", lambda *a, **k: pytest.fail("must not connect"))
    assert watcher_mod.main(["--vault", VAULT]) == 2
    assert "MISCONFIGURED" in capsys.readouterr().err


# -- an alert goes out even when a secondary figure cannot be read (unknown storage / account layout) ---------------

def test_unreadable_balance_still_alerts_with_the_whole_balance_at_risk(setup):
    logs = []
    setup.w.log = logs.append
    setup.fake.account = {"mystery": 1}  # an AccountData layout no profile knows
    setup.fake.events[100] = [announced(VAULT, HOT, H1)]
    alerts = setup.w.catch_up(100)
    assert len(alerts) == 1 and len(setup.out.sent) == 1
    text = setup.out.sent[0][1]
    assert text.startswith(NOT_YOURS) and "Up to the whole balance (unreadable) at risk" in text
    assert alerts[0].release_block == 105  # the other figures are still read
    assert any(m.startswith("alert figure 'at-risk' unreadable") for m in logs)


def test_unreadable_proxies_layout_still_alerts_with_veto_now(setup):
    setup.fake.proxies = {VAULT: ([{"who": HOT}], 198)}  # neither the named nor the positional shape
    setup.fake.events[100] = [announced(VAULT, HOT, H1)]
    alerts = setup.w.catch_up(100)
    assert alerts[0].release_block is None
    assert "its delay could not be read — veto now" in setup.out.sent[0][1]


def test_transport_errors_inside_an_alert_figure_still_reconnect(setup):
    class Dropped(FakeSubstrate):
        def query(self, module, storage, params, block_hash=None):
            if storage == "Account":
                raise ConnectionResetError("socket closed")
            return super().query(module, storage, params, block_hash)

    store = setup.store
    w = Watcher(chain(Dropped()), store, [Recorder()], log=lambda m: None)
    w.s.events[100] = [announced(VAULT, HOT, H1)]
    with pytest.raises(ConnectionResetError):
        w.catch_up(100)
    assert store.last_block is None  # the block is re-processed after the reconnect


def test_retries_rotate_so_one_slow_alert_cannot_starve_the_rest(tmp_path):
    """With a budget that allows one send per pass, each pass retries a different alert (least recently attempted)."""
    fake = FakeSubstrate()
    store = Store(tmp_path / "w.db")
    store.watch(VAULT, "interim-sdk")
    telegram = Recorder("telegram", fail_times=10**6)
    ticks = iter(range(1, 10**6))
    w = Watcher(chain(fake), store, [telegram], log=lambda m: None, clock=lambda: float(next(ticks)))
    for n, h in ((100, H1), (101, H2), (102, "0x" + "33" * 32)):
        fake.events[n] = [announced(VAULT, HOT, h)]
    w.catch_up(100, from_block=100)
    fake.head = 102
    w.catch_up(102)
    retried = []
    for _ in range(3):
        w._attempted = set()  # a new pass
        before = {r["announced_block"]: r["attempts"] for r in store.announcements()}
        w.retry_unsent(102, budget_s=0)  # the first send already exceeds the budget: one retry per pass
        after = {r["announced_block"]: r["attempts"] for r in store.announcements()}
        retried += [b for b in after if after[b] != before[b]]
    assert sorted(retried) == [100, 101, 102]


def test_node_lost_during_start_up_exits_cleanly(monkeypatch, capsys):
    from airlock import watcher as watcher_mod

    def boom(*a, **k):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(watcher_mod, "run", boom)
    assert watcher_mod.main(["--vault", VAULT, "--no-telegram"]) == 1
    assert "during start-up (ConnectionRefusedError" in capsys.readouterr().err


def test_a_sweep_that_cannot_read_storage_is_owed_to_the_next_pass(setup):
    """'Could not read Proxy.Announcements' must not count as 'nothing pending': the sweep is repeated."""
    setup.w.catch_up(100)
    setup.fake.events[99] = [announced(VAULT, HOT, H1)]  # pending since before the watcher's first block
    broken = {"on": True}
    real_query = setup.fake.query

    def flaky(module, storage, params, block_hash=None):
        if storage == "Announcements" and broken["on"]:
            raise ValueError("unknown Announcements layout")
        return real_query(module, storage, params, block_hash)

    setup.fake.query = flaky
    setup.fake.head = 101
    assert setup.w.catch_up(101, sweep=True) == [] and setup.w.sweep_owed
    broken["on"] = False
    setup.fake.head = 102
    alerts = setup.w.catch_up(102)  # no sweep asked for: the owed one runs
    assert [(a.announced_block, a.extra["source"]) for a in alerts] == [(99, "pending sweep")]
    assert not setup.w.sweep_owed


def test_once_exits_1_when_a_refused_events_read_stopped_the_pass(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    from substrateinterface.exceptions import SubstrateRequestException

    from airlock import watcher as wmod

    class Busy(FakeSubstrate):
        refusals = 1

        def get_events(self, block_hash):
            if int(block_hash, 16) == 101 and self.refusals:
                self.refusals -= 1
                raise SubstrateRequestException({"code": -32000, "message": "rate limited"})
            return super().get_events(block_hash)

    fake, logs = Busy(), []
    fake.head = 102
    fake.close = lambda: None
    monkeypatch.setattr(wmod, "_open_reads", lambda args: chain(fake))
    args = NS(db=tmp_path / "w.db", vault=[VAULT], from_block=101, finalized=False, once=True, max_alerts=None)
    assert wmod.run(args, [Recorder()], log=logs.append) == 1
    assert any("catch-up incomplete" in m for m in logs)
    assert wmod.run(args, [Recorder()], log=logs.append) == 0  # the node answers now: the one-shot completes
