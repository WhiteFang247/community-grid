"""Behavior checks for the restricted inference transport and credential file."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from community_grid.coordinator import ssh_transport
from community_grid.operator_auth import read_private_token


class RestrictedTransportTests(unittest.TestCase):
    def test_forced_command_ssh_sends_no_remote_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity, pin = root / "identity", root / "known_hosts"
            identity.touch()
            pin.touch()
            transport = ssh_transport("192.168.137.252", "cgos-infer", identity, pin,
                                      forced_command=True, bind_address="192.168.137.1")
            with patch("community_grid.coordinator.subprocess.run") as run:
                run.return_value.stdout = '[200, {"healthy": true}]'
                self.assertEqual(transport("GET", "/api/worker"), [200, {"healthy": True}])
            argv = run.call_args.args[0]
            self.assertEqual(argv[-1], "cgos-infer@192.168.137.252")
            self.assertEqual(argv[argv.index("-b") + 1], "192.168.137.1")
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("BatchMode=yes", argv)
            self.assertFalse(any("grid_relay" in arg for arg in argv))

    def test_forced_command_cannot_select_remote_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity, pin = root / "identity", root / "known_hosts"
            identity.touch()
            pin.touch()
            with self.assertRaises(ValueError):
                ssh_transport("192.168.137.252", "cgos-infer", identity, pin,
                              staging="/dev/shm/old", forced_command=True)


@unittest.skipUnless(os.name == "posix", "POSIX file modes required")
class OperatorTokenTests(unittest.TestCase):
    def test_accepts_owned_private_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "operator-token"
            token.write_text("a" * 32 + "\n", encoding="ascii")
            token.chmod(0o600)
            self.assertEqual(read_private_token(token), "a" * 32)
            token.chmod(0o644)
            with self.assertRaises(PermissionError):
                read_private_token(token)
            token.chmod(0o600)
            alias = token.with_name("alias")
            alias.symlink_to(token)
            with self.assertRaises(OSError):
                read_private_token(alias)


if __name__ == "__main__":
    unittest.main()
