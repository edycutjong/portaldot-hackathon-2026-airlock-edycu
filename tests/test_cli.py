"""The CLI surface: argument contract, refusals before any connection or signature, and that the veto command the
watcher's alert prints is one this CLI accepts as pasted."""

import shlex

import pytest

from airlock import cli
from airlock.alerts import NOT_YOURS, Alert
from airlock.chain_profile import UnknownChain
from airlock.ops import Refused

VAULT = "5Hb37SqmVJSzREN7ziBw1ZQJWLgDS7DLaEjKisf8a6KUYsLv"
HOT = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
H = "0x" + "ab" * 32


def no_connect(*a, **k):
    raise AssertionError("must not connect")


def test_alert_veto_command_is_valid_cli_input():
    alert = Alert(verdict=NOT_YOURS, profile_id="interim-sdk", profile_label="x", is_interim=True, vault=VAULT,
                  delegate=HOT, call_hash=H, announced_block=1, block_hash="0x", release_block=6, head=2,
                  block_time_ms=3000, at_risk="1 UNIT", locked=None)
    argv = shlex.split(alert.veto_command())
    assert argv[0] == "airlock"
    args = cli.parser().parse_args(argv[1:])
    assert (args.command, args.vault, args.delegate, args.hash) == ("veto", VAULT, HOT, H)


def test_cold_and_hot_default_from_env(monkeypatch):
    monkeypatch.setenv("AIRLOCK_COLD_KEY", "file:/k/cold")
    monkeypatch.setenv("AIRLOCK_HOT_KEY", "env:HOT")
    assert cli.parser().parse_args(["veto", "--vault", VAULT, "--delegate", HOT, "--hash", H]).cold == "file:/k/cold"
    assert cli.parser().parse_args(["announce", "--vault", VAULT]).hot == "env:HOT"


def test_create_needs_exactly_one_delay(capsys):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["create", "--hot", HOT])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["create", "--hot", HOT, "--delay", "5", "--delay-s", "60"])
    assert cli.parser().parse_args(["create", "--hot", HOT, "--delay-s", "60"]).delay_s == 60


def test_zero_delay_refused_before_connecting(monkeypatch, capsys):
    monkeypatch.setattr(cli, "connect", no_connect)
    assert cli.main(["create", "--hot", HOT, "--delay", "0"]) == 2
    assert "at least 1 block" in capsys.readouterr().err


def test_unknown_chain_refused_with_exit_2(monkeypatch, capsys):
    def unknown(*a, **k):
        raise UnknownChain("no Airlock profile for genesis 0xdead")

    monkeypatch.setattr(cli, "connect", unknown)
    assert cli.main(["status", "--vault", VAULT]) == 2
    assert "UnknownChain" in capsys.readouterr().err


def test_address_check():
    assert cli.address(VAULT, "--vault") == VAULT
    with pytest.raises(Refused, match="not an SS58 address"):
        cli.address("0x1234", "--to")


def test_spend_needs_to_and_amount():
    class Args:
        call = None
        to = HOT
        amount = None

    with pytest.raises(Refused, match="--to and --amount"):
        cli.spend_call(None, Args())


def test_json_rows_are_one_object_per_line(capsys):
    out = cli.Out(as_json=True)
    out.say("not printed in json mode")
    out.row({"step": "veto", "outcome": "ok", "expected": "ok"})
    out.result(command="veto")
    lines = capsys.readouterr().out.splitlines()
    assert lines == ['{"tx": {"step": "veto", "outcome": "ok", "expected": "ok"}}', '{"result": {"command": "veto"}}']


# main(): every failure is a clean exit status, never a traceback --------------------------------------------------

def fake_connect(monkeypatch, raise_on_submit=None):
    from tests.test_ops import make

    s, tx = make()
    s.close = lambda: None
    if raise_on_submit is not None:
        def boom(*a, **k):
            raise raise_on_submit
        s.submit_extrinsic = boom
    monkeypatch.setattr(cli, "connect", lambda url, profile: tx.chain)
    return s


def test_veto_end_to_end_through_main(monkeypatch, capsys):
    from tests.test_ops import HOT, VAULT, spend
    s = fake_connect(monkeypatch)
    s.announcements[HOT.ss58_address] = [{"real": VAULT, "call_hash": cli.call_hash(spend(s)), "height": 99}]
    rc = cli.main(["veto", "--cold", "//Alice", "--vault", VAULT, "--delegate", HOT.ss58_address,
                   "--hash", cli.call_hash(spend(s))])
    assert rc == 0 and "VETOED" in capsys.readouterr().out and s.announcements[HOT.ss58_address] == []


def test_nothing_to_veto_is_exit_2(monkeypatch, capsys):
    from tests.test_ops import HOT, VAULT
    fake_connect(monkeypatch)
    assert cli.main(["veto", "--cold", "//Alice", "--vault", VAULT, "--delegate", HOT.ss58_address, "--hash", H]) == 2
    assert "nothing sent" in capsys.readouterr().err


def test_bad_fund_is_refused_not_a_traceback(monkeypatch, capsys):
    fake_connect(monkeypatch)
    assert cli.main(["create", "--cold", "//Alice", "--hot", HOT, "--delay", "5", "--fund", "abc"]) == 2
    assert "--fund 'abc'" in capsys.readouterr().err


@pytest.mark.parametrize("exc, code, text", [
    ("node", 1, "node rejected"),
    ("socket", 1, "outcome unknown"),
    ("ctrl-c", 130, "interrupted"),
])
def test_node_and_connection_failures_map_to_exit_codes(monkeypatch, capsys, exc, code, text):
    from substrateinterface.exceptions import SubstrateRequestException

    from tests.test_ops import HOT, VAULT, spend
    error = {"node": SubstrateRequestException({"code": 1010, "message": "Invalid Transaction"}),
             "socket": BrokenPipeError(32, "Broken pipe"), "ctrl-c": KeyboardInterrupt()}[exc]
    s = fake_connect(monkeypatch, raise_on_submit=error)
    s.announcements[HOT.ss58_address] = [{"real": VAULT, "call_hash": cli.call_hash(spend(s)), "height": 99}]
    rc = cli.main(["veto", "--cold", "//Alice", "--vault", VAULT, "--delegate", HOT.ss58_address,
                   "--hash", cli.call_hash(spend(s))])
    assert rc == code and text in capsys.readouterr().err


def test_unexpected_errors_are_not_swallowed(monkeypatch):
    from tests.test_ops import HOT, VAULT, spend
    s = fake_connect(monkeypatch, raise_on_submit=ZeroDivisionError("bug"))
    s.announcements[HOT.ss58_address] = [{"real": VAULT, "call_hash": cli.call_hash(spend(s)), "height": 99}]
    with pytest.raises(ZeroDivisionError):
        cli.main(["veto", "--cold", "//Alice", "--vault", VAULT, "--delegate", HOT.ss58_address,
                  "--hash", cli.call_hash(spend(s))])
