"""HTTP with a polite rate limit and an on-disk cache, so repeat runs cost nothing."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
import truststore

DATA_DIR = Path(os.environ.get("FINN_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
CACHE_DIR = DATA_DIR / "cache"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)
_MIN_GAP = {"www.screener.in": 2.5, "www.nseindia.com": 0.6, "nsearchives.nseindia.com": 0.4, "www.bseindia.com": 0.8}
_last_hit: dict[str, float] = {}
_client: httpx.Client | None = None
_nse_cookies = False


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status  # None when the host never answered


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
            timeout=httpx.Timeout(45, connect=15),
            follow_redirects=True,
        )
    return _client


def _throttle(host: str) -> None:
    gap = _MIN_GAP.get(host, 0.3)
    wait = _last_hit.get(host, 0) + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def _cache_path(url: str) -> Path:
    return CACHE_DIR / hashlib.sha256(url.encode()).hexdigest()[:32]


def _is_public(host: str) -> bool:
    try:
        return all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(host, 443))
    except (OSError, ValueError):
        return False


def fetch_linked_pdf(url: str, max_bytes: int = 40_000_000) -> Path:
    """Fetch a link found inside a filing. That text is untrusted, so a link could point anywhere:
    https only, public addresses only with every redirect re-checked, PDF only, and capped in size."""
    path = _cache_path(url)
    if path.exists():
        return path
    current = url
    for _ in range(4):
        parsed = httpx.URL(current)
        if parsed.scheme != "https" or not _is_public(parsed.host):
            raise FetchError(f"{current}: refused")
        _throttle(parsed.host)
        try:
            with client().stream("GET", current, follow_redirects=False) as r:
                if r.is_redirect:
                    current = urljoin(current, r.headers.get("location", ""))
                    continue
                if r.status_code != 200:
                    raise FetchError(f"{current}: HTTP {r.status_code}", r.status_code)
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > max_bytes:
                        raise FetchError(f"{current}: too large")
                    if len(data) >= 1024 and not bytes(data[:1024]).lstrip().startswith(b"%PDF"):
                        raise FetchError(f"{current}: not a PDF")
        except httpx.HTTPError as e:
            raise FetchError(f"{current}: {e}") from e
        if not bytes(data[:1024]).lstrip().startswith(b"%PDF"):
            raise FetchError(f"{current}: not a PDF")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path
    raise FetchError(f"{url}: too many redirects")


def fetch_file(url: str, *, headers: dict | None = None) -> Path:
    """Fetch once, for good, and return the path, so a 100 MB annual report needn't sit in memory."""
    path = _cache_path(url)
    if not path.exists():
        fetch(url, ttl=None, headers=headers)
    return path


def fetch(url: str, *, ttl: float | None = 3600, headers: dict | None = None, retries: int = 3) -> bytes:
    """GET bytes. ttl=None caches forever (filings never change once published)."""
    path = _cache_path(url)
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        return path.read_bytes()

    host = httpx.URL(url).host
    error: Exception | str = "no attempt"
    status = None
    for attempt in range(retries):
        _throttle(host)
        try:
            r = client().get(url, headers=headers)
            if r.status_code == 200 and r.content:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(r.content)
                return r.content
            status = r.status_code
            error = f"HTTP {status}"
            if r.status_code in (404, 410):
                break
            # NSE can turn away a first visit that has none of its cookies, and its home page hands them out.
            global _nse_cookies
            if r.status_code in (401, 403) and host == "www.nseindia.com" and not _nse_cookies:
                _nse_cookies = True
                client().get("https://www.nseindia.com/")
            if r.status_code == 429:
                time.sleep(float(r.headers.get("retry-after", 10)))
        except httpx.HTTPError as e:
            error = e
        time.sleep(1.5 * (attempt + 1))
    # A stale copy beats no copy: filings and tables only ever gain rows.
    if path.exists():
        return path.read_bytes()
    raise FetchError(f"{url}: {error}", status)


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, **kw))
