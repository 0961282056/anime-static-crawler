"""HTTP clients for source pages and bounded image downloads."""

from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config
from services.errors import ImageStoreError, SourceFetchError, SourceNotFoundError
from services.settings import CrawlerSettings

RETRY_STATUSES = (429, 500, 502, 503, 504)
ALLOWED_IMAGE_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def create_retry_session(settings: CrawlerSettings) -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.75,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=settings.max_workers,
        pool_maxsize=settings.max_workers,
        max_retries=retry,
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": settings.source_user_agent,
            "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*",
        }
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class SourceClient:
    """Fetches one seasonal source page and surfaces typed failures."""

    def __init__(
        self,
        settings: CrawlerSettings,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or create_retry_session(settings)

    def season_url(self, year: str, season: str) -> str:
        if season not in Config.SEASON_TO_MONTH:
            raise ValueError(f"Unsupported season: {season}")
        if not str(year).isdigit() or len(str(year)) != 4:
            raise ValueError(f"Invalid year: {year}")
        month = Config.SEASON_TO_MONTH[season]
        return f"{self.settings.source_base_url}/{year}{month:02d}/"

    def fetch_quarter_html(self, year: str, season: str) -> tuple[str, str]:
        url = self.season_url(year, season)
        try:
            response = self.session.get(
                url,
                timeout=self.settings.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SourceFetchError(f"Unable to fetch {url}: {exc}") from exc

        if response.status_code == 404:
            raise SourceNotFoundError(f"Source season does not exist yet: {url}")
        if not response.ok:
            raise SourceFetchError(
                f"Source returned HTTP {response.status_code}: {url}"
            )

        response.encoding = "utf-8"
        if not response.text.strip():
            raise SourceFetchError(f"Source returned an empty document: {url}")
        return url, response.text


@dataclass(frozen=True)
class DownloadedImage:
    content: bytes
    content_type: str
    final_url: str


class SafeImageDownloader:
    """Downloads only bounded raster images from approved public hosts."""

    def __init__(self, settings: CrawlerSettings) -> None:
        self.settings = settings
        self._thread_local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = create_retry_session(self.settings)
            self._thread_local.session = session
        return session

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise ImageStoreError("Image URL must use HTTPS")
        if parsed.username or parsed.password:
            raise ImageStoreError("Image URL may not contain credentials")
        if parsed.port not in (None, 443):
            raise ImageStoreError("Image URL may only use the default HTTPS port")
        if host not in self.settings.image_allowed_hosts:
            raise ImageStoreError(f"Image host is not allowed: {host}")

        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ImageStoreError(f"Unable to resolve image host: {host}") from exc

        if not addresses:
            raise ImageStoreError(f"Image host did not resolve: {host}")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ImageStoreError(f"Image host resolved to a blocked address: {ip}")

    def download(self, url: str, *, max_redirects: int = 3) -> DownloadedImage:
        current_url = url
        session = self._session()

        for redirect_number in range(max_redirects + 1):
            self._validate_url(current_url)
            try:
                response = session.get(
                    current_url,
                    timeout=self.settings.image_timeout_seconds,
                    allow_redirects=False,
                    stream=True,
                    headers={
                        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif"
                    },
                )
            except requests.RequestException as exc:
                raise ImageStoreError(
                    f"Unable to download approved image URL: {exc}"
                ) from exc

            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise ImageStoreError("Image redirect omitted the Location header")
                if redirect_number >= max_redirects:
                    raise ImageStoreError("Image exceeded the redirect limit")
                current_url = urljoin(current_url, location)
                continue

            try:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                content_type = content_type.split(";", 1)[0].strip().lower()
                if content_type not in ALLOWED_IMAGE_TYPES:
                    raise ImageStoreError(
                        f"Image response has an unsupported Content-Type: {content_type}"
                    )

                content_length = response.headers.get("Content-Length")
                if (
                    content_length
                    and int(content_length) > self.settings.image_max_bytes
                ):
                    raise ImageStoreError("Image exceeds the configured size limit")

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.settings.image_max_bytes:
                        raise ImageStoreError("Image exceeds the configured size limit")
                    chunks.append(chunk)
            except (requests.RequestException, ValueError) as exc:
                if isinstance(exc, ImageStoreError):
                    raise
                raise ImageStoreError(f"Invalid image response: {exc}") from exc
            finally:
                response.close()

            content = b"".join(chunks)
            if not content:
                raise ImageStoreError("Image response was empty")
            return DownloadedImage(
                content=content,
                content_type=content_type,
                final_url=current_url,
            )

        raise ImageStoreError("Image redirect handling failed")
