"""matlas CLI — every pipeline stage is a subcommand so CI, agents and
humans run identical entry points."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="matlas", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="check every data-catalog URL is alive")
    p_probe.add_argument(
        "--catalog", type=Path, default=REPO_ROOT / "data-catalog", help="catalog dir"
    )

    p_infra = sub.add_parser("infra", help="fetch + compile infrastructure layers (v0)")
    p_infra.add_argument(
        "--out", type=Path, default=REPO_ROOT / "web" / "public" / "data", help="output dir"
    )

    sub.add_parser(
        "auth-check",
        help="verify configured credentials actually work (never prints secret values)",
    )

    args = parser.parse_args(argv)

    if args.command == "probe":
        from . import probe

        sys.exit(probe.run(args.catalog))
    elif args.command == "auth-check":
        from . import auth

        sys.exit(auth.run())
    elif args.command == "infra":
        from . import infra

        counts = infra.run(args.out)
        for layer, n in counts.items():
            print(f"{layer}: {n} features")
        print(f"written to {args.out}")


if __name__ == "__main__":
    main()
