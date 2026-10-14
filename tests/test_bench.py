"""scripts/bench.py's arithmetic: nearest-rank percentiles and the summary it writes (numbers come only from the run)."""

import pytest

from tests.test_seed_fixtures import load_script

bench = load_script("bench")


def test_nearest_rank_percentiles():
    values = list(range(1, 21))  # 1..20
    assert bench.percentile(values, 0.50) == 10 and bench.percentile(values, 0.95) == 19
    assert bench.percentile([5.0], 0.95) == 5.0
    assert bench.percentile([3, 1, 2], 0.5) == 2  # order of the input does not matter
    with pytest.raises(ValueError):
        bench.percentile([], 0.5)


def test_stats_shape():
    s = bench.stats([0.4, 0.2, 0.9, 0.1])
    assert s == {"n": 4, "p50": 0.2, "p95": 0.9, "min": 0.1, "max": 0.9}


def test_markdown_quotes_the_run_numbers_only(tmp_path):
    st = bench.stats([1.0, 2.0])
    summary = {"drains": 2, "alerted_not_yours": 2, "vetoed_then_unannounced": 2, "moved_to_thief": 0,
               "vault_total_before": 10, "vault_total_after": 10, "blocks_to_spare_min": 17,
               **{k: st for k in ("alert_latency_s", "alert_latency_blocks", "veto_inclusion_s", "veto_inclusion_blocks",
                                  "announce_to_veto_s", "announce_to_veto_blocks")}}
    r = {"started": "t", "label": "interim — not V3", "code": "abc", "genesis_hash": "0x0", "block_time_ms": 3000,
         "delay_blocks": 20, "delay_s": 60, "vault": "V", "summary": summary,
         "ablation": {"moved_fmt": "99.0000 UNIT", "moved": 99, "control_total_before_fmt": "100.0000 UNIT",
                      "extrinsics": 1, "id": "5-2", "outcome": "ok", "blocks_after_head": 1}}
    md = bench.markdown(r, bench.ROOT / "evidence" / "bench-x.json")
    assert "2/2 vetoed" in md and "moved to the thief: 0 planck" in md and "| 1.0 s | 2.0 s |" in md
    assert "moved 99.0000 UNIT (99 planck)" in md and "interim — not V3" in md


def test_bench_dev_keys_are_namespaced():
    assert bench.key("cold").ss58_address != bench.key("hot").ss58_address
    assert set(bench.KEYS) >= {"cold", "hot", "thief-payee"}
