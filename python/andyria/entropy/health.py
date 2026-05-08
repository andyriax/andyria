"""Entropy health monitoring for Andyria.

Implements simplified NIST SP 800-90B health tests:
  - Repetition Count Test (RCT): detects stuck output values.
  - Adaptive Proportion Test (APT): detects bias or low-entropy output.

Each EntropySource should have a dedicated EntropyHealthMonitor updated
after every collection call.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class HealthTestResult:
    passed: bool
    test_name: str
    detail: str


@dataclass
class EntropyHealthMonitor:
    """Stateful health monitor for a stream of entropy bytes.

    Parameters
    ----------
    rct_cutoff:
        Fail if any single byte repeats ``rct_cutoff`` or more times
        consecutively (NIST SP 800-90B §4.4.1).
    apt_window:
        Window size for Adaptive Proportion Test.
    apt_cutoff:
        Fail if the most-common byte appears ``apt_cutoff`` or more times
        within the APT window (~38.5 % for uniform 8-bit output at H=1).
    """

    rct_cutoff: int = 24
    apt_window: int = 512
    apt_cutoff: int = 196

    _last_byte: Optional[int] = field(default=None, init=False, repr=False)
    _run_length: int = field(default=0, init=False, repr=False)
    _window: list = field(default_factory=list, init=False, repr=False)

    def update(self, data: bytes) -> List[HealthTestResult]:
        """Feed new bytes into the monitor and return any failures."""
        results: List[HealthTestResult] = []
        for byte in data:
            results.extend(self._rct_update(byte))
            self._window.append(byte)
            if len(self._window) > self.apt_window:
                self._window.pop(0)
            if len(self._window) >= self.apt_window:
                results.extend(self._apt_check())
        return results

    def _rct_update(self, byte: int) -> List[HealthTestResult]:
        if byte == self._last_byte:
            self._run_length += 1
        else:
            self._run_length = 1
        self._last_byte = byte

        if self._run_length >= self.rct_cutoff:
            return [
                HealthTestResult(
                    passed=False,
                    test_name="RCT",
                    detail=f"Byte 0x{byte:02x} repeated {self._run_length} times consecutively",
                )
            ]
        return [HealthTestResult(passed=True, test_name="RCT", detail="ok")]

    def _apt_check(self) -> List[HealthTestResult]:
        counts = Counter(self._window)
        most_common_byte, count = counts.most_common(1)[0]
        if count >= self.apt_cutoff:
            return [
                HealthTestResult(
                    passed=False,
                    test_name="APT",
                    detail=(
                        f"Byte 0x{most_common_byte:02x} appears {count}/{self.apt_window} times "
                        f"(threshold {self.apt_cutoff})"
                    ),
                )
            ]
        return [HealthTestResult(passed=True, test_name="APT", detail="ok")]
