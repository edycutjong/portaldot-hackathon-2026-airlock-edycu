"""Where the CLI's signing keys come from — never from the command line itself.

A key is named by a spec:
  file:<path>   the file holds a mnemonic or secret URI (e.g. ~/.config/airlock/cold.seed, mode 600)
  env:<VAR>     the environment variable holds it
  //Name        a public dev derivation (//Alice, //AirlockDemo//hot): accepted ONLY on an interim dev chain, where
                every such key is known to everyone and protects nothing (a file or variable holding one, or the
                public dev phrase itself, is refused elsewhere too)

A seed typed as an argument ends up in shell history and `ps` output, so a bare mnemonic is refused. The CLI reads
the default specs from $AIRLOCK_COLD_KEY / $AIRLOCK_HOT_KEY, so the veto command printed in an alert works as pasted.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from substrateinterface import Keypair
from substrateinterface.constants import DEV_PHRASE

from .chain_profile import Profile


class KeyRefused(ValueError):
    """The key spec is malformed, unreadable, or not allowed on this chain."""


def is_dev_uri(spec: str) -> bool:
    return spec.startswith("//")


def secret_of(spec: str, profile: Profile) -> str:
    """The secret URI a spec names, after the policy checks (no keypair is built here)."""
    spec = spec.strip()
    if is_dev_uri(spec):
        if not profile.is_interim:
            raise KeyRefused(f"{spec} is a public dev key; {profile.label} is not an interim dev chain — "
                             f"use file:<path> or env:<VAR>")
        return spec
    kind, _, where = spec.partition(":")
    if kind == "file" and where:
        path = Path(where).expanduser()
        try:
            mode = path.stat().st_mode
            secret = path.read_text().strip()
        except OSError as e:
            raise KeyRefused(f"cannot read key file {path}: {e.strerror or e}") from e
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"warning: key file {path} is readable by other users; chmod 600 it", file=sys.stderr)
    elif kind == "env" and where:
        secret = os.environ.get(where, "").strip()
        if not secret:
            raise KeyRefused(f"environment variable {where} is empty or unset")
    else:
        words = len(spec.split())
        hint = " (that looks like a seed phrase: it would end up in shell history)" if words >= 12 else ""
        raise KeyRefused(f"key spec must be file:<path>, env:<VAR> or a //dev URI on an interim chain{hint}")
    if (is_dev_uri(secret) or " ".join(secret.split()).startswith(DEV_PHRASE)) and not profile.is_interim:
        raise KeyRefused(f"{spec} holds a public dev derivation; {profile.label} is not an interim dev chain")
    return secret


def load_key(spec: str | None, profile: Profile, role: str) -> Keypair:
    if not spec:
        raise KeyRefused(f"no {role} key: pass --{role} file:<path> (or set $AIRLOCK_{role.upper()}_KEY)")
    secret = secret_of(spec, profile)
    try:
        return Keypair.create_from_uri(secret)
    except Exception as e:  # the library's message can echo the secret: never pass it on
        raise KeyRefused(f"{role} key from {spec.split(':', 1)[0] if ':' in spec else 'dev URI'} is not a valid "
                         f"mnemonic or secret URI ({type(e).__name__})") from None
