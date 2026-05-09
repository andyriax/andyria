"""Tests for Andyria entropy subsystem."""

from __future__ import annotations

import os


def _make_private_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

class TestOsUrandomSource:
    def test_returns_requested_bytes(self):
        from andyria.entropy.collectors import OsUrandomSource
        src = OsUrandomSource()
        data = src.collect(num_bytes=32)
        assert len(data) == 32

    def test_always_available(self):
        from andyria.entropy.collectors import OsUrandomSource
        assert OsUrandomSource().available

    def test_output_is_not_all_zeros(self):
        from andyria.entropy.collectors import OsUrandomSource
        data = OsUrandomSource().collect(64)
        assert any(b != 0 for b in data)


class TestClockJitterSource:
    def test_returns_data(self):
        from andyria.entropy.collectors import ClockJitterSource
        data = ClockJitterSource().collect(num_bytes=32)
        assert len(data) > 0

    def test_two_calls_differ(self):
        from andyria.entropy.collectors import ClockJitterSource
        src = ClockJitterSource()
        a = src.collect(32)
        b = src.collect(32)
        # Should differ due to timing — extremely unlikely to collide
        assert a != b


class TestBuildCollectorChain:
    def test_os_urandom_always_present(self):
        from andyria.entropy.collectors import build_collector_chain
        chain = build_collector_chain(sources=["nonexistent_source"])
        names = [s.name for s in chain]
        assert "os_urandom" in names

    def test_filter_by_name(self):
        from andyria.entropy.collectors import build_collector_chain
        chain = build_collector_chain(sources=["os_urandom", "clock_jitter"])
        names = [s.name for s in chain]
        assert "thermal" not in names


# ---------------------------------------------------------------------------
# Health monitor
# ---------------------------------------------------------------------------

class TestEntropyHealthMonitor:
    def test_all_same_byte_fails_rct(self):
        from andyria.entropy.health import EntropyHealthMonitor
        monitor = EntropyHealthMonitor(rct_cutoff=5)
        results = monitor.update(bytes([0xAA] * 10))
        failures = [r for r in results if not r.passed and r.test_name == "RCT"]
        assert failures

    def test_random_bytes_pass(self):
        from andyria.entropy.health import EntropyHealthMonitor
        monitor = EntropyHealthMonitor()
        results = monitor.update(os.urandom(128))
        failures = [r for r in results if not r.passed]
        assert len(failures) == 0


# ---------------------------------------------------------------------------
# Beacon factory
# ---------------------------------------------------------------------------

class TestEntropyBeaconFactory:
    def test_beacon_has_required_fields(self):
        from andyria.entropy.beacon import EntropyBeaconFactory
        key = _make_private_key()
        factory = EntropyBeaconFactory("test-node", key, sources=["os_urandom"])
        beacon = factory.generate()

        assert beacon.id
        assert beacon.timestamp_ns > 0
        assert beacon.raw_entropy_hash
        assert len(bytes.fromhex(beacon.nonce)) == 32
        assert beacon.node_id == "test-node"
        assert beacon.signature

    def test_beacon_signature_verifies(self):
        from andyria.entropy.beacon import EntropyBeaconFactory

        key = _make_private_key()
        factory = EntropyBeaconFactory("test-node", key, sources=["os_urandom"])
        beacon = factory.generate()
        pub = key.public_key()
        assert factory.verify(beacon, pub)

    def test_two_beacons_have_different_ids(self):
        from andyria.entropy.beacon import EntropyBeaconFactory
        key = _make_private_key()
        factory = EntropyBeaconFactory("test-node", key, sources=["os_urandom", "clock_jitter"])
        b1 = factory.generate()
        b2 = factory.generate()
        assert b1.id != b2.id

    def test_tampered_beacon_fails_verification(self):
        from andyria.entropy.beacon import EntropyBeaconFactory
        key = _make_private_key()
        factory = EntropyBeaconFactory("test-node", key, sources=["os_urandom"])
        beacon = factory.generate()
        beacon.nonce = "00" * 32  # tamper
        pub = key.public_key()
        assert not factory.verify(beacon, pub)
