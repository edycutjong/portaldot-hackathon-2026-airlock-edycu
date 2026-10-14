"""Encode calls against recorded runtime metadata, with no node running.

Used by the unit tests so call-hash checks are reproducible from the repo alone. Fixtures live in tests/fixtures/
(a source checkout, not an installed package) and are written by `scripts/record_metadata.py` from a live chain
identified by `chain_profile`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scalecodec.base import RuntimeConfigurationObject, ScaleBytes
from scalecodec.type_registry import load_type_registry_preset

from .chain_profile import PROFILES, Profile

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


@dataclass
class OfflineRuntime:
    profile: Profile
    spec_version: int
    runtime_config: RuntimeConfigurationObject
    metadata: Any

    def has_call(self, module: str, function: str) -> bool:
        for pallet in self.metadata.pallets:
            if pallet.name == module:
                return any(c.name == function for c in (pallet.calls or []))
        return False

    def compose_call(self, module: str, function: str, params: dict) -> Any:
        if not self.has_call(module, function):
            raise LookupError(f"{self.profile.id} runtime has no {module}.{function}")
        call = self.runtime_config.create_scale_object("Call", metadata=self.metadata)
        call.encode({"call_module": module, "call_function": function, "call_args": params})
        return call


def load(profile_id: str, fixtures: Path = FIXTURES) -> OfflineRuntime:
    """Rebuild a profile's runtime type registry from its recorded metadata fixture."""
    profile = PROFILES[profile_id]
    info = json.loads((fixtures / f"{profile_id}.json").read_text())
    raw = gzip.decompress((fixtures / f"{profile_id}.metadata.scale.gz").read_bytes())
    if hashlib.sha256(raw).hexdigest() != info["metadata_sha256"]:
        raise ValueError(f"{profile_id} metadata fixture does not match its recorded sha256; re-record it")

    rc = RuntimeConfigurationObject()
    rc.update_type_registry(load_type_registry_preset("core"))
    if profile.type_registry_preset:
        rc.update_type_registry(load_type_registry_preset(profile.type_registry_preset))
    if profile.type_registry_types:
        rc.update_type_registry({"types": dict(profile.type_registry_types)})
    rc.set_active_spec_version_id(info["spec_version"])

    metadata = rc.create_scale_object("MetadataVersioned", data=ScaleBytes(raw))
    metadata.decode()
    if metadata.portable_registry:  # metadata V14+
        rc.add_portable_registry(metadata)
    return OfflineRuntime(profile, info["spec_version"], rc, metadata)
