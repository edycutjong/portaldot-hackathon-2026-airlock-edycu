"""Did an extrinsic actually do what it was sent to do?

`ExtrinsicSuccess` alone is not enough. A delegate's `proxy.proxy(...)` can fail two ways:
  (a) the outer dispatch fails — `System.ExtrinsicFailed` (e.g. `Proxy.Unannounced`, `Proxy.NotProxy`);
  (b) the outer call succeeds but the proxied call fails — `Proxy.ProxyExecuted { result: Err(..) }`.
Reading (b) as success would turn a failed veto into a fake one, so both are checked, and a ProxyExecuted result that
is not a clear `Ok` is reported as a failure. A successful extrinsic that emits no ProxyExecuted (any non-proxy call)
is judged by the outer result alone.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .chain_profile import event_field

OK = "ok"


def events(receipt: Any) -> list[tuple[str, str, Any]]:
    return [(e.value["module_id"], e.value["event_id"], e.value["attributes"]) for e in receipt.triggered_events]


def find_event(receipt: Any, module: str, event: str) -> Any:
    for m, e, attrs in events(receipt):
        if (m, e) == (module, event):
            return attrs
    return None


def _is_ok(result: Any) -> bool:
    return result == "Ok" or (isinstance(result, dict) and set(result) == {"Ok"})


ErrorNamer = Callable[[int, Any], str]


def _error_index(error: Any) -> int:
    """Module error index: a u8 on old runtimes, the first byte of a 4-byte array on modern ones."""
    if isinstance(error, int):
        return error
    if isinstance(error, str) and error.startswith("0x"):
        return bytes.fromhex(error[2:])[0]
    if isinstance(error, (bytes, bytearray, list, tuple)):
        return int(error[0])
    raise ValueError(f"unrecognised module error {error!r}")


def describe_error(err: Any, name_error: ErrorNamer) -> str:
    """`Pallet.Error` for a module error, else the DispatchError variant (`BadOrigin`, `Token`, …)."""
    if isinstance(err, dict) and "Module" in err:
        module = err["Module"]
        try:
            return name_error(int(module["index"]), module["error"])
        except (KeyError, TypeError, ValueError, IndexError):
            return f"Module.unreadable:{module!r}"
    if isinstance(err, dict) and len(err) == 1:
        variant, detail = next(iter(err.items()))
        return f"{variant}.{detail}" if isinstance(detail, str) else variant  # e.g. Token.FundsUnavailable
    return str(err)


def outcome(receipt: Any, name_error: ErrorNamer) -> str:
    """'ok', 'Pallet.Error' for an outer failure, or 'inner:Pallet.Error' for a failed proxied call."""
    if not receipt.is_success:
        failed = find_event(receipt, "System", "ExtrinsicFailed")
        if failed is None:
            return "failed:no-ExtrinsicFailed-event"
        return describe_error(event_field(failed, "dispatch_error", 0), name_error)
    for m, e, attrs in events(receipt):
        if (m, e) == ("Proxy", "ProxyExecuted"):
            result = event_field(attrs, "result", 0)
            if not _is_ok(result):
                err = result.get("Err") if isinstance(result, dict) else result
                return f"inner:{describe_error(err, name_error)}"
    return OK


def error_namer(pallets: Iterable[Any]) -> ErrorNamer:
    """(pallet index, module error) -> 'Pallet.Error', from runtime metadata pallets."""
    table: dict[int, tuple[str, list[str]]] = {}
    for p in pallets:
        index = p["index"]
        index = int(getattr(index, "value", index))
        table[index] = (p.name, [e.name for e in (p.errors or [])])

    def name(index: int, error: Any) -> str:
        pallet, errors = table.get(index, (f"pallet#{index}", []))
        i = _error_index(error)
        return f"{pallet}.{errors[i] if i < len(errors) else f'error#{i}'}"

    return name
