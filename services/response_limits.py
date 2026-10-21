"""Bound decoded HTTP bodies before Requests handles redirects or JSON/text.

Requests can consume a redirect body even with allow_redirects=False. Enforce
the limit in a response hook, before that processing, rather than checking
response.content after the whole body has already been downloaded.
"""

import time
from collections.abc import Callable

import requests


class ResponseSizeExceeded(requests.RequestException):
    """An upstream response exceeded its decoded byte allowance."""


def bounded_response(max_bytes: int, *, deadline: float | None = None) -> Callable:
    if max_bytes <= 0:
        raise ValueError("Response byte limit must be positive")

    def receive(response: requests.Response, **kwargs: object) -> requests.Response:
        try:
            length = response.headers.get("Content-Length")
            if length is not None:
                if not length.isascii() or not length.isdigit():
                    raise ResponseSizeExceeded("Invalid HTTP Content-Length")
                significant = length.lstrip("0") or "0"
                if (
                    len(significant) > len(str(max_bytes))
                    or int(significant) > max_bytes
                ):
                    raise ResponseSizeExceeded("HTTP response exceeds size limit")
            body = bytearray()
            # iter_content counts decompressed bytes, including gzip responses.
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if deadline is not None and time.monotonic() >= deadline:
                    raise requests.Timeout("HTTP response time budget exhausted")
                if len(body) + len(chunk) > max_bytes:
                    raise ResponseSizeExceeded("HTTP response exceeds size limit")
                body.extend(chunk)
            # Preserve Requests' normal text/json/iter_content interface without
            # reading the socket a second time. Both are Requests' body cache.
            response._content = bytes(body)
            response._content_consumed = True
            return response
        except BaseException:
            response.close()
            raise

    return receive
