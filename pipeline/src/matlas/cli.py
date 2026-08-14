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

    sub.add_parser(
        "gee-login",
        help="sign in to Earth Engine interactively (optional; unused by default)",
    )

    p_tropomi = sub.add_parser(
        "tropomi", help="build TROPOMI methane composites from Sentinel-5P L2"
    )
    p_tropomi.add_argument(
        "--start", default="2025-01-01", help="inclusive start date (default 2025-01-01)"
    )
    p_tropomi.add_argument("--end", default=None, help="exclusive end date (default today)")
    p_tropomi.add_argument(
        "--kind", choices=("week", "month"), default="week", help="compositing period"
    )
    p_tropomi.add_argument(
        "--limit", type=int, default=None, help="cap granules per period (for pilot runs)"
    )
    p_tropomi.add_argument(
        "--out", type=Path, default=REPO_ROOT / "data" / "rasters", help="output dir"
    )

    p_render = sub.add_parser(
        "render", help="render methane composites to PNG overlays for the web app"
    )
    p_render.add_argument(
        "--rasters", type=Path, default=REPO_ROOT / "data" / "rasters", help="composite dir"
    )
    p_render.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "data" / "methane",
        help="output dir",
    )
    p_render.add_argument("--kind", choices=("week", "month"), default="month")

    args = parser.parse_args(argv)

    if args.command == "probe":
        from . import probe

        sys.exit(probe.run(args.catalog))
    elif args.command == "auth-check":
        from . import auth

        sys.exit(auth.run())
    elif args.command == "gee-login":
        from . import auth

        sys.exit(auth.gee_login())
    elif args.command == "tropomi":
        import datetime as dt

        from . import tropomi

        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
        tropomi.run(args.out, start, end, kind=args.kind, limit=args.limit)
    elif args.command == "render":
        from . import render

        render.run(args.rasters, args.out, kind=args.kind)
    elif args.command == "infra":
        from . import infra

        counts = infra.run(args.out)
        for layer, n in counts.items():
            print(f"{layer}: {n} features")
        print(f"written to {args.out}")


if __name__ == "__main__":
    main()
