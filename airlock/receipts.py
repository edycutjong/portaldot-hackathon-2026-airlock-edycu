"""Re-read a recorded extrinsic from the chain by its extrinsic id (`block-index`), independently of the run that sent it.

A receipt rebuilt from an id carries no transaction hash, so the hash is recomputed from the extrinsic's bytes in the
block itself. The outcome is judged by the same `dispatch.outcome` the sender used, so a re-fetch can disagree with a
recorded row only if the chain disagrees.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .dispatch import error_namer, outcome


@dataclass(frozen=True)
class Refetched:
    extrinsic_id: str
    block_number: int
    block_hash: str
    extrinsic_hash: str
    call: str
    outcome: str


def parse_extrinsic_id(extrinsic_id: str) -> tuple[int, int]:
    """'85-2' -> (85, 2); anything else is refused."""
    parts = extrinsic_id.split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not an extrinsic id (block-index): {extrinsic_id!r}")
    return int(parts[0]), int(parts[1])


def extrinsic_hash(encoded: bytes) -> str:
    return "0x" + hashlib.blake2b(encoded, digest_size=32).hexdigest()


def _call_name(extrinsic: Any) -> str:
    call = extrinsic.value.get("call", {}) if isinstance(extrinsic.value, Mapping) else {}
    name = f"{call.get('call_module', '?')}.{call.get('call_function', '?')}"
    for arg in call.get("call_args", []):
        inner = arg.get("value") if arg.get("name") == "call" else None
        if isinstance(inner, Mapping) and "call_module" in inner:
            name += f"({inner['call_module']}.{inner['call_function']})"
    return name


def refetch(substrate: Any, extrinsic_id: str, name_error=None) -> Refetched:
    """Look the extrinsic up by id on this connection and judge its outcome from the chain's own events."""
    from substrateinterface.base import ExtrinsicReceipt

    block_number, index = parse_extrinsic_id(extrinsic_id)
    name_error = name_error or error_namer(substrate.get_metadata().pallets)
    receipt = ExtrinsicReceipt.create_from_extrinsic_identifier(substrate, extrinsic_id)
    extrinsics = substrate.get_block(block_hash=receipt.block_hash)["extrinsics"]
    if index >= len(extrinsics):
        raise LookupError(f"block {block_number} has {len(extrinsics)} extrinsics, no index {index}")
    xt = extrinsics[index]
    return Refetched(
        extrinsic_id=extrinsic_id,
        block_number=block_number,
        block_hash=receipt.block_hash,
        extrinsic_hash=extrinsic_hash(bytes(xt.data.data)),
        call=_call_name(xt),
        outcome=outcome(receipt, name_error),
    )


def compare(recorded: Mapping[str, Any], got: Refetched) -> list[str]:
    """Differences between a recorded evidence row and what the chain returns for the same extrinsic id."""
    diffs = []
    for key, value in (("block_hash", got.block_hash), ("extrinsic_hash", got.extrinsic_hash), ("outcome", got.outcome)):
        if str(recorded.get(key, "")).lower() != str(value).lower():
            diffs.append(f"{key}: recorded {recorded.get(key)!r}, chain {value!r}")
    return diffs


def as_dict(r: Refetched) -> dict:
    return asdict(r)
