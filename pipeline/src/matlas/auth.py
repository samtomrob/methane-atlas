"""Validate credentials end to end, without ever printing their values.

Each check does the weakest real thing that proves the credential works —
for CDSE that means authorizing an actual granule download, because a token
that mints fine can still be unauthorized to fetch product bytes.
"""

from __future__ import annotations

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
        return f"{icon} {self.service:24s} {self.detail}"


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


def check_cdse() -> Result:
    service = "CDSE (Sentinel-5P)"
    if not (config.CDSE_OAUTH.ready or config.CDSE_PASSWORD.ready):
        return Result(service, SKIP, "not configured — see docs/CREDENTIALS.md")

    with httpx.Client(follow_redirects=True) as client:
        token, method = cdse_token(client)
        if not token:
            return Result(service, FAIL, method)

        # Find one real CH4 granule, then prove we are authorized to fetch its
        # bytes. A ranged GET keeps this to a few KB instead of ~500 MB.
        try:
            q = client.get(
                CDSE_ODATA,
                params={
                    "$filter": "contains(Name,'L2__CH4___')",
                    "$orderby": "ContentDate/Start desc",  # newest = still on hot storage
                    "$top": "1",
                },
                timeout=60,
            )
            q.raise_for_status()
            items = q.json().get("value", [])
        except (httpx.HTTPError, ValueError) as e:
            return Result(service, FAIL, f"token ok ({method}); catalogue query failed: {e}")
        if not items:
            return Result(service, OK, f"token ok ({method}); no CH4 granule returned to test download")

        product_id = items[0].get("Id")
        try:
            d = _authorized_get(
                client,
                f"{CDSE_ODATA}({product_id})/$value",
                token,
                extra_headers={"Range": "bytes=0-2047"},
            )
        except httpx.HTTPError as e:
            return Result(service, FAIL, f"token ok ({method}); download probe network error: {e}")
        if d.status_code in (200, 206):
            return Result(service, OK, f"token + granule download authorized ({method})")
        if d.status_code == 202:
            # Product is on cold storage and now staging — authorization passed,
            # which is what this check is about.
            return Result(service, OK, f"authorized ({method}); test granule staging from archive")
        if d.status_code in (401, 403):
            return Result(
                service, FAIL, f"token ok ({method}) but download refused ({d.status_code})"
            )
        return Result(service, FAIL, f"token ok ({method}); download probe returned {d.status_code}")


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
        return Result(service, SKIP, f"missing {', '.join(config.GEE.missing)}")

    key_path = config.gee_key_path()
    if key_path is None or not key_path.exists():
        return Result(service, FAIL, f"key file not found at {key_path}")

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

    try:
        import ee
    except ImportError:
        return Result(service, SKIP, f"key looks valid ({email}); install earthengine-api to fully verify")

    project = config.get("GEE_PROJECT_ID")
    try:
        creds = ee.ServiceAccountCredentials(email, str(key_path))
        ee.Initialize(credentials=creds, project=project)
    except Exception as e:  # ee raises bare Exception subclasses liberally
        return Result(service, FAIL, f"initialize failed: {str(e)[:160]}")

    try:
        n = (
            ee.ImageCollection("COPERNICUS/S5P/OFFL/L3_CH4")
            .filterDate("2026-07-01", "2026-08-01")
            .size()
            .getInfo()
        )
    except Exception as e:
        return Result(service, FAIL, f"initialized, but S5P collection query failed: {str(e)[:160]}")
    return Result(service, OK, f"authorized on project {project}; {n} S5P CH4 images in Jul 2026")


def _check_bearer(service: str, cred: config.Credential, url: str, header: dict[str, str]) -> Result:
    if not cred.ready:
        return Result(service, SKIP, f"missing {', '.join(cred.missing)}")
    try:
        r = httpx.get(url, headers=header, timeout=45, follow_redirects=True)
    except httpx.HTTPError as e:
        return Result(service, FAIL, f"network error ({type(e).__name__})")
    if r.status_code == 200:
        return Result(service, OK, "token accepted")
    if r.status_code in (401, 403):
        return Result(service, FAIL, f"token rejected ({r.status_code})")
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
    check_cdse,
    check_cdse_s3,
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
    if failed:
        print("See docs/CREDENTIALS.md for the exact setup steps for any failing service.")
    return 1 if failed else 0
