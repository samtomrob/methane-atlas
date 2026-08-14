"""Credential + settings loading.

Values come from the environment, seeded from a .env file at the repo root
(gitignored). Nothing here ever prints a secret value — callers get booleans
and lengths, so `matlas auth-check` output is safe to paste anywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = REPO_ROOT / ".env"
SECRETS_DIR = REPO_ROOT / "secrets"


def load() -> None:
    """Seed os.environ from .env if present. Real environment wins, so CI
    secrets are never overridden by a stale local file."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)


def get(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip().strip('"').strip("'")
    return value or None


def present(*names: str) -> bool:
    return all(get(n) for n in names)


@dataclass(frozen=True)
class Credential:
    """One credential group and how to tell whether it is usable."""

    service: str
    vars: tuple[str, ...]
    doc: str

    @property
    def ready(self) -> bool:
        return present(*self.vars)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(v for v in self.vars if not get(v))


# Alternative credential sets per service — any one satisfied means "ready".
CDSE_OAUTH = Credential("CDSE (OAuth client)", ("CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET"), "docs/CREDENTIALS.md#cdse")
CDSE_PASSWORD = Credential("CDSE (account password)", ("CDSE_USERNAME", "CDSE_PASSWORD"), "docs/CREDENTIALS.md#cdse")
CDSE_S3 = Credential("CDSE (S3 keys)", ("CDSE_S3_ACCESS_KEY", "CDSE_S3_SECRET_KEY"), "docs/CREDENTIALS.md#cdse")
# Earth Engine needs only the project ID. Auth is either a service-account key
# file (set GEE_SERVICE_ACCOUNT_JSON_PATH) or the credentials stored locally by
# `matlas gee-login`. EE runs only for the one-time backfill, never in CI, so
# the interactive route is sufficient.
GEE = Credential("Earth Engine", ("GEE_PROJECT_ID",), "docs/CREDENTIALS.md#earth-engine")
EARTHDATA = Credential("NASA Earthdata", ("EARTHDATA_TOKEN",), "docs/CREDENTIALS.md#earthdata")
CARBON_MAPPER = Credential("Carbon Mapper", ("CARBON_MAPPER_TOKEN",), "docs/CREDENTIALS.md#carbon-mapper")
GFW = Credential("Global Forest Watch", ("GFW_API_KEY",), "docs/CREDENTIALS.md#gfw")
R2 = Credential(
    "Cloudflare R2",
    ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"),
    "docs/CREDENTIALS.md#r2",
)


def gee_key_path() -> Path | None:
    raw = get("GEE_SERVICE_ACCOUNT_JSON_PATH")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path
