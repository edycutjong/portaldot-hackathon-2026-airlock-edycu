"""The call hash: the only thing `proxy.announce` puts on-chain about the spend it is announcing.

`Proxy.Announcements(delegate)` stores `blake2_256(SCALE(call))`. The watcher sees only that hash, so Airlock
recomputes it from a candidate call and compares (invariant I3). Hash the call the delegate will execute — the inner
call — never the `proxy.proxy(...)` wrapper around it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from substrateinterface.utils.ss58 import ss58_decode


class ReviveUnavailable(LookupError):
    """The runtime has no `Revive.call`, so an EVM-side protected call cannot be composed on this chain."""


@dataclass(frozen=True)
class Announcement:
    real: str
    call_hash: str
    height: int


def blake2_256(data: bytes) -> str:
    return "0x" + hashlib.blake2b(data, digest_size=32).hexdigest()


def call_bytes(call: Any) -> bytes:
    """SCALE bytes of a call given as a composed call object, raw bytes, or 0x-hex."""
    if isinstance(call, (bytes, bytearray)):
        return bytes(call)
    if isinstance(call, str):
        if not call.startswith("0x"):
            raise ValueError("hex call data must start with 0x")
        return bytes.fromhex(call[2:])
    if hasattr(call, "get_used_bytes") and getattr(call, "data", None) is not None:
        # only the bytes this call occupies: a call decoded out of a block shares the block's buffer
        return bytes(call.get_used_bytes())
    raise TypeError(f"not an encoded call: {call!r}")


def call_hash(call: Any) -> str:
    """blake2_256 of the encoded call, as 0x-hex — exactly what proxy.announce stores."""
    return blake2_256(call_bytes(call))


def _field(value: Any, name: str, position: int) -> Any:
    if isinstance(value, dict):
        value = value[name]
    else:
        value = value[position]
    if isinstance(value, dict) and set(value) == {"type", "value"}:
        value = value["value"]
    return value


def normalize_announcements(storage_value: Any) -> list[Announcement]:
    """`Proxy.Announcements(delegate)` -> list of Announcement, from (announcements, deposit) or a bare list."""
    if storage_value is None:
        return []
    entries = storage_value
    if (isinstance(storage_value, (list, tuple)) and len(storage_value) == 2
            and isinstance(storage_value[0], (list, tuple)) and isinstance(storage_value[1], int)):
        entries = storage_value[0]  # (announcements, deposit)
    return [Announcement(real=_field(e, "real", 0), call_hash=_field(e, "call_hash", 1).lower(),
                         height=int(_field(e, "height", 2))) for e in entries]


def pending(substrate: Any, delegate: str) -> list[Announcement]:
    """Live read of a delegate's pending announcements."""
    return normalize_announcements(substrate.query("Proxy", "Announcements", [delegate]).value)


def same_account(a: str, b: str) -> bool:
    """Compare accounts by public key, so the same account under different SS58 prefixes (or as hex) matches."""
    return _public_key(a) == _public_key(b)


def _public_key(account: str) -> str:
    if account.startswith("0x"):
        return account[2:].lower()
    try:
        return ss58_decode(account)
    except ValueError:
        return account  # not an address we can decode; compare as given


def is_announced(announcements: Iterable[Announcement], real: str, call: Any) -> bool:
    """I3: is this exact call announced by the delegate for this vault?"""
    h = call_hash(call)
    return any(a.call_hash == h and same_account(a.real, real) for a in announcements)


def _has_call(runtime: Any, module: str, function: str) -> bool:
    if hasattr(runtime, "has_call"):
        return runtime.has_call(module, function)
    try:
        return runtime.get_metadata_call_function(module, function) is not None
    except Exception:
        return False


def revive_call(runtime: Any, dest_h160: str, value: int, weight_limit: dict, storage_deposit_limit: int,
                data: str | bytes) -> Any:
    """Compose `Revive.call` — an EVM-side spend the vault could be asked to make.

    The gas limits (`weight_limit`, `storage_deposit_limit`) are part of the encoded call, so they are part of the
    announced hash: an announcement commits to them, and executing with different limits is a different call.
    """
    if not _has_call(runtime, "Revive", "call"):
        raise ReviveUnavailable("runtime has no Revive.call (no Revive pallet on this chain)")
    if isinstance(data, (bytes, bytearray)):
        data = "0x" + bytes(data).hex()
    params = {"dest": dest_h160, "value": value, "weight_limit": weight_limit,
              "storage_deposit_limit": storage_deposit_limit, "data": data}
    return runtime.compose_call("Revive", "call", params)
