"""
OBD vehicle-adapter extension boundary.

The existing OBD-II flow is manual code entry (the user types P0302 and
gets a manual-scoped diagnosis). This module defines the seam a real
scanner adapter (Bluetooth ELM327, USB, serial) would plug into, so a
future integration never has to touch the core ask/diagnose pipeline.

Deliberately NO working adapter ships here -- shipping a stub that looks
like scanner support would be dishonest. get_adapter() returns a
NullAdapter that reports exactly that. A future implementation registers
itself via register_adapter() and every caller switches automatically.
"""
from __future__ import annotations

from typing import Protocol


class OBDAdapter(Protocol):
    """Interface for a live OBD scanner connection. Implementations must
    be local (serial/BT/USB) -- nothing here may call a cloud service."""

    name: str

    def is_available(self) -> bool:
        """Whether the adapter hardware/driver is present on this machine."""
        ...

    def connect(self) -> bool:
        """Opens the connection. Returns False instead of raising when the
        vehicle/adapter isn't there."""
        ...

    def read_dtcs(self) -> list[dict]:
        """Stored trouble codes: [{"code": "P0302", "status": "stored"|"pending"|"permanent"}]."""
        ...

    def read_live_data(self, pids: list[str] | None = None) -> dict:
        """Optional live PID data; plain dict so any adapter shape fits."""
        ...

    def clear_dtcs(self) -> bool:
        """Clears stored codes. Implementations should refuse unless the
        user explicitly triggered it in the UI."""
        ...

    def disconnect(self) -> None: ...


class NullAdapter:
    """The only adapter that ships today: reports honestly that no live
    scanner is connected, so callers can say 'manual code entry only'
    instead of pretending hardware support exists."""

    name = "none"

    def is_available(self) -> bool:
        return False

    def connect(self) -> bool:
        return False

    def read_dtcs(self) -> list[dict]:
        return []

    def read_live_data(self, pids: list[str] | None = None) -> dict:
        return {}

    def clear_dtcs(self) -> bool:
        return False

    def disconnect(self) -> None:
        return None


_adapter: OBDAdapter | None = None


def register_adapter(adapter: OBDAdapter) -> None:
    """Registers the machine's adapter implementation (called by a future
    extension package at startup)."""
    global _adapter
    _adapter = adapter


def get_adapter() -> OBDAdapter:
    """The current adapter, or NullAdapter when none is installed."""
    return _adapter if _adapter is not None else NullAdapter()
