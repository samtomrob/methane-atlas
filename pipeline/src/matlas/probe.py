"""Probe every URL in data-catalog/ and fail loudly on drift.

Each catalog YAML lists urls with an `expect` status list (default [200]).
Entries with `requires: ENV_VAR` are skipped (reported PENDING) until the
owner supplies that secret — reachability of the auth wall still counts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import yaml

UA = {"User-Agent": "methane-atlas-probe/0.1 (open public-good project)"}


def run(catalog_dir: Path) -> int:
    files = sorted(catalog_dir.glob("*.yaml"))
    if not files:
        print(f"no catalog files found in {catalog_dir}", file=sys.stderr)
        return 2
    failures = 0
    with httpx.Client(headers=UA, timeout=45, follow_redirects=True) as client:
        for path in files:
            entry = yaml.safe_load(path.read_text(encoding="utf-8"))
            for u in entry.get("urls", []):
                url = u["url"]
                expect = u.get("expect", [200])
                requires = u.get("requires")
                if requires and not os.environ.get(requires):
                    print(f"PENDING  {entry['id']:24s} needs ${requires}  {url}")
                    continue
                try:
                    r = client.get(url)
                    status = r.status_code
                except httpx.HTTPError as e:
                    print(f"FAIL     {entry['id']:24s} {type(e).__name__}: {e}  {url}")
                    failures += 1
                    continue
                if status in expect:
                    print(f"ok       {entry['id']:24s} {status}  {url}")
                else:
                    print(f"FAIL     {entry['id']:24s} {status} (expected {expect})  {url}")
                    failures += 1
    print(f"\nprobe complete: {failures} failure(s)")
    return 1 if failures else 0
