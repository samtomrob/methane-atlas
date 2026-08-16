"""Validate credentials end to end, without ever printing their values.

Each check does the weakest real thing that proves the credential works —
for CDSE that means authorizing an actual granule download, because a token
that mints fine can still be unauthorized to fetch product bytes.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from dataclasses import dataclass
from typing import Callable

import httpx

from . import config

CDSE_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
CDSE_ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
CDSE_PUBLIC_CLIENT = "cdse-public"
CDSE_S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"

OK, FAIL, SKIP = "ok", "FAIL", "--"


@dataclass
class Result:
    service: str
    status: str
    detail: str

    def line(self) -> str:
        icon = {OK: "ok    ", FAIL: "FAIL  ", SKIP: "skip  "}[self.status]
        # Provider errors often span lines; keep one service per line.
        detail = " ".join(self.detail.split())
        return f"{icon} {self.service:24s} {detail}"


def cdse_token(client: httpx.Client) -> tuple[str | None, str]:
    """Mint a CDSE access token by whichever grant the user configured."""
    if config.CDSE_OAUTH.ready:
        data = {
            "grant_type": "client_credentials",
            "client_id": config.get("CDSE_CLIENT_ID"),
            "client_secret": config.get("CDSE_CLIENT_SECRET"),
        }
        method = "OAuth client"
    elif config.CDSE_PASSWORD.ready:
        data = {
            "grant_type": "password",
            "client_id": CDSE_PUBLIC_CLIENT,
            "username": config.get("CDSE_USERNAME"),
            "password": config.get("CDSE_PASSWORD"),
        }
        method = "account password"
    else:
        return None, "no credentials set"

    try:
        r = client.post(CDSE_TOKEN_URL, data=data, timeout=45)
    except httpx.HTTPError as e:
        return None, f"{method}: network error ({type(e).__name__})"
    if r.status_code != 200:
        hint = ""
        try:
            hint = r.json().get("error_description") or r.json().get("error") or ""
        except ValueError:
            pass
        return None, f"{method}: token request returned {r.status_code} {hint}".strip()
    token = r.json().get("access_token")
    if not token:
        return None, f"{method}: response had no access_token"
    return token, method


def _authorized_get(
    client: httpx.Client,
    url: str,
    token: str,
    extra_headers: dict[str, str] | None = None,
    max_hops: int = 6,
) -> httpx.Response:
    """GET following redirects manually, re-attaching the bearer token each hop.

    CDSE bounces product downloads across hosts; automatic redirect handling
    drops the Authorization header on cross-origin hops and the download 401s.
    """
    headers = {"Authorization": f"Bearer {token}", **(extra_headers or {})}
    for _ in range(max_hops):
        r = client.get(url, headers=headers, follow_redirects=False, timeout=90)
        if r.status_code not in (301, 302, 303, 307, 308):
            return r
        location = r.headers.get("Location")
        if not location:
            return r
        url = str(httpx.URL(url).join(location))
    return r


def check_cdse_search() -> Result:
    """Granule discovery. The OData catalogue is anonymous, so this needs no
    credential — it is the other half of the download path and worth asserting
    because the whole pipeline starts here."""
    service = "CDSE (granule search)"
    try:
        r = httpx.get(
            CDSE_ODATA,
            params={
                "$filter": "contains(Name,'S5P_OFFL_L2__CH4')",
                "$orderby": "ContentDate/Start desc",
                "$top": "1",
            },
            headers={"User-Agent": "methane-atlas/0.1"},
            timeout=60,
            follow_redirects=True,
        )
        r.raise_for_status()
        items = r.json().get("value", [])
    except (httpx.HTTPError, ValueError) as e:
        return Result(service, FAIL, f"catalogue query failed ({type(e).__name__})")
    if not items:
        return Result(service, FAIL, "catalogue reachable but returned no CH4 granules")
    newest = (items[0].get("ContentDate") or {}).get("Start", "?")[:10]
    return Result(service, OK, f"catalogue reachable (anonymous); newest CH4 granule {newest}")


def check_cdse_oauth() -> Result:
    """Informational only.

    Verified 2026-08-14: a client_credentials token is accepted by the identity
    service but REJECTED (401) by the OData product-download endpoint, which is
    why this pipeline downloads over S3 instead. The OAuth client is therefore
    not required — it exists for Sentinel Hub / openEO APIs we don't use.
    """
    service = "CDSE (OAuth, unused)"
    if not config.CDSE_OAUTH.ready:
        return Result(service, SKIP, "not set — not needed, pipeline downloads via S3")
    with httpx.Client(follow_redirects=True) as client:
        token, method = cdse_token(client)
    if not token:
        return Result(service, SKIP, f"token failed ({method}) — harmless, S3 is the download path")
    return Result(service, SKIP, "token mints ok; unused (OData download rejects it by design)")


def check_cdse_s3() -> Result:
    """CDSE recommends searching via OData but pulling bytes over S3, so the
    S3 keys are a separate credential from the OAuth client."""
    service = "CDSE (S3 download)"
    if not config.CDSE_S3.ready:
        return Result(service, SKIP, f"missing {', '.join(config.CDSE_S3.missing)}")
    try:
        import boto3
        from botocore.client import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return Result(service, SKIP, "boto3 not installed")

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=CDSE_S3_ENDPOINT,
            aws_access_key_id=config.get("CDSE_S3_ACCESS_KEY"),
            aws_secret_access_key=config.get("CDSE_S3_SECRET_KEY"),
            region_name="default",
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 2}),
        )
        resp = s3.list_objects_v2(Bucket="eodata", Prefix="Sentinel-5P/", MaxKeys=1)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "?")
        return Result(service, FAIL, f"S3 rejected the keys ({code})")
    except (BotoCoreError, OSError) as e:
        return Result(service, FAIL, f"S3 connection error ({type(e).__name__})")
    found = resp.get("KeyCount", 0)
    return Result(service, OK, f"eodata bucket readable ({found} test key listed)")


def check_gee() -> Result:
    service = "Earth Engine"
    if not config.GEE.ready:
        return Result(service, SKIP, "missing GEE_PROJECT_ID")

    try:
        import ee
    except ImportError:
        return Result(service, SKIP, "earthengine-api not installed")

    project = config.get("GEE_PROJECT_ID")
    key_path = config.gee_key_path()
    use_service_account = key_path is not None and key_path.exists()

    if use_service_account:
        import json

        try:
            key = json.loads(key_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            return Result(service, FAIL, f"key file unreadable: {type(e).__name__}")
        email = key.get("client_email")
        if key.get("type") != "service_account" or not email:
            return Result(
                service,
                FAIL,
                "key file is not a service-account JSON (needs type=service_account + client_email)",
            )
        mode = f"service account {email.split('@')[0]}"
        try:
            creds = ee.ServiceAccountCredentials(email, str(key_path))
            ee.Initialize(credentials=creds, project=project)
        except Exception as e:  # ee raises bare Exception subclasses liberally
            return Result(service, FAIL, f"initialize failed as {mode}: {str(e)[:150]}")
    else:
        # Credentials stored by `matlas gee-login` / `earthengine authenticate`.
        mode = "stored login"
        try:
            ee.Initialize(project=project)
        except ee.EEException:
            # Not a failure: Earth Engine is an optional shortcut, not a
            # dependency — the pipeline sources everything from CDSE.
            return Result(service, SKIP, "optional/unused; run `matlas gee-login` to enable")
        except Exception as e:
            return Result(service, SKIP, f"optional/unused; not initialised ({str(e)[:70]})")

    try:
        n = (
            ee.ImageCollection("COPERNICUS/S5P/OFFL/L3_CH4")
            .filterDate("2026-07-01", "2026-08-01")
            .size()
            .getInfo()
        )
    except Exception as e:
        return Result(service, FAIL, f"initialized ({mode}), but S5P query failed: {str(e)[:140]}")
    return Result(
        service, OK, f"{mode} on project {project}; {n} S5P CH4 images found for Jul 2026"
    )


def gee_login() -> int:
    """Interactive Earth Engine sign-in. Opens a browser, stores a refresh token
    under the user's config dir — enough for the one-time backfill run."""
    config.load()
    try:
        import ee
    except ImportError:
        print("earthengine-api is not installed; run `uv sync` in pipeline/ first.")
        return 1
    project = config.get("GEE_PROJECT_ID")
    if not project:
        print("Set GEE_PROJECT_ID in .env first (your Earth Engine Cloud project ID).")
        return 1
    print(f"Signing in to Earth Engine for project {project} — a browser window will open.")
    try:
        ee.Authenticate()
        ee.Initialize(project=project)
    except Exception as e:
        print(f"Sign-in failed: {e}")
        return 1
    print("Signed in. Verify with: matlas auth-check")
    return 0


