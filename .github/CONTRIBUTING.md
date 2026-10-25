# Contributing

Airlock is a hackathon project (Portaldot Hacker House 2026); issues and small pull requests are welcome.

## Getting started

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q      # offline suite; live/V3 tests are opt-in and skipped by default
pip install ruff && ruff check .
```

## Before you open a PR

- Keep the change small and focused; add or update a test with it.
- `ruff check .` and `python -m pytest -q` pass locally.
- Never commit keys, seeds, or `.env` files. Demo keys are dev derivation paths on a local chain only.
- CI needs no secrets and never writes to a live chain.

## Reporting bugs / requesting features

Open an issue using the templates. For vulnerabilities, see [SECURITY.md](SECURITY.md) instead.

## Releases

Releases are automatic. Use Angular-style commit prefixes: `fix:`/`perf:` bump the patch version, `feat:` the minor, `feat!:` or a `BREAKING CHANGE:` footer the major. `docs:`, `chore:`, `test:`, `refactor:`, `style:` and `ci:` publish nothing.
