"""Which chain are we talking to, and how does its Proxy pallet spell things?

Airlock targets the Portaldot V3 testnet. While V3 is offline it runs on a labelled interim chain,
and every piece of evidence carries the profile label so an interim transaction is never presented
as a V3 one. A profile is picked by genesis hash + runtime `:code` hash; an unknown chain, or a known
genesis running different code, is refused rather than guessed at.
"""

from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from decimal import Context, Decimal, InvalidOperation
from typing import Any, Mapping

CONNECT_TIMEOUT_S = 15  # handshake + identification only; cleared once connected so subscriptions never time out
U128_MAX = 2**128 - 1
CODE_KEY = "0x3a636f6465"  # the well-known storage key ":code" (the runtime wasm)


class UnknownChain(RuntimeError):
    """The endpoint's genesis/code hash matches no known profile."""


class ChainUnreachable(UnknownChain):
    """Nothing answered at the endpoint, so the chain cannot be identified (e.g. V3 while its RPC is offline)."""


class ProfileMismatch(RuntimeError):
    """The chain was identified, but not as the profile the caller asked for."""


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    genesis_hash: str | None  # None = not filled yet (v3 until gate G2)
    code_hashes: tuple[str, ...]
    spec: tuple[str, int] | None
    default_endpoint: str | None
    # Proxy pallet naming: modern `create_pure`/`PureCreated` vs the 2021 `anonymous`/`AnonymousCreated`.
    create_pure_call: str = "create_pure"
    kill_pure_call: str = "kill_pure"
    pure_created_event: str = "PureCreated"
    pure_field: str = "pure"
    # substrate-interface type registry preset; only the 1002 runtime needs one.
    type_registry_preset: str | None = None
    # substrate-interface type overrides on top of the preset, keyed by the metadata's exact type string.
    type_registry_types: Mapping[str, str] = field(default_factory=dict)
    # @polkadot/api type overrides (UI side); only the 1002 runtime needs one.
    polkadotjs_types: Mapping[str, str] = field(default_factory=dict)
    # Used only when the chain's system_properties carry no tokenDecimals.
    decimals_fallback: int | None = None
    symbol_fallback: str | None = None
    is_interim: bool = True


# Everything below except the label is UNVERIFIED until gate G2 runs against the live chain: endpoint (baseline V1),
# 14 decimals / tPOTv3 (V2, documented), runtime portaldotV3/3000 (V4, third-party measurement).
V3 = Profile(
    id="v3",
    label="Portaldot V3 testnet",
    genesis_hash=None,  # filled at the V3 day-0 check (G2); until then nothing can match it
    code_hashes=(),
    spec=("portaldotV3", 3000),
    default_endpoint="wss://testnetv3-node.feso-apps.xyz",
    decimals_fallback=14,
    symbol_fallback="tPOTv3",
    is_interim=False,
)

INTERIM_SDK = Profile(
    id="interim-sdk",
    label="interim — not V3 (upstream polkadot-sdk substrate-node dev chain)",
    genesis_hash="0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2",
    code_hashes=("0x72e84be232be151b059440f89710895de0cb6b71b6529f97e9c02340765f1ec0",),
    spec=("node", 268),
    default_endpoint="ws://127.0.0.1:29944",
    symbol_fallback="UNIT",
)

INTERIM_1002 = Profile(
    id="interim-1002",
    label="interim — not V3 (Portaldot dev node, runtime spec 1002)",
    genesis_hash="0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22",
    code_hashes=("0x8c0d4d9d638ad05e7d214e1496321cea00274698e4bcf5b2006c3eaf86b7fa80",),
    spec=("portaldot", 1002),
    default_endpoint="ws://127.0.0.1:19944",
    create_pure_call="anonymous",
    kill_pure_call="kill_anonymous",
    pure_created_event="AnonymousCreated",
    pure_field="anonymous",
    type_registry_preset="legacy",
    # The 1002 metadata spells Proxy storage values with stray spaces/commas the legacy preset cannot parse.
    type_registry_types={
        "(BoundedVec<ProxyDefinition<AccountId, ProxyType,  BlockNumber>, MaxProxies,>, BalanceOf)":
            "(Vec<ProxyDefinition>, BalanceOf)",
        "(BoundedVec<Announcement<AccountId, CallHashOf,  BlockNumber>, MaxPending,>, BalanceOf,)":
            "(Vec<ProxyAnnouncement>, BalanceOf)",
    },
    polkadotjs_types={"DispatchErrorModule": "DispatchErrorModuleU8"},
    decimals_fallback=14,  # the dev node exposes empty system_properties; mainnet POT has 14
    symbol_fallback="POT",
)

