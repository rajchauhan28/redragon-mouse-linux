"""The macro library: what the user has written, kept on disk.

The mouse holds one macro per button slot and nothing else - no names it will
show you, no way to keep a macro you are not currently using. The stock tool
solves that with a folder of ``.jmm`` files; we keep a single JSON file, which
is easier to back up and to read.
"""

from __future__ import annotations

import json
import os

from m693.macro import Macro

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
LIBRARY_PATH = os.path.join(CONFIG_HOME, "m693", "macros.json")
FORMAT = "m693-macros"
VERSION = 1


class MacroLibrary:
    """An ordered list of named macros, persisted on change."""

    def __init__(self, path: str = LIBRARY_PATH):
        self.path = path
        self.macros: list[Macro] = []
        self.error: str | None = None
        self.load()

    def load(self) -> None:
        self.error = None
        try:
            with open(self.path) as fh:
                document = json.load(fh)
        except FileNotFoundError:
            self.macros = []
            return
        except (OSError, ValueError) as exc:
            # A broken library must not take the whole app down, and must not
            # be silently overwritten either - so we surface it and refuse to
            # save over it until the user does something.
            self.error = f"{self.path}: {exc}"
            self.macros = []
            return
        if document.get("format") != FORMAT:
            self.error = f"{self.path}: not an m693 macro library"
            self.macros = []
            return
        self.macros = [Macro.from_json(item) for item in document.get("macros", [])]

    def save(self) -> None:
        if self.error:
            return  # never clobber a file we could not understand
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        document = {
            "format": FORMAT,
            "version": VERSION,
            "macros": [macro.to_json() for macro in self.macros],
        }
        temporary = self.path + ".tmp"
        with open(temporary, "w") as fh:
            json.dump(document, fh, indent=2)
            fh.write("\n")
        os.replace(temporary, self.path)  # never leave a half-written library

    # -- editing -----------------------------------------------------------
    def unique_name(self, wanted: str) -> str:
        taken = {macro.name for macro in self.macros}
        if wanted not in taken:
            return wanted
        index = 2
        while f"{wanted} {index}" in taken:
            index += 1
        return f"{wanted} {index}"

    def add(self, macro: Macro) -> int:
        macro.name = self.unique_name(macro.name or "New macro")
        self.macros.append(macro)
        self.save()
        return len(self.macros) - 1

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.macros):
            del self.macros[index]
            self.save()

    def replace(self, index: int, macro: Macro) -> None:
        if 0 <= index < len(self.macros):
            self.macros[index] = macro
            self.save()
