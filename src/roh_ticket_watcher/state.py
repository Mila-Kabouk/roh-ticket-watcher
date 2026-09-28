from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Seat


class StateError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "performances": {}}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Cannot read state file {self.path}: {exc}") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("version") != 1
            or not isinstance(raw.get("performances"), dict)
        ):
            raise StateError(f"Unsupported or malformed state file: {self.path}")
        self.data = raw

    def available_for(self, state_key: str) -> set[Seat]:
        entry = self.data["performances"].get(state_key, {})
        if not isinstance(entry, dict):
            raise StateError(f"Malformed availability state for {state_key}")
        values = entry.get("available", [])
        if not isinstance(values, list):
            raise StateError(f"Malformed availability state for {state_key}")
        if any(not isinstance(value, str) for value in values):
            raise StateError(f"Malformed seat state for {state_key}")
        try:
            return {Seat.from_key(value) for value in values}
        except (ValueError, TypeError) as exc:
            raise StateError(f"Malformed seat state for {state_key}") from exc

    def update(self, state_key: str, available: set[Seat]) -> None:
        self.data["performances"][state_key] = {
            "available": sorted(seat.key for seat in available),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(self.data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise StateError(f"Cannot save state file {self.path}: {exc}") from exc
