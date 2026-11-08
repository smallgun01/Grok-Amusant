"""Synthetic, no-network checks for the CONNECT proxy."""
import asyncio
import importlib.util
import ipaddress
from pathlib import Path
import socket
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location("egress_proxy", Path(__file__).with_name("egress_proxy.py"))
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)

class HostPolicyTests(unittest.TestCase):
    def test_normalization_and_configuration_fail_closed(self):
        self.assertEqual(proxy.parse_allowed_hosts(" API.example., bücher.example "), frozenset({"api.example", "xn--bcher-kva.example"}))
        for value in (None, "", "*.example", "api.example,,other.example", "https://api.example", "127.0.0.1", "198.51.100.5", "host..example", "placeholder.invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                proxy.parse_allowed_hosts(value)
        for target in (b"CONNECT 198.51.100.5:443 HTTP/1.1", b"CONNECT [::1]:443 HTTP/1.1", b"CONNECT bad..example:443 HTTP/1.1", b"CONNECT ok.example:80 HTTP/1.1", b"GET ok.example:443 HTTP/1.1"):
            with self.subTest(target=target), self.assertRaises(proxy.ProxyRequestError):
                proxy.parse_connect_target(target)
        self.assertEqual(proxy.parse_connect_target(b"CONNECT API.EXAMPLE.:443 HTTP/1.1"), ("api.example", 443))

class Writer:
    def __init__(self): self.data = bytearray(); self.closed = False
    def get_extra_info(self, name): return ("127.0.0.1", 1234) if name == "peername" else None
    def write(self, data): self.data.extend(data)
    async def drain(self): pass
    def close(self): self.closed = True
    async def wait_closed(self): pass

class ProxyPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_denied_host_never_resolves_or_connects(self):
        reader=asyncio.StreamReader(); reader.feed_data(b"CONNECT denied.example:443 HTTP/1.1\r\nHost: denied.example\r\n\r\n"); reader.feed_eof()
        writer=Writer(); resolver=unittest.mock.Mock(); connector=AsyncMock()
        await proxy.handle_connect(reader, writer, allowed_peers=frozenset({ipaddress.ip_address("127.0.0.1")}), allowed_hosts=frozenset({"allowed.example"}), resolver=resolver, connect_upstream=connector)
        self.assertIn(b" 403 ", writer.data); resolver.assert_not_called(); connector.assert_not_called()
        self.assertNotIn(b"denied.example",writer.data)

    async def test_allowed_host_rechecks_all_addresses_and_blocks_rebinding(self):
        host="allowed.example"; public=unittest.mock.Mock(is_global=True, version=4); public.__str__=lambda self: "198.51.100.7"
        real_ip=ipaddress.ip_address
        def parse(value):
            return public if value == "198.51.100.7" else real_ip(value)
        def result(addr): return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (addr, 443))]
        with patch.object(proxy.ipaddress, "ip_address", side_effect=parse):
            found=await proxy.resolve_public_addresses(host,443,resolver=lambda *_:result("198.51.100.7"))
            self.assertEqual(found,(public,))
            with self.assertRaises(proxy.ProxyRequestError) as error:
                await proxy.resolve_public_addresses(host,443,resolver=lambda *_:result("127.0.0.1"))
            self.assertEqual(error.exception.status,403)
            with self.assertRaises(proxy.ProxyRequestError):
                await proxy.resolve_public_addresses(host,443,resolver=lambda *_:result("198.51.100.7")+result("127.0.0.1"))

if __name__ == "__main__": unittest.main()
