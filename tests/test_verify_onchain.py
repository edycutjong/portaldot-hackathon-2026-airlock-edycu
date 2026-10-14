"""scripts/verify_onchain.py's reading of PROOF.md: which rows it checks, how a label maps to a profile, what counts
as a mismatch. The chain side is airlock.receipts.refetch (tested in test_receipts.py)."""

from types import SimpleNamespace

from tests.test_seed_fixtures import load_script

v = load_script("verify_onchain")

PROOF = """
### spike_proxy — t — interim — not V3

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `13907-2` | `0xb1` | `0xt1` | interim-sdk — not V3 |  |
| 4 | hot spends | `Proxy.proxy` | hot | Proxy.Unannounced | Proxy.Unannounced | `13910-2` | `0xb4` | `0xt4` | interim-sdk — not V3 | I1 |

Some prose with a | pipe | that is not a table row.

### re-fetch — t — 8565-1 on interim-1002

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `8565-1` | `Proxy.announce` | ok | `0xt9` | interim-1002 — not V3 |
| `n/a` | x | ok | `0x` | interim-1002 — not V3 |
"""


def test_rows_with_an_extrinsic_id_and_their_columns():
    rows = v.proof_rows(PROOF)
    assert [r["extrinsic_id"] for r in rows] == ["13907-2", "13910-2", "8565-1"]
    assert rows[0]["profile"] == "interim-sdk" and rows[0]["block_hash"] == "0xb1" and rows[0]["extrinsic_hash"] == "0xt1"
    assert rows[1]["outcome"] == "Proxy.Unannounced"
    assert rows[2]["profile"] == "interim-1002" and rows[2]["block_hash"] is None  # that table has no block hash column


def test_profile_labels():
    assert v.profile_of("interim-sdk — not V3") == "interim-sdk"
    assert v.profile_of("`v3`") == "v3"
    assert v.profile_of("Portaldot V3") is None and v.profile_of("") is None


def test_check_reports_each_differing_field_only_when_recorded():
    got = SimpleNamespace(block_hash="0xb1", extrinsic_hash="0xT1", outcome="ok")
    assert v.check({"block_hash": "0xb1", "extrinsic_hash": "0xt1", "outcome": "ok"}, got) == []
    assert v.check({"block_hash": None, "extrinsic_hash": "0xt1", "outcome": None}, got) == []
    diffs = v.check({"block_hash": "0xbX", "extrinsic_hash": "0xt1", "outcome": "Proxy.Unannounced"}, got)
    assert len(diffs) == 2 and diffs[0].startswith("block_hash") and diffs[1].startswith("outcome")


def test_evidence_rows_take_the_file_profile_and_genesis(tmp_path):
    import json

    f = tmp_path / "bench.json"
    f.write_text(json.dumps({"profile": "interim-sdk", "genesis_hash": "0xg", "started": "t", "rows": [
        {"extrinsic_id": "5-2", "block_hash": "0xb", "extrinsic_hash": "0xt", "outcome": "ok",
         "profile": "interim-sdk — not V3"},
        {"extrinsic_id": "6-2", "block_hash": "0xb6", "extrinsic_hash": "0xt6", "outcome": "Proxy.Unannounced"}]}))
    rows = v.evidence_rows(f)
    assert [r["profile"] for r in rows] == ["interim-sdk", "interim-sdk"] and rows[1]["genesis_hash"] == "0xg"


def test_a_row_with_an_id_in_an_unknown_column_is_reported_not_skipped():
    text = "### x\n\n| id | call |\n|---|---|\n| `123-2` | Proxy.announce |\n| 2026-10-14 | a date, not an id |\n"
    rows = v.proof_rows(text)
    assert len(rows) == 1 and rows[0]["unparsed"] and rows[0]["extrinsic_id"] is None


def _run(monkeypatch, tmp_path, connect_error, skip):
    proof = tmp_path / "PROOF.md"
    proof.write_text(PROOF)

    def fake_connect(url, profile):
        raise connect_error

    monkeypatch.setattr(v, "connect", fake_connect)
    monkeypatch.setattr(v, "ROOT", tmp_path)
    (tmp_path / "evidence").mkdir()
    return v.main([str(proof)] + (["--skip-unreachable"] if skip else []))


def test_label_mismatch_fails_even_with_skip_unreachable(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, v.ProfileMismatch("endpoint is interim-1002"), skip=True) == 1


def test_all_unreachable_and_skipped_is_not_a_pass(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, v.ChainUnreachable("down"), skip=True) == 1
