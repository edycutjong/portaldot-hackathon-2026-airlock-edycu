"""outcome() is what stops a failed or vetoed transaction from being reported as a success."""

from types import SimpleNamespace

import pytest

from airlock.dispatch import OK, error_namer, outcome
from airlock.offline import load

SUCCESS = ("System", "ExtrinsicSuccess", {"dispatch_info": {}})


def receipt(is_success, *evs):
    return SimpleNamespace(
        is_success=is_success,
        triggered_events=[SimpleNamespace(value={"module_id": m, "event_id": e, "attributes": a}) for m, e, a in evs],
    )


@pytest.fixture(scope="module", params=["interim-sdk", "interim-1002"])
def runtime(request):
    rt = load(request.param)
    proxy = next(int(p["index"].value) for p in rt.metadata.pallets if p.name == "Proxy")
    return SimpleNamespace(namer=error_namer(rt.metadata.pallets), proxy=proxy)


def test_plain_success_is_ok(runtime):
    assert outcome(receipt(True, SUCCESS), runtime.namer) == OK


def test_outer_unannounced_modern_error_bytes(runtime):
    failed = ("System", "ExtrinsicFailed", {"dispatch_error": {"Module": {"index": runtime.proxy, "error": "0x06000000"}}})
    assert outcome(receipt(False, failed), runtime.namer) == "Proxy.Unannounced"


def test_outer_error_legacy_positional_u8(runtime):
    failed = ("System", "ExtrinsicFailed",
              [{"type": "DispatchError", "value": {"Module": {"index": runtime.proxy, "error": 2}}}, {"type": "DispatchInfo", "value": {}}])
    assert outcome(receipt(False, failed), runtime.namer) == "Proxy.NotProxy"


def test_outer_non_module_error(runtime):
    failed = ("System", "ExtrinsicFailed", {"dispatch_error": {"BadOrigin": None}})
    assert outcome(receipt(False, failed), runtime.namer) == "BadOrigin"


def test_failure_without_failed_event_is_still_a_failure(runtime):
    assert outcome(receipt(False), runtime.namer).startswith("failed:")


def test_inner_proxied_call_error_is_not_success(runtime):
    # outer proxy.proxy succeeded, the call it wrapped did not: this must never read as a veto that landed
    inner = ("Proxy", "ProxyExecuted", {"result": {"Err": {"Module": {"index": runtime.proxy, "error": "0x01000000"}}}})
    assert outcome(receipt(True, inner, SUCCESS), runtime.namer) == "inner:Proxy.NotFound"


def test_inner_error_legacy_wrapped(runtime):
    inner = ("Proxy", "ProxyExecuted", [{"type": "DispatchResult", "value": {"Err": {"BadOrigin": None}}}])
    assert outcome(receipt(True, inner, SUCCESS), runtime.namer) == "inner:BadOrigin"


def test_inner_ok_both_shapes(runtime):
    assert outcome(receipt(True, ("Proxy", "ProxyExecuted", {"result": "Ok"}), SUCCESS), runtime.namer) == OK
    assert outcome(receipt(True, ("Proxy", "ProxyExecuted", [{"type": "DispatchResult", "value": {"Ok": None}}]), SUCCESS),
                   runtime.namer) == OK


def test_error_variant_detail_is_kept(runtime):
    failed = ("System", "ExtrinsicFailed", {"dispatch_error": {"Token": "FundsUnavailable"}})
    assert outcome(receipt(False, failed), runtime.namer) == "Token.FundsUnavailable"


def test_unreadable_module_error_is_a_failure_not_a_crash(runtime):
    failed = ("System", "ExtrinsicFailed", {"dispatch_error": {"Module": {"index": runtime.proxy, "error": {"odd": 1}}}})
    assert outcome(receipt(False, failed), runtime.namer).startswith("Module.unreadable")


def test_success_without_proxy_executed_is_judged_by_outer_result(runtime):
    # a plain transfer emits no ProxyExecuted; outcome() does not invent an inner result for it
    transfer = ("Balances", "Transfer", {"from": "a", "to": "b", "amount": 1})
    assert outcome(receipt(True, transfer, SUCCESS), runtime.namer) == OK


def test_unknown_pallet_and_error_index_are_named_not_hidden(runtime):
    failed = ("System", "ExtrinsicFailed", {"dispatch_error": {"Module": {"index": 250, "error": "0x09000000"}}})
    assert outcome(receipt(False, failed), runtime.namer) == "pallet#250.error#9"


@pytest.mark.parametrize("attrs", [{}, []])
def test_malformed_failed_event_is_a_failure_not_a_crash(runtime, attrs):
    assert outcome(receipt(False, ("System", "ExtrinsicFailed", attrs)), runtime.namer) == "failed:unreadable-ExtrinsicFailed"


def test_malformed_proxy_executed_is_not_success(runtime):
    assert outcome(receipt(True, ("Proxy", "ProxyExecuted", {}), SUCCESS), runtime.namer) == "inner:unreadable-ProxyExecuted"