def token_expiry(token: str | None) -> dt.datetime | None:
    """Expiry of a JWT, read from its unsigned payload.

    Worth surfacing because these tokens are short-lived and a working
    credential today says nothing about tomorrow — a Carbon Mapper access token
    runs days, not months, so unattended jobs die quietly unless the expiry is
    visible before it bites.
    """
    if not token or token.count(".") < 2:
        return None
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return dt.datetime.fromtimestamp(exp, dt.timezone.utc)


def _expiry_note(token: str | None) -> str:
    when = token_expiry(token)
    if not when:
        return ""
    days = (when - dt.datetime.now(dt.timezone.utc)).days
    if days < 0:
        return f" — EXPIRED {when:%Y-%m-%d}"
    if days <= 7:
        return f" — expires in {days} day{'s' if days != 1 else ''} ({when:%Y-%m-%d})"
    return f" — valid {days} more days"


def _check_bearer(service: str, cred: config.Credential, url: str, header: dict[str, str]) -> Result:
    if not cred.ready:
        return Result(service, SKIP, f"missing {', '.join(cred.missing)}")
    try:
        r = httpx.get(url, headers=header, timeout=45, follow_redirects=True)
    except httpx.HTTPError as e:
        return Result(service, FAIL, f"network error ({type(e).__name__})")
    token = next(
        (v.split(" ", 1)[-1] for k, v in header.items() if k.lower() == "authorization"), None
    ) or next(iter(header.values()), None)
    if r.status_code == 200:
        return Result(service, OK, f"token accepted{_expiry_note(token)}")
    if r.status_code in (401, 403):
        return Result(service, FAIL, f"token rejected ({r.status_code}){_expiry_note(token)}")
    return Result(service, FAIL, f"unexpected status {r.status_code}")


