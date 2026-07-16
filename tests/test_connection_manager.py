from __future__ import annotations

import unittest
from unittest.mock import patch

import connection_manager
from connection_manager import ConnectionManager


class FakeHardware:
    instances: list["FakeHardware"] = []

    def __init__(self) -> None:
        self.connected_to: tuple[str, float] | None = None
        self.disconnected = False
        self.reset_count = 0
        self.instances.append(self)

    def connect(self, ip_address: str, timeout: float = 0) -> None:
        self.connected_to = (ip_address, timeout)

    def reset_all_outputs(self) -> None:
        self.reset_count += 1

    def disconnect(self) -> None:
        self.disconnected = True


class ConnectionManagerTests(unittest.TestCase):
    def test_settings_can_restore_interactive_session(self) -> None:
        FakeHardware.instances.clear()
        with patch.object(connection_manager, "CpxApHardware", FakeHardware):
            manager = ConnectionManager()

            manager.connect("192.168.0.11", 2.5)
            settings = manager.connection_settings()
            first = FakeHardware.instances[0]
            manager.disconnect()

            self.assertIsNotNone(settings)
            self.assertTrue(first.disconnected)
            self.assertFalse(manager.is_connected)

            assert settings is not None
            manager.connect(settings.ip_address, settings.timeout)

            self.assertTrue(manager.is_connected)
            self.assertEqual(manager.ip_address, "192.168.0.11")
            self.assertEqual(manager.timeout, 2.5)
            self.assertEqual(
                FakeHardware.instances[1].connected_to,
                ("192.168.0.11", 2.5),
            )


if __name__ == "__main__":
    unittest.main()
