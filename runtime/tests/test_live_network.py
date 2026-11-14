import configparser
from pathlib import Path
import unittest

from community_grid.live_network import validate_addresses


DISTRO = Path(__file__).resolve().parents[2] / "distro"


class LiveNetworkTests(unittest.TestCase):
    def setUp(self):
        self.ics_route = {
            "defaults": [{"dst": "default", "gateway": "192.168.137.1", "protocol": "dhcp"}]
        }

    def test_dhcp_profile(self):
        profile = configparser.ConfigParser()
        profile.read(DISTRO / "community-grid-wired.nmconnection")
        self.assertTrue(profile["connection"].getboolean("autoconnect"))
        self.assertEqual(profile["connection"]["type"], "ethernet")
        self.assertEqual(profile["ipv4"]["method"], "auto")
        self.assertNotIn("address1", profile["ipv4"])

    def test_dhcp_address_change_requires_current_lease(self):
        for host in ("192.168.137.12", "192.168.137.212"):
            validate_addresses(host, "192.168.137.1", {f"{host}/24"}, self.ics_route)
        with self.assertRaises(ValueError):
            validate_addresses("192.168.137.12", "192.168.137.1", {"192.168.137.212/24"}, self.ics_route)

    def test_static_fallback(self):
        validate_addresses("192.168.50.2", "192.168.50.1", {"192.168.50.2/24"}, {})

    def test_reject_wrong_peer_or_route(self):
        assigned = {"192.168.137.212/24"}
        with self.assertRaises(ValueError):
            validate_addresses("192.168.137.212", "192.168.137.3", assigned, self.ics_route)
        with self.assertRaises(ValueError):
            validate_addresses("192.168.137.212", "192.168.137.1", assigned, {})
        with self.assertRaises(ValueError):
            validate_addresses("192.168.137.212", "192.168.137.1", assigned, {
                **self.ics_route, "gateway": "192.168.137.254"
            })

    def test_opt_in_ssh_security_configuration(self):
        helper = (DISTRO / "start-supervisor-ssh.sh").read_text()
        hook = (DISTRO / "0100-community-grid.hook.chroot").read_text()
        self.assertIn("systemctl mask ssh.service ssh.socket", hook)
        self.assertIn('restrict,from="%s"', helper)
        self.assertIn("ListenAddress $dell", helper)
        self.assertIn("AllowUsers $account@$lenovo", helper)
        for setting in (
            "AuthenticationMethods publickey", "PasswordAuthentication no",
            "PermitRootLogin no", "DisableForwarding yes",
        ):
            self.assertIn(setting, helper)
