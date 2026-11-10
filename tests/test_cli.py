import argparse

import ayasee
from ayasee import cli


def test_version_prints_package_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == f"ayasee {ayasee.__version__}\n"


def test_registered_subcommand_is_dispatched(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_COMMANDS", {})

    def setup(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("who")
        parser.set_defaults(handler=lambda args: print(f"hello {args.who}") or 7)

    cli.register("greet", setup)
    assert cli.main(["greet", "world"]) == 7
    assert capsys.readouterr().out == "hello world\n"


def test_no_subcommand_prints_help_and_fails(capsys):
    assert cli.main([]) == 2
    assert "usage: ayasee" in capsys.readouterr().err
