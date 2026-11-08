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
        for target in (b"CONNECT 198.51.100.5:443 HTTP/1.1", b"CONNECT [::1]:443 HTTP/1.1", b"CONNECT bad..example:443 HTTP/1.1", b"CONNECT ok.example:80 HTTP/1.1", b"CONNECT ok.example:+443 HTTP/1.1", b"GET ok.example:443 HTTP/1.1"):
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

    async def test_allowed_connect_uses_validated_ip_and_establishes_tunnel(self):
        reader=asyncio.StreamReader(); reader.feed_data(b"CONNECT ALLOWED.EXAMPLE.:443 HTTP/1.1\r\nHost: allowed.example\r\n\r\n"); reader.feed_eof()
        writer=Writer(); upstream_writer=Writer(); address=unittest.mock.Mock(version=4)
        address.__str__=lambda self: "198.51.100.7"
        connector=AsyncMock(return_value=(asyncio.StreamReader(),upstream_writer))
        with patch.object(proxy, "resolve_public_addresses", new=AsyncMock(return_value=(address,))) as resolver, patch.object(proxy,"_tunnel",new=AsyncMock()) as tunnel:
            await proxy.handle_connect(reader,writer,allowed_peers=frozenset({ipaddress.ip_address("127.0.0.1")}),allowed_hosts=frozenset({"allowed.example"}),connect_upstream=connector)
        self.assertIn(b" 200 ",writer.data)
        self.assertEqual(resolver.await_args.args[:2],("allowed.example",443))
        self.assertEqual(connector.await_args.args,("198.51.100.7",443))
        tunnel.assert_awaited_once()

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


class DnsCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_resolver_slots_are_retained_until_workers_finish(self):
        import threading
        import time
        peer=ipaddress.ip_address("127.0.0.1")
        caps=proxy.ProxyCapacity(frozenset({peer}))
        gate=threading.Event(); lock=threading.Lock(); entered=[]
        def blocked(*_args):
            with lock: entered.append(True)
            gate.wait(5)
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))]
        try:
            with patch.object(proxy, "DNS_TIMEOUT_SECONDS", 0.05):
                async def attempt():
                    with self.assertRaises(proxy.ProxyRequestError) as error:
                        await proxy.resolve_public_addresses("allowed.example",443,capacities=caps,peer_ip=peer,resolver=blocked)
                    return error.exception.status
                tasks=[asyncio.create_task(attempt()) for _ in range(proxy.MAX_DNS_WORKERS_PER_CLIENT)]
                self.assertEqual(await asyncio.gather(*tasks), [504]*proxy.MAX_DNS_WORKERS_PER_CLIENT)
                self.assertEqual(len(entered),proxy.MAX_DNS_WORKERS_PER_CLIENT)
                started=time.monotonic(); self.assertEqual(await attempt(),503)
                self.assertLess(time.monotonic()-started,0.5)
                self.assertTrue(caps.dns_slots[peer].locked())
                # An already-established tunnel does not need another DNS slot.
                client=asyncio.StreamReader(); upstream=asyncio.StreamReader()
                client.feed_data(b"synthetic-frame"); client.feed_eof()
                upstream.feed_data(b"synthetic-reply"); upstream.feed_eof()
                client_writer=Writer(); upstream_writer=Writer()
                await asyncio.wait_for(proxy._tunnel(client, client_writer, upstream, upstream_writer),1)
                self.assertEqual(bytes(upstream_writer.data),b"synthetic-frame")
                self.assertEqual(bytes(client_writer.data),b"synthetic-reply")
                gate.set()
                for _ in range(100):
                    if not caps.dns_slots[peer].locked() and caps.dns_slots[peer]._value == proxy.MAX_DNS_WORKERS_PER_CLIENT:break
                    await asyncio.sleep(0.01)
                self.assertEqual(caps.dns_slots[peer]._value,proxy.MAX_DNS_WORKERS_PER_CLIENT)
                with self.assertRaises(proxy.ProxyRequestError) as error:
                    await proxy.resolve_public_addresses("allowed.example",443,capacities=caps,peer_ip=peer,resolver=blocked)
                self.assertEqual(error.exception.status,403)
        finally:
            gate.set()
            caps.close()

    async def test_shutdown_pool_rejects_new_work_without_leaking_capacity(self):
        peer=ipaddress.ip_address("127.0.0.1"); caps=proxy.ProxyCapacity(frozenset({peer})); caps.close()
        with self.assertRaises(proxy.ProxyRequestError) as error:
            await proxy.resolve_public_addresses("allowed.example",443,capacities=caps,peer_ip=peer,resolver=lambda *_:[])
        self.assertEqual(error.exception.status,503)
        self.assertEqual(caps.dns_slots[peer]._value,proxy.MAX_DNS_WORKERS_PER_CLIENT)
        self.assertEqual(caps.dns_total._value,proxy.MAX_DNS_WORKERS)

    async def test_default_executor_remains_available_with_blocked_dns(self):
        import concurrent.futures
        import threading
        peer=ipaddress.ip_address("127.0.0.1"); caps=proxy.ProxyCapacity(frozenset({peer})); gate=threading.Event()
        loop=asyncio.get_running_loop(); pool=concurrent.futures.ThreadPoolExecutor(max_workers=4); loop.set_default_executor(pool)
        def blocked(*_args): gate.wait(5); return []
        try:
            with patch.object(proxy, "DNS_TIMEOUT_SECONDS", 0.05):
                async def attempt():
                    try: await proxy.resolve_public_addresses("allowed.example",443,capacities=caps,peer_ip=peer,resolver=blocked)
                    except proxy.ProxyRequestError: pass
                await asyncio.gather(*(attempt() for _ in range(4)))
                other=loop.run_in_executor(None, lambda: "ready")
                self.assertEqual(await asyncio.wait_for(other,1),"ready")
        finally:
            gate.set(); caps.close(); pool.shutdown(wait=True)


class ResolverProcessTests(unittest.TestCase):
    @unittest.skipUnless(__import__("os").name == "posix", "SIGTERM behavior is validated on Linux CI")
    def test_sigterm_stops_process_with_blocked_synthetic_resolver(self):
        import os, subprocess, sys
        code = """import asyncio, importlib.util, pathlib, threading
spec=importlib.util.spec_from_file_location('proxy',str(pathlib.Path('deploy/oci-a1/egress_proxy.py')))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
async def main():
 caps=p.ProxyCapacity(frozenset({p.ipaddress.ip_address('127.0.0.1')}))
 started=threading.Event(); gate=threading.Event()
 def blocked(*args): started.set();gate.wait()
 asyncio.get_running_loop().run_in_executor(caps.resolver_pool,blocked)
 await asyncio.to_thread(started.wait)
 print('ready',flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
"""
        process=subprocess.Popen([sys.executable,"-c",code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),"ready")
            process.terminate()
            process.communicate(timeout=3)
            self.assertNotEqual(process.returncode,0)
        finally:
            if process.poll() is None:
                process.kill();process.communicate(timeout=3)

if __name__ == "__main__": unittest.main()
