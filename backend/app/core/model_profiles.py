"""Look up configured generation and embedding profiles by their attributes."""

from collections.abc import Mapping
from typing import Any


def match_profiles(catalog: Mapping[str, Any], **criteria: object) -> list[str]:
    """Names of the profiles whose spec matches every criterion that is not None."""
    wanted = {key: value for key, value in criteria.items() if value is not None}
    return [
        name
        for name, spec in catalog.items()
        if all(getattr(spec, key) == value for key, value in wanted.items())
    ]


def find_profile(catalog: Mapping[str, Any], description: str, **criteria: object) -> str:
    """The single profile matching the criteria; ValueError when none or several match."""
    matches = match_profiles(catalog, **criteria)
    label = "/".join(str(value) for value in criteria.values() if value is not None)
    if not matches:
        raise ValueError(f"Unknown {description} '{label}'")
    if len(matches) > 1:
        raise ValueError(f"Multiple {description}s match '{label}'. Specify the profile explicitly.")
    return matches[0]
