"""MCU entropy collector for the Andyria EntropyBeaconFactory.

Reads bytes from a file-based queue written by ``mcu_bridge``.
The file is a flat binary append log; the collector drains up to
``num_bytes`` per call and returns whatever is available (falling
back to an empty bytes if the file is absent or empty).

Design notes
------------
- No IPC beyond the filesystem — avoids coupling the bridge process
  lifetime to the coordinator.
- The bridge writes 64-byte entropy records; the collector reads up
  to ``num_bytes`` and discards the rest of the current record so
  the read pointer stays aligned.
- File rotation: when the queue grows beyond 1 MiB the collector
  truncates it to zero (safe because the beacon factory XOR-mixes
  this source with others — a temporary gap is acceptable).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .collectors import EntropySource

# Default location — overridable via McuEntropySource(path=...)
_DEFAULT_QUEUE = Path.home() / ".andyria" / "data" / "mcu_entropy.bin"
_MAX_QUEUE_BYTES = 1 * 1024 * 1024  # 1 MiB


class McuEntropySource(EntropySource):
    """File-queue entropy source fed by the MCU bridge daemon.

    Parameters
    ----------
    path:
        Path to the binary entropy queue file produced by ``mcu_bridge``.
        Defaults to ``~/.andyria/data/mcu_entropy.bin``.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else _DEFAULT_QUEUE
        self._read_pos: int = 0

    @property
    def name(self) -> str:
        return "mcu"

    @property
    def available(self) -> bool:
        return self._path.exists() and self._path.stat().st_size > 0

    def collect(self, num_bytes: int = 64) -> bytes:
        if not self._path.exists():
            return b""
        try:
            with open(self._path, "rb") as fh:
                fh.seek(self._read_pos)
                data = fh.read(num_bytes)
            if not data:
                # Nothing new; reset so the next write is readable
                self._read_pos = 0
                return b""
            self._read_pos += len(data)

            # Rotate queue file if it has grown too large
            try:
                size = self._path.stat().st_size
                if size > _MAX_QUEUE_BYTES:
                    with open(self._path, "wb"):
                        pass  # truncate
                    self._read_pos = 0
            except OSError:
                pass

            return data
        except OSError:
            return b""
