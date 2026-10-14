"""The watcher's verdict from the owner intent registry: YOURS only for the owner's registered call, announced after
registration; a thief's announcement — even of a call whose preimage is known, or a replay of the owner's own — stays
NOT YOURS (I6). The registry and the watcher share one SQLite file."""

from airlock.alerts import NOT_YOURS, YOURS, format_text
from airlock.store import Store
from airlock.watcher import Watcher
from tests.test_watcher import H1, H2, HOT, OTHER, VAULT, FakeSubstrate, Recorder, announced, chain

CALL_HEX = "0x0603"


def make(tmp_path):
    fake = FakeSubstrate()
    db = tmp_path / "shared.db"
    store = Store(db)
    store.watch(VAULT, "interim-sdk")
    out = Recorder()
    w = Watcher(chain(fake), store, [out], log=lambda m: None, clock=lambda: 1.0)
    return fake, Store(db), out, w  # a second connection: the registry's process


def test_registered_intent_makes_the_alert_yours(tmp_path):
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(VAULT, H1, CALL_HEX, "Balances.transfer_keep_alive(dest=X, value=5.0000 UNIT)", 99)
    fake.events[100] = [announced(VAULT, HOT, H1)]
    (alert,) = w.catch_up(100)
    assert alert.verdict == YOURS and alert.extra["call"].startswith("Balances.transfer_keep_alive")
    text = out.sent[0][1]
    assert text.startswith("YOURS") and "NOT YOURS" not in text and "Cancel it" in text and "airlock veto" in text


def test_unregistered_announcement_is_not_yours(tmp_path):
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(VAULT, H1, CALL_HEX, "x", 99)
    fake.events[100] = [announced(VAULT, HOT, H2)]
    (alert,) = w.catch_up(100)
    assert alert.verdict == NOT_YOURS and out.sent[0][1].startswith("NOT YOURS — VETO")


def test_foreign_preimage_registered_for_another_vault_stays_not_yours(tmp_path):
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(OTHER, H1, CALL_HEX, "x", 99)  # the same call hash, vouched for on someone else's vault
    fake.events[100] = [announced(VAULT, HOT, H1)]
    assert w.catch_up(100)[0].verdict == NOT_YOURS


def test_replayed_owner_call_is_not_yours_the_second_time(tmp_path):
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(VAULT, H1, CALL_HEX, "x", 95)
    fake.events[96] = [announced(VAULT, HOT, H1)]
    fake.events[100] = [announced(VAULT, HOT, H1)]
    verdicts = [a.verdict for a in w.catch_up(100, from_block=96)]
    assert verdicts == [YOURS, NOT_YOURS]


def test_intent_registered_after_the_announcement_does_not_count(tmp_path):
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(VAULT, H1, CALL_HEX, "x", 100)  # registered at head #100, announcement in #100
    fake.events[100] = [announced(VAULT, HOT, H1)]
    assert w.catch_up(100)[0].verdict == NOT_YOURS


def test_rescan_keeps_the_verdict_and_does_not_realert(tmp_path):
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(VAULT, H1, CALL_HEX, "x", 99)
    fake.events[100] = [announced(VAULT, HOT, H1)]
    w.catch_up(100)
    w.store.rewind(99)
    again = w.catch_up(100)
    assert again[0].verdict == YOURS and len(out.sent) == 1


def test_unreadable_registry_fails_closed(tmp_path):
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(VAULT, H1, CALL_HEX, "x", 99)

    def broken(*a, **k):
        raise RuntimeError("database is locked")

    w.store.match_intent = broken
    fake.events[100] = [announced(VAULT, HOT, H1)]
    assert w.catch_up(100)[0].verdict == NOT_YOURS


def test_yours_text_has_no_veto_deadline_language():
    from airlock.alerts import Alert

    a = Alert(verdict=YOURS, profile_id="interim-sdk", profile_label="interim", is_interim=True, vault=VAULT,
              delegate=HOT, call_hash=H1, announced_block=100, block_hash="0x", release_block=105, head=100,
              block_time_ms=3000, at_risk="1 UNIT", locked=None,
              extra={"intent_id": 7, "registered_head": 99, "call": "Balances.transfer_keep_alive(…)"})
    text = format_text(a)
    assert "intent 7" in text and "executable from #105 (5 blocks (≈ 15 s)" in text and "Veto must land" not in text


def test_reorg_moving_the_owner_announcement_keeps_it_yours(tmp_path):
    """The owner's announce lands in #100, is reorged out and re-included in #101: still the owner's spend."""
    fake, registry, out, w = make(tmp_path)
    registry.add_intent(VAULT, H1, CALL_HEX, "x", 99)
    fake.events[100] = [announced(VAULT, HOT, H1)]
    assert w.catch_up(100, from_block=98)[0].verdict == YOURS
    fake.fork[100] = "0x" + "fe" * 32  # #100 replaced: the announcement is no longer in it
    fake.events[101] = [announced(VAULT, HOT, H1)]
    fake.head = 101
    again = w.catch_up(101)
    assert [(a.announced_block, a.verdict) for a in again] == [(101, YOURS)]


def test_rewind_releases_only_claims_above_the_rewind_point(tmp_path):
    store = Store(tmp_path / "s.db")
    store.add_intent(VAULT, H1, CALL_HEX, "x", 90)
    store.add_intent(VAULT, H2, CALL_HEX, "y", 90)
    assert store.match_intent(VAULT, H1, HOT, 95) and store.match_intent(VAULT, H2, HOT, 99)
    store.rewind(97)
    assert store.match_intent(VAULT, H2, HOT, 98) is not None  # released, re-claimed by the re-included one
    assert store.match_intent(VAULT, H1, HOT, 98) is None  # #95 is still canonical: its claim stands
