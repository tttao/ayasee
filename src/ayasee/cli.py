"""Command-line entry point with a tiny subcommand registry.

Later modules add subcommands by calling :func:`register` at import time::

    def setup(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan-json")
        parser.set_defaults(handler=run)      # run(args: argparse.Namespace) -> int

    cli.register("changeset", setup)

``setup`` receives the subparser created for ``name`` and must set a ``handler``
default; ``main`` calls ``handler(args)`` and returns its exit code.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from ayasee import __version__

SetupFn = Callable[[argparse.ArgumentParser], None]

_COMMANDS: dict[str, SetupFn] = {}


def register(name: str, setup: SetupFn) -> None:
    """Register subcommand ``name``; ``setup`` configures its subparser."""
    if name in _COMMANDS:
        raise ValueError(f"subcommand already registered: {name}")
    _COMMANDS[name] = setup


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ayasee", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, setup in _COMMANDS.items():
        setup(sub.add_parser(name))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"ayasee {__version__}")
        return 0
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


def entrypoint() -> None:
    import ayasee.approvals  # noqa: F401  (registers its subcommand)
    import ayasee.binding  # noqa: F401  (registers its subcommand)
    import ayasee.cascade  # noqa: F401  (registers its subcommand)
    import ayasee.changed  # noqa: F401  (registers its subcommand)
    import ayasee.changeset  # noqa: F401  (registers its subcommand)
    import ayasee.cockpit.collect  # noqa: F401  (registers its subcommand)
    import ayasee.config  # noqa: F401  (registers its subcommand)
    import ayasee.doctor  # noqa: F401  (registers its subcommand)
    import ayasee.drift  # noqa: F401  (registers its subcommand)
    import ayasee.features  # noqa: F401  (registers its subcommand)
    import ayasee.findings  # noqa: F401  (registers its subcommand)
    import ayasee.guard  # noqa: F401  (registers its subcommand)
    import ayasee.issues  # noqa: F401  (registers its subcommand)
    import ayasee.ledger  # noqa: F401  (registers its subcommand)
    import ayasee.maintip  # noqa: F401  (registers its subcommand)
    import ayasee.notify  # noqa: F401  (registers its subcommand)
    import ayasee.prcomment  # noqa: F401  (registers its subcommand)
    import ayasee.providerenv  # noqa: F401  (registers its subcommand)
    import ayasee.sarif  # noqa: F401  (registers its subcommand)
    import ayasee.trunkguard  # noqa: F401  (registers its subcommand)

    raise SystemExit(main())
