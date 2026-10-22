"""Compact full-text discovery index; complete evidence stays in quarterly JSON."""

from __future__ import annotations

import json
import unicodedata

from services.atomic_io import atomic_write_text


def search_identity(anime):
    return [
        anime.bangumi_id,
        anime.anime_name,
        anime.original_name or "",
        anime.external_ids.bangumi,
    ]


def build_search_index(paths, repository):
    repository = repository.validated_snapshot()
    entries = {}
    latest = None
    quarters = sorted(
        repository.validate_all(),
        key=lambda p: (int(p.stem[:4]), "冬春夏秋".index(p.stem[-1])),
        reverse=True,
    )
    for path in quarters:
        dataset = repository.load_path(path)
        if latest is None or dataset.generated_at > latest:
            latest = dataset.generated_at
        for anime in dataset.anime_list:
            identity = search_identity(anime)
            key = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
            if key in entries:
                entries[key]["quarters"].append(path.stem)
            else:
                entries[key] = {
                    "identity": identity,
                    "story": unicodedata.normalize("NFKC", anime.story).lower(),
                    "quarters": [path.stem],
                }
    return {
        "schema_version": 1,
        **repository.data_inventory(),
        "generated_at": latest.isoformat() if latest else None,
        "entries": list(entries.values()),
    }


def write_search_index(paths, repository):
    index = build_search_index(paths, repository)
    atomic_write_text(
        paths.output_dir / "search-index.json",
        json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n",
    )
    return index


def validate_search_index(paths, repository):
    if json.loads(
        (paths.output_dir / "search-index.json").read_text()
    ) != build_search_index(paths, repository):
        raise RuntimeError("Search index does not match canonical quarterly data")
