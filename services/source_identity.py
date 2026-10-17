"""Shared identity checks for source imports and repository writes."""

from models import ExternalIds


def conflicting_providers(
    previous: ExternalIds, incoming: ExternalIds
) -> tuple[str, ...]:
    """Missing links are not changes; two different known IDs require review."""
    return tuple(
        provider
        for provider in ("bangumi", "anilist")
        if getattr(previous, provider) is not None
        and getattr(incoming, provider) is not None
        and getattr(previous, provider) != getattr(incoming, provider)
    )
