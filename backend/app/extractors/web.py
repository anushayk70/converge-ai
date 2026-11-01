"""Website fetching with SSRF protection + article extraction."""
from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings
from .base import Extraction, ExtractionError
from .documents import extract_html, extract_pdf

UA = "Mozilla/5.0 (compatible; ConvergeAI/1.0; research-synthesis)"


def _check_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ExtractionError("Please enter a full http(s) URL, e.g. https://example.com/article")
    if settings.allow_private_urls:
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        if os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY"):
            return  # a proxy resolves names for us
        raise ExtractionError(f"Could not resolve the host '{p.hostname}'. Check the URL and your internet connection.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ExtractionError("This URL points to a private/internal address, which is blocked for safety "
                                  "(set ALLOW_PRIVATE_URLS=true on a trusted local machine to allow it).")


def fetch(url: str) -> tuple[bytes, str, str]:
    limit = settings.max_upload_mb * 1024 * 1024
    cur = url.strip()
    try:
        with httpx.Client(follow_redirects=False, timeout=25, headers={"User-Agent": UA, "Accept": "text/html,application/pdf,*/*"}) as c:
            for _ in range(6):
                _check_url(cur)
                with c.stream("GET", cur) as r:
                    if r.is_redirect:
                        cur = urljoin(cur, r.headers.get("location", ""))
                        continue
                    if r.status_code >= 400:
                        hint = " (the site may block automated access)" if r.status_code in (401, 403, 429) else ""
                        raise ExtractionError(f"The website returned HTTP {r.status_code}{hint}.")
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > limit:
                            raise ExtractionError(f"The page is larger than {settings.max_upload_mb} MB.")
                    return bytes(buf), r.headers.get("content-type", "").lower(), cur
            raise ExtractionError("Too many redirects.")
    except httpx.TimeoutException:
        raise ExtractionError("The website took too long to respond.")
    except httpx.HTTPError as e:
        raise ExtractionError(f"Could not download the page: {e}")


def extract_url(url: str) -> Extraction:
    data, ctype, final = fetch(url)
    if "pdf" in ctype or final.lower().split("?")[0].endswith(".pdf"):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(data)
        try:
            ex = extract_pdf(Path(f.name))
        finally:
            os.unlink(f.name)
        ex.meta["final_url"] = final
        return ex
    if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
        raise ExtractionError(f"Unsupported content type '{ctype or 'unknown'}'. Upload the file directly instead.")
    html = data.decode(httpx.Response(200, content=data, headers={"content-type": ctype}).encoding or "utf-8", errors="replace")
    ex = extract_html(html, urlparse(final).netloc)
    ex.meta["final_url"] = final
    return ex
