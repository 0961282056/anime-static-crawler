"""Small atomic file helpers used by every persistent repository."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    primary_error: BaseException | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if temporary_path:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    f"Temporary file cleanup also failed: {temporary_path}: {cleanup_error}"
                )
                logger.warning("Temporary file cleanup also failed: %s", temporary_path)


def atomic_write_json(path: Path, data: Any) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content)