PROFILES: dict[str, Profile] = {p.id: p for p in (V3, INTERIM_SDK, INTERIM_1002)}

# Every Proxy call Airlock's flow uses, by the profile's spelling of create_pure.
REQUIRED_PROXY_CALLS = ("proxy", "add_proxy", "remove_proxy", "announce", "remove_announcement",
                        "reject_announcement", "proxy_announced")


def identify(genesis_hash: str, code_hash: str) -> Profile:
    """Map a chain's genesis hash + runtime code hash to its profile, or raise UnknownChain."""
    if not genesis_hash or not code_hash:
        raise UnknownChain(f"chain did not report a genesis hash ({genesis_hash!r}) and runtime code hash ({code_hash!r})")
    genesis_hash, code_hash = genesis_hash.lower(), code_hash.lower()
    for profile in PROFILES.values():
        if profile.genesis_hash is None or profile.genesis_hash != genesis_hash:
            continue
        if code_hash not in profile.code_hashes:
            raise UnknownChain(
                f"genesis {genesis_hash} is {profile.id}, but its runtime code hash {code_hash} is not "
                f"one Airlock has verified ({', '.join(profile.code_hashes)}); re-check the profile before use"
            )
        return profile
    raise UnknownChain(f"no Airlock profile for genesis {genesis_hash} (code {code_hash})")


def missing_proxy_calls(profile: Profile, call_names: list[str] | set[str]) -> list[str]:
    """Proxy calls the flow needs that this runtime's metadata does not list."""
    have = set(call_names)
    need = (*REQUIRED_PROXY_CALLS, profile.create_pure_call)
    return [c for c in need if c not in have]


def transferable(account_data: Mapping[str, int], existential_deposit: int = 0, keep_alive: bool = False) -> int:
    """Balance that can leave the account: the amount at risk if a delegate key is stolen.

    Mirrors pallet_balances' `reducible_balance` for both AccountData layouts:
      - modern (`frozen`, `flags`): untouchable = max(frozen - reserved, 0), raised to ED when keeping alive
      - 1002-era (`misc_frozen`, `fee_frozen`): liquid = free - max(misc_frozen, fee_frozen); when keeping alive,
        ED is satisfied first by what is already locked or reserved: liquid - max(ED - (total - liquid), 0)
    """
    free = int(account_data["free"])
    reserved = int(account_data.get("reserved", 0))
    if "frozen" in account_data:
        untouchable = max(int(account_data["frozen"]) - reserved, 0)
        if keep_alive:
            untouchable = max(untouchable, existential_deposit)
        return max(free - untouchable, 0)
    if "misc_frozen" in account_data:
        liquid = max(free - max(int(account_data["misc_frozen"]), int(account_data["fee_frozen"])), 0)
        if keep_alive:
            must_remain = max(existential_deposit - (free + reserved - liquid), 0)
            liquid = max(liquid - must_remain, 0)
        return liquid
    raise ValueError(f"unrecognised AccountData layout: {sorted(account_data)}")


def event_field(attributes: Any, name: str, position: int) -> Any:
    """Read one event field whatever the decoder produced.

    Modern metadata decodes attributes as a dict keyed by field name. The 1002 runtime under the
    `legacy` preset decodes them positionally, each wrapped as {"type": ..., "value": ...}.
    """
    if isinstance(attributes, Mapping):
        value = attributes[name]
    elif isinstance(attributes, (list, tuple)) and len(attributes) > position:
        value = attributes[position]
    else:
        raise ValueError(f"no field {name!r} (position {position}) in {attributes!r}")
    if isinstance(value, Mapping) and set(value) == {"type", "value"}:
        value = value["value"]
    return value


