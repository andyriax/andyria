"""Physical entropy collectors for Andyria.

Each collector gathers bytes from a different hardware/OS source. Raw outputs
are XOR-mixed and BLAKE3-whitened in ``EntropyBeaconFactory``.

Available sources
-----------------
os_urandom    — Kernel TRNG via os.urandom(); always present.
hwrng         — /dev/hwrng hardware TRNG (Raspberry Pi BCM, Intel RDRAND mapped).
clock_jitter  — Nanosecond-level CPU scheduling jitter; available everywhere.
thermal       — CPU temperature sensor fluctuations; Linux / psutil-capable systems.
system_stats  — Cumulative OS counters (CPU, memory, net I/O); cross-platform.
"""

from __future__ import annotations

import hashlib
import io
import os
import struct
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

try:
    import psutil as _psutil  # type: ignore[import-untyped]  # optional — not available on all platforms (e.g. Android/Termux)

    _PSUTIL = _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL = None


class EntropySource(ABC):
    """Abstract base for a physical entropy source."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def collect(self, num_bytes: int = 64) -> bytes:
        """Return at least ``num_bytes`` of raw entropy. May return more."""
        ...

    @property
    def available(self) -> bool:
        """Whether this source is usable on the current platform."""
        return True


# ---------------------------------------------------------------------------
# Concrete sources
# ---------------------------------------------------------------------------


class OsUrandomSource(EntropySource):
    """Kernel TRNG via os.urandom(). Always available."""

    @property
    def name(self) -> str:
        return "os_urandom"

    def collect(self, num_bytes: int = 64) -> bytes:
        return os.urandom(num_bytes)


class HwRngSource(EntropySource):
    """Hardware TRNG via /dev/hwrng (Raspberry Pi BCM2835/BCM2711, Intel RDRAND)."""

    @property
    def name(self) -> str:
        return "hwrng"

    @property
    def available(self) -> bool:
        return Path("/dev/hwrng").exists()

    def collect(self, num_bytes: int = 64) -> bytes:
        try:
            with open("/dev/hwrng", "rb") as f:
                return f.read(num_bytes)
        except OSError:
            return b""


class ClockJitterSource(EntropySource):
    """CPU clock jitter via nanosecond-resolution timer deltas.

    Tight loops capture scheduling noise, pipeline stalls, and thermal
    effects at the nanosecond scale. Low bits of each delta carry the
    most entropy.
    """

    @property
    def name(self) -> str:
        return "clock_jitter"

    def collect(self, num_bytes: int = 64) -> bytes:
        buf = io.BytesIO()
        samples = num_bytes * 8  # oversample to accumulate enough jitter
        prev = time.perf_counter_ns()
        for _ in range(samples):
            now = time.perf_counter_ns()
            buf.write(struct.pack(">Q", now - prev))
            prev = now
        return buf.getvalue()


class ThermalSource(EntropySource):
    """CPU thermal sensor readings as entropy mixing material.

    Temperature values fluctuate at millidegree resolution driven by
    workload variance and physical thermal noise in the silicon.
    """

    @property
    def name(self) -> str:
        return "thermal"

    @property
    def available(self) -> bool:
        if sys.platform == "linux":
            return Path("/sys/class/thermal").exists()
        if _PSUTIL is None:
            return False
        try:
            return bool(_PSUTIL.sensors_temperatures())
        except AttributeError:
            return False

    def collect(self, num_bytes: int = 64) -> bytes:
        readings = io.BytesIO()

        # psutil cross-platform path
        if _PSUTIL is not None:
            try:
                temps = _PSUTIL.sensors_temperatures()
                if temps:
                    for sensor_list in temps.values():
                        for entry in sensor_list:
                            val = int(entry.current * 1000) & 0xFFFFFFFF
                            readings.write(struct.pack(">I", val))
            except (AttributeError, OSError):
                pass

        # Direct /sys/class/thermal fallback on Linux
        if readings.tell() == 0 and sys.platform == "linux":
            for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
                try:
                    val = int(zone.read_text().strip()) & 0xFFFFFFFF
                    readings.write(struct.pack(">I", val))
                except (OSError, ValueError):
                    continue

        data = readings.getvalue()
        if not data:
            return b""

        h = hashlib.shake_256(data)
        return h.digest(max(num_bytes, len(data)))


class SystemStatsSource(EntropySource):
    """System-level OS counters as entropy mixing material.

    CPU usage, memory, network I/O, and process counters change
    continuously and reflect physical system state.
    """

    @property
    def name(self) -> str:
        return "system_stats"

    def collect(self, num_bytes: int = 64) -> bytes:
        buf = io.BytesIO()
        if _PSUTIL is not None:
            try:
                ct = _PSUTIL.cpu_times()
                for val in (ct.user, ct.system, ct.idle):
                    buf.write(struct.pack(">d", val))
                vm = _PSUTIL.virtual_memory()
                buf.write(struct.pack(">QQ", vm.used, vm.available))
                net = _PSUTIL.net_io_counters()
                if net:
                    buf.write(struct.pack(">QQ", net.bytes_sent, net.bytes_recv))
                proc = _PSUTIL.Process()
                buf.write(struct.pack(">II", os.getpid(), proc.num_threads()))
                buf.write(struct.pack(">Q", time.perf_counter_ns()))
            except (OSError, AttributeError):
                buf.write(os.urandom(num_bytes))
        else:
            buf.write(os.urandom(num_bytes))

        data = buf.getvalue()
        h = hashlib.shake_256(data)
        return h.digest(max(num_bytes, 32))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_collector_chain(sources: Optional[List[str]] = None) -> List[EntropySource]:
    """Build an ordered list of available entropy sources.

    Args:
        sources: Optional allowlist of source names. Defaults to all available.
                 ``os_urandom`` is always included as a fallback floor.
    """
    from .mcu_collector import McuEntropySource  # local import avoids circular deps

    all_sources: List[EntropySource] = [
        OsUrandomSource(),
        HwRngSource(),
        ClockJitterSource(),
        ThermalSource(),
        SystemStatsSource(),
        McuEntropySource(),
    ]

    available = [s for s in all_sources if s.available]

    if sources is not None:
        available = [s for s in available if s.name in sources]

    # Guarantee os_urandom is always present
    names = {s.name for s in available}
    if "os_urandom" not in names:
        available.insert(0, OsUrandomSource())

    return available