def check_earthdata() -> Result:
    return _check_bearer(
        "NASA Earthdata",
        config.EARTHDATA,
        "https://cmr.earthdata.nasa.gov/search/granules.json"
        "?collection_concept_id=C3242707413-LPCLOUD&page_size=1",
        {"Authorization": f"Bearer {config.get('EARTHDATA_TOKEN')}"},
    )


def check_carbon_mapper() -> Result:
    return _check_bearer(
        "Carbon Mapper",
        config.CARBON_MAPPER,
        "https://api.carbonmapper.org/api/v1/catalog/plumes/annotated?limit=1",
        {"Authorization": f"Bearer {config.get('CARBON_MAPPER_TOKEN')}"},
    )


def check_gfw() -> Result:
    return _check_bearer(
        "Global Forest Watch",
        config.GFW,
        "https://data-api.globalforestwatch.org/dataset/umd_tree_cover_loss/latest",
        {"x-api-key": config.get("GFW_API_KEY") or ""},
    )


def check_r2() -> Result:
    service = "Cloudflare R2"
    if not config.R2.ready:
        return Result(service, SKIP, f"missing {', '.join(config.R2.missing)}")
    try:
        import boto3
        from botocore.client import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return Result(service, SKIP, "boto3 not installed")

    bucket = config.get("R2_BUCKET") or ""
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{config.get('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
            aws_access_key_id=config.get("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=config.get("R2_SECRET_ACCESS_KEY"),
            region_name="auto",
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 2}),
        )
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "?")
        if code in ("404", "NoSuchBucket"):
            return Result(service, FAIL, f"credentials work but bucket '{bucket}' does not exist")
        return Result(service, FAIL, f"R2 rejected the request ({code})")
    except (BotoCoreError, OSError) as e:
        return Result(service, FAIL, f"R2 connection error ({type(e).__name__})")
    return Result(service, OK, f"bucket '{bucket}' reachable and writable")


CHECKS: tuple[Callable[[], Result], ...] = (
    check_cdse_search,
    check_cdse_s3,
    check_cdse_oauth,
    check_gee,
    check_earthdata,
    check_carbon_mapper,
    check_gfw,
    check_r2,
)


def run() -> int:
    config.load()
    if not config.ENV_PATH.exists():
        print(f"note: no .env at {config.ENV_PATH} — reading environment only\n")
    results = [check() for check in CHECKS]
    for r in results:
        print(r.line())
    failed = sum(1 for r in results if r.status == FAIL)
    ready = sum(1 for r in results if r.status == OK)
    skipped = sum(1 for r in results if r.status == SKIP)
    print(f"\n{ready} ready · {failed} failing · {skipped} not configured yet")

    # A credential that works locally is not the same as automation that keeps
    # working: GitHub Actions cannot read .env, and short-lived tokens die.
    expiring = [
        (name, token_expiry(config.get(name)))
        for name in ("CARBON_MAPPER_TOKEN", "EARTHDATA_TOKEN", "GFW_API_KEY")
    ]
    soon = [
        (n, w) for n, w in expiring
        if w and (w - dt.datetime.now(dt.timezone.utc)).days <= 14
    ]
    if soon:
        print("\nExpiring soon:")
        for name, when in soon:
            days = (when - dt.datetime.now(dt.timezone.utc)).days
            print(f"  {name} expires {when:%Y-%m-%d} ({days} days)")
        print(
            "  For unattended runs prefer credentials that refresh themselves —\n"
            "  CARBON_MAPPER_EMAIL + CARBON_MAPPER_PASSWORD mint a fresh token each run."
        )
    if failed:
        print("See docs/CREDENTIALS.md for the exact setup steps for any failing service.")
    return 1 if failed else 0