def pure_address(profile: Profile, attributes: Any) -> str:
    """The new vault address from a PureCreated / AnonymousCreated event: (pure, who, proxy_type, index)."""
    return event_field(attributes, profile.pure_field, 0)


@dataclass
class Chain:
    """A live connection plus everything the profile resolved about it."""

    substrate: Any
    profile: Profile
    url: str
    genesis_hash: str
    code_hash: str
    spec_name: str
    spec_version: int
    decimals: int
    symbol: str
    block_time_ms: int | None
    block_time_source: str  # "consensus constant" | "measured (median Timestamp.Now delta)" | "unknown"
    has_revive: bool
    existential_deposit: int

    def units(self, amount: float | int | str | Decimal) -> int:
        """Whole tokens -> planck, exactly; refuses negative, non-finite and sub-planck amounts."""
        return to_planck(amount, self.decimals)

    def fmt(self, planck: int) -> str:
        return f"{planck / 10**self.decimals:,.4f} {self.symbol}"


_EXACT = Context(prec=100)  # far beyond any u128 balance, so no rounding ever happens


def to_planck(amount: float | int | str | Decimal, decimals: int) -> int:
    text = str(amount)
    try:
        if "_" in text:
            raise InvalidOperation
        value = Decimal(text)
        if not value.is_finite() or value < 0:
            raise ValueError(f"amount must be a finite, non-negative number: {amount!r}")
        planck = value.scaleb(decimals, context=_EXACT)
        exact = planck == planck.to_integral_value(context=_EXACT)
    except (InvalidOperation, ArithmeticError):
        raise ValueError(f"not a usable amount: {amount!r}") from None
    if not exact:
        raise ValueError(f"{amount} has more than {decimals} decimal places")
    if planck > U128_MAX:
        raise ValueError(f"{amount} exceeds the u128 balance range")
    return int(planck)


def median_block_time_ms(timestamps_ms: list[int]) -> int | None:
    """Median gap between consecutive block timestamps."""
    gaps = [b - a for a, b in zip(timestamps_ms, timestamps_ms[1:]) if b > a]
    return int(statistics.median(gaps)) if gaps else None


def _block_time(substrate, span: int = 10) -> tuple[int | None, str]:
    """Consensus constant if the runtime exposes one, else measured from the last `span` blocks."""
    for pallet, const in (("Babe", "ExpectedBlockTime"), ("Aura", "SlotDuration")):
        try:
            c = substrate.get_constant(pallet, const)
        except Exception:
            c = None
        if c is not None and c.value:
            return int(c.value), "consensus constant"
    head = substrate.get_block_number(None)
    stamps = []
    for n in range(max(head - span, 1), head + 1):
        now = substrate.query("Timestamp", "Now", block_hash=substrate.get_block_hash(n))
        stamps.append(int(now.value))
    measured = median_block_time_ms(stamps)
    return (measured, "measured (median Timestamp.Now delta)") if measured else (None, "unknown")


