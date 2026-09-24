"""Where saved lighting presets live.

One JSON file under the user's config directory, shared by the GUI and the CLI
so a look saved in one is applicable from the other.  Built-in presets are not
written to it: they are merged in at read time, so upgrading the package picks
up new ones and a user cannot end up with a stale copy of a shipped preset.
A user preset that reuses a built-in name shadows it, which is the only way to
"edit" one.
"""

from __future__ import annotations

import json
import os

from .lighting import BUILTIN_PRESETS, Preset

_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
PRESET_PATH = os.path.join(_CONFIG_HOME, "m693", "presets.json")

MAGIC = "m693-presets"


def load_user() -> list[Preset]:
    """Presets the user saved.  A damaged file is reported, not silently lost."""
    try:
        with open(PRESET_PATH) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {PRESET_PATH}: {exc}") from exc
    if data.get("magic") != MAGIC:
        raise ValueError(f"{PRESET_PATH} is not an m693 preset file")
    presets = []
    for entry in data.get("presets", []):
        try:
            presets.append(Preset.from_json(entry))
        except ValueError:
            continue  # skip the one bad entry rather than lose the whole file
    return presets


def load_all() -> list[Preset]:
    """Built-ins first, then the user's, with user names winning."""
    user = load_user()
    taken = {p.name for p in user}
    return [p for p in BUILTIN_PRESETS if p.name not in taken] + user


def find(name: str) -> Preset | None:
    for preset in load_all():
        if preset.name.lower() == name.lower():
            return preset
    return None


def is_builtin(name: str) -> bool:
    return any(p.name.lower() == name.lower() for p in BUILTIN_PRESETS)


def save_user(presets: list[Preset]) -> None:
    os.makedirs(os.path.dirname(PRESET_PATH), exist_ok=True)
    payload = {
        "magic": MAGIC,
        "version": 1,
        "presets": [p.to_json() for p in presets],
    }
    # Write via a temporary file so an interrupted save cannot truncate the
    # only copy of everything the user has built up.
    temporary = PRESET_PATH + ".new"
    with open(temporary, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, PRESET_PATH)


def store(preset: Preset) -> None:
    """Add or replace one user preset."""
    presets = [p for p in load_user() if p.name.lower() != preset.name.lower()]
    presets.append(preset)
    presets.sort(key=lambda p: p.name.lower())
    save_user(presets)


def remove(name: str) -> bool:
    """Delete a user preset.  Returns False if there was no such preset."""
    presets = load_user()
    kept = [p for p in presets if p.name.lower() != name.lower()]
    if len(kept) == len(presets):
        return False
    save_user(kept)
    return True
