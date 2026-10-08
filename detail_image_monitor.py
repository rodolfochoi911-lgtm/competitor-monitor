"""Detect in-place changes to SK 7mobile event-detail images.

The crawler disables browser image loading to stay fast, so download only the
image resources found inside the event detail and hash the actual bytes.
"""
import hashlib
import re
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

MAX_IMAGES = 24
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TIMEOUT_SECONDS = 10
BACKGROUND_URL = re.compile(r"url\(\s*(.*?)\s*\)", re.I)
DETAIL_SELECTORS = (
    ".event_view", ".event-view", ".eventView", ".event_detail",
    ".event-detail", ".event_cont", ".event-cont", ".evt_cont",
    ".evt-cont", ".event_content", ".event-content",
    "#ct > section", "#ct", "main", "article",
)


def _absolute_image_url(raw, page_url):
    raw = (raw or "").strip()
    if not raw or raw.startswith(("data:", "blob:", "javascript:")):
        return None
    absolute = urljoin(page_url, raw)
    parsed = urlsplit(absolute)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".internal")):
        return None
    if host.startswith(("127.", "10.", "192.168.", "169.254.")):
        return None
    return absolute


def extract_detail_image_urls(page_source, page_url):
    """Return event-area image URLs in DOM order, ignoring page chrome."""
    soup = BeautifulSoup(page_source or "", "html.parser")
    root = next((el for css in DETAIL_SELECTORS
                 if (el := soup.select_one(css)) is not None), None)
    if root is None:
        return []

    for noise in root.select("header, footer, nav, aside, .gnb, .sns, .share, .breadcrumb"):
        noise.decompose()

    urls, seen = [], set()

    def add(raw):
        url = _absolute_image_url(raw, page_url)
        if url and url not in seen and len(urls) < MAX_IMAGES:
            seen.add(url)
            urls.append(url)

    for tag in root.find_all(True):
        if tag.name == "img":
            candidates = [tag.get(key) for key in
                          ("data-src", "data-original", "data-lazy-src", "src")]
            valid = next((value for value in candidates
                          if _absolute_image_url(value, page_url)), None)
            if valid:
                add(valid)
            else:
                srcset = tag.get("data-srcset") or tag.get("srcset") or ""
                if srcset:
                    add(srcset.split(",")[0].strip().split()[0])
        style = tag.get("style") or ""
        for match in BACKGROUND_URL.finditer(style):
            add(match.group(1).strip(" '\""))
        if len(urls) >= MAX_IMAGES:
            break
    return urls


def hash_detail_images(page_source, page_url, session=requests, cache=None):
    """Return (SHA-256 hashes, warning). Incomplete fetches never form a baseline."""
    urls = extract_detail_image_urls(page_source, page_url)
    digests = []
    failures = []
    cache = cache if cache is not None else {}
    for url in urls:
        if url in cache:
            if cache[url] is None:
                failures.append(url)
                break
            digests.append(cache[url])
            continue
        response = None
        try:
            response = session.get(
                url, timeout=IMAGE_TIMEOUT_SECONDS, stream=True,
                headers={"User-Agent": "Mozilla/5.0", "Referer": page_url,
                         "Cache-Control": "no-cache"},
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not (
                content_type.startswith("image/") or
                content_type.startswith("application/octet-stream")
            ):
                raise ValueError("response is not an image")

            digest = hashlib.sha256()
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError("image exceeds size limit")
                digest.update(chunk)
            if not total:
                raise ValueError("empty image")
            hexdigest = digest.hexdigest()
            cache[url] = hexdigest
            digests.append(hexdigest)
        except (requests.RequestException, ValueError) as exc:
            cache[url] = None
            failures.append(f"{url}: {type(exc).__name__}")
            break
        finally:
            if response is not None:
                response.close()

    if failures:
        return None, f"이미지 {len(failures)}/{len(urls)}개 확인 실패 (이전 이미지 비교 기준 보존)"
    return digests, None