def connect(url: str | None = None, profile_id: str | None = None) -> Chain:
    """Connect, identify the chain, and fail loudly if it is unknown or not the requested profile.

    `profile_id` defaults to $AIRLOCK_PROFILE; `url` to $AIRLOCK_WSS, then the profile's default endpoint.
    """
    from substrateinterface import SubstrateInterface

    profile_id = profile_id or os.environ.get("AIRLOCK_PROFILE") or None
    if profile_id is not None and profile_id not in PROFILES:
        raise ProfileMismatch(f"unknown profile {profile_id!r}; known: {', '.join(PROFILES)}")
    url = url or os.environ.get("AIRLOCK_WSS") or (PROFILES[profile_id].default_endpoint if profile_id else None)
    if not url:
        raise ProfileMismatch("no endpoint: pass url, set AIRLOCK_WSS, or pick a profile with a default endpoint")

    def open_(**kwargs):
        try:
            return SubstrateInterface(url=url, ws_options={"timeout": CONNECT_TIMEOUT_S}, **kwargs)
        except Exception as e:
            raise ChainUnreachable(f"no node answered at {url}: {type(e).__name__}: {e}") from e

    # Identify with raw RPC first: decoding needs the right preset, and the preset depends on the chain.
    probe = open_()
    try:
        genesis = probe.rpc_request("chain_getBlockHash", [0]).get("result")
        code_hash = probe.rpc_request("state_getStorageHash", [CODE_KEY]).get("result")
        profile = identify(genesis, code_hash)
        if profile_id is not None and profile.id != profile_id:
            raise ProfileMismatch(f"{url} is {profile.id} ({profile.label}), not the requested {profile_id}")
    except (UnknownChain, ProfileMismatch):
        probe.close()
        raise
    except Exception as e:
        probe.close()
        raise UnknownChain(f"could not identify the chain at {url}: {type(e).__name__}: {e}") from e

    if profile.type_registry_preset:
        probe.close()
        substrate = open_(type_registry_preset=profile.type_registry_preset,
                          type_registry={"types": dict(profile.type_registry_types)})
    else:
        substrate = probe
    try:
        chain = _resolve(substrate, profile, url, genesis, code_hash)
    except UnknownChain:
        substrate.close()
        raise
    except Exception as e:
        substrate.close()
        raise ChainUnreachable(f"{profile.id} at {url} stopped answering while connecting: {type(e).__name__}: {e}") from e
    _clear_timeout(substrate)
    return chain


def _clear_timeout(substrate) -> None:
    """Connected: drop the handshake timeout, on this socket and on any socket a reconnect would open."""
    options = getattr(substrate, "ws_options", None)
    if isinstance(options, dict):
        options.pop("timeout", None)
    websocket = getattr(substrate, "websocket", None)
    if websocket is not None:
        websocket.settimeout(None)


def _resolve(substrate, profile: Profile, url: str, genesis: str, code_hash: str) -> Chain:
    """Check the identified runtime has what Airlock needs and read its parameters."""
    substrate.init_runtime()
    version = substrate.rpc_request("state_getRuntimeVersion", [])["result"]
    if profile.spec and (version["specName"], version["specVersion"]) != profile.spec:
        raise UnknownChain(f"{profile.id} expects runtime {profile.spec}, chain reports "
                           f"({version['specName']}, {version['specVersion']})")

    metadata = substrate.get_metadata()
    pallets = {p.name: p for p in metadata.pallets}
    if "Proxy" not in pallets:
        raise UnknownChain(f"{profile.id}: runtime has no Proxy pallet")
    missing = missing_proxy_calls(profile, [c.name for c in pallets["Proxy"].calls or []])
    if missing:
        raise UnknownChain(f"{profile.id}: Proxy pallet lacks {missing}")

    props = substrate.properties or {}
    decimals = props.get("tokenDecimals")
    if isinstance(decimals, list):
        decimals = decimals[0]
    if decimals is None:
        decimals = profile.decimals_fallback
    if decimals is None:
        raise UnknownChain(f"{profile.id}: chain reports no tokenDecimals and the profile has no fallback")
    symbol = props.get("tokenSymbol") or profile.symbol_fallback or "UNIT"
    if isinstance(symbol, list):
        symbol = symbol[0]

    block_time_ms, block_time_source = _block_time(substrate)
    return Chain(
        substrate=substrate,
        profile=profile,
        url=url,
        genesis_hash=genesis,
        code_hash=code_hash,
        spec_name=version["specName"],
        spec_version=version["specVersion"],
        decimals=int(decimals),
        symbol=symbol,
        block_time_ms=block_time_ms,
        block_time_source=block_time_source,
        has_revive="Revive" in pallets,
        existential_deposit=int(substrate.get_constant("Balances", "ExistentialDeposit").value),
    )
