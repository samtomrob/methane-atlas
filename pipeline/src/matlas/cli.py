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

    p_plumes = sub.add_parser(
        "plumes", help="fetch point-source plume detections from every configured provider"
    )
    p_plumes.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "data" / "plumes.geojson",
        help="output GeoJSON",
    )
    p_plumes.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "data",
        help="dir holding the infrastructure layers used for association",
    )

    p_s2 = sub.add_parser(
        "s2-detect", help="detect methane plumes ourselves from Sentinel-2 imagery"
    )
    p_s2.add_argument("--region", default="png", help="site group (default png)")
    p_s2.add_argument("--site", default=None, help="restrict to one site key")
    p_s2.add_argument("--start", default=None, help="start date (default 1 year back)")
    p_s2.add_argument("--end", default=None, help="end date (default today)")
    p_s2.add_argument(
        "--max-scenes", type=int, default=14, help="cap scenes per site (least cloudy first)"
    )
    p_s2.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "data" / "s2_detections.geojson",
        help="output GeoJSON",
    )

    p_emit = sub.add_parser(
        "emit-scan",
        help="read the existing EMIT methane archive over PNG facilities (observability audit)",
    )
    p_emit.add_argument("--limit", type=int, default=None, help="cap granules per site")
    p_emit.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "data" / "png_emit_scan.json",
        help="output JSON",
    )

    p_clim = sub.add_parser(
        "climatology",
        help="average every composite into a long-term enhancement layer (low-noise)",
    )
    p_clim.add_argument(
        "--rasters", type=Path, default=REPO_ROOT / "data" / "rasters", help="composite dir"
    )
    p_clim.add_argument("--kind", choices=("week", "month"), default="month")

    p_hot = sub.add_parser(
        "hotspots",
        help="find methane enhancement that persists across months and match it to infrastructure",
    )
    p_hot.add_argument(
        "--rasters", type=Path, default=REPO_ROOT / "data" / "rasters", help="composite dir"
    )
    p_hot.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "data",
        help="dir holding the infrastructure GeoJSON layers",
    )
    p_hot.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "data" / "hotspots.json",
        help="output file",
    )
    p_hot.add_argument("--kind", choices=("week", "month"), default="month")

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
    elif args.command == "plumes":
        from . import plumes

        plumes.run(args.out, args.data)
    elif args.command == "s2-detect":
        import datetime as dt

        from . import s2detect

        s2detect.run(
            args.out,
            region=args.region,
            start=dt.date.fromisoformat(args.start) if args.start else None,
            end=dt.date.fromisoformat(args.end) if args.end else None,
            max_scenes=args.max_scenes,
            only=args.site,
        )
    elif args.command == "emit-scan":
        from . import emitscan
        from .s2detect import PNG_SITES

        emitscan.run(
            {s.name: (s.lon, s.lat) for s in PNG_SITES},
            args.out,
            limit=args.limit,
        )
    elif args.command == "climatology":
        from . import climatology

        climatology.run(args.rasters, kind=args.kind)
    elif args.command == "hotspots":
        from . import hotspots

        hotspots.run(args.rasters, args.data, args.out, kind=args.kind)
    elif args.command == "infra":
        from . import infra

        counts = infra.run(args.out)
        for layer, n in counts.items():
            print(f"{layer}: {n} features")
        print(f"written to {args.out}")


if __name__ == "__main__":
    main()
