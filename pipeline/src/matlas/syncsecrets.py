"""Copy the credentials in .env up to GitHub Actions secrets.

Two places hold credentials and they are not connected:

  .env              read by everything you run locally; gitignored, never leaves
                    this machine — which is why GitHub cannot see it
  GitHub secrets    read by the scheduled workflow running on GitHub's servers

The daily job kept failing because the second was empty. This copies one to the
other, reading values straight from .env and handing them to `gh` over stdin so
nothing lands in shell history, in the process list, or on screen.

Only credentials the workflow actually reads are copied. Values are never
printed — the output names secrets and reports lengths, nothing more.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config

# Exactly what .github/workflows/plumes-daily.yml consumes.
CI_SECRETS = (
    "CARBON_MAPPER_EMAIL",
    "CARBON_MAPPER_PASSWORD",
    "CARBON_MAPPER_TOKEN",
    "EARTHDATA_TOKEN",
)

# Preferred over a pasted token: these mint a fresh one on every run, so the
# automation cannot expire out from under you.
DURABLE = ("CARBON_MAPPER_EMAIL", "CARBON_MAPPER_PASSWORD")


def run(repo: str | None = None, dry_run: bool = False) -> int:
    config.load()

    if not shutil.which("gh"):
        print("GitHub CLI (gh) not found. Install it from https://cli.github.com/")
        return 1
    try:
        who = subprocess.run(
            ["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"could not run gh: {type(e).__name__}")
        return 1
    if who.returncode != 0:
        print("gh is not signed in. Run:  gh auth login")
        return 1
    print(f"signed in to GitHub as {who.stdout.strip()}")

    present = {name: config.get(name) for name in CI_SECRETS}
    missing_durable = [n for n in DURABLE if not present.get(n)]
    if missing_durable:
        print(
            "\nNote: "
            + " and ".join(missing_durable)
            + " not set in .env.\n"
            "Carbon Mapper access tokens expire within days, so unattended runs are\n"
            "far more reliable with the email/password pair."
        )

    to_set = {k: v for k, v in present.items() if v}
    if not to_set:
        print("\nNothing to copy — none of the CI credentials are set in .env.")
        return 1

    print(f"\ncopying {len(to_set)} secret(s) to GitHub:")
    failures = 0
    for name, value in to_set.items():
        if dry_run:
            print(f"  {name:26s} would set ({len(value)} chars)")
            continue
        cmd = ["gh", "secret", "set", name]
        if repo:
            cmd += ["--repo", repo]
        try:
            # Value goes over stdin, never argv: argv is visible in the process
            # list and can be captured by shell history.
            proc = subprocess.run(
                cmd, input=value, capture_output=True, text=True, timeout=120
            )
        except (OSError, subprocess.SubprocessError) as e:
            print(f"  {name:26s} FAILED ({type(e).__name__})")
            failures += 1
            continue
        if proc.returncode == 0:
            print(f"  {name:26s} set ({len(value)} chars)")
        else:
            print(f"  {name:26s} FAILED — {(proc.stderr or '').strip()[:120]}")
            failures += 1

    if failures:
        print(f"\n{failures} secret(s) failed to set.")
        return 1

    if not dry_run:
        print("\nDone. Verify with:   gh secret list")
        print("Trigger a run with:  gh workflow run 'daily plume refresh'")
    return 0
