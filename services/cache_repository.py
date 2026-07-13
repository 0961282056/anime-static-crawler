"""Thread-safe, atomic repository for Cloudinary URL mappings."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from pathlib import Path

from services.atomic_io import atomic_write_json
from services.errors import DataContractError


class CacheRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()
        self._saved_snapshot = dict(self._data)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataContractError(f"Invalid cache file {self.path}: {exc}") from exc
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw.items()
        ):
            raise DataContractError(
                f"Cache file must be a string-to-string object: {self.path}"
            )
        return raw

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._data)

    def remove_urls_with_public_ids(self, public_ids: Iterable[str]) -> int:
        from services.retention import cloudinary_public_id_from_url

        targets = set(public_ids)
        with self._lock:
            keys = [
                key
                for key, value in self._data.items()
                if cloudinary_public_id_from_url(value) in targets
            ]
            for key in keys:
                del self._data[key]
            return len(keys)

    def save_if_changed(self) -> bool:
        with self._lock:
            if self._data == self._saved_snapshot:
                return False
            atomic_write_json(self.path, self._data)
            self._saved_snapshot = dict(self._data)
            return True
