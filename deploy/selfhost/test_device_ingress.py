"""Synthetic loopback tests for the bounded byte-transparent forwarder."""
import asyncio
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("device_ingress", Path(__file__).with_name("device_ingress.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeviceIngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peers = set()
        async def echo(reader, writer):
            self.peers.add(writer)
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                self.peers.discard(writer)
                writer.close()
        self.upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = self.upstream.sockets[0].getsockname()[1]
        original_connect = asyncio.open_connection
        async def connect_fixture(host, requested_port):
            self.assertEqual((host, requested_port), ("127.0.0.1", 8443))
            return await original_connect("127.0.0.1", port)
        self.connect_patch = patch.object(module.asyncio, "open_connection", side_effect=connect_fixture)
        self.connect_patch.start()
        self.original_connect = original_connect
        self.ingress = module.DeviceIngress("127.0.0.1", max_connections=1, first_byte_timeout=0.15)
        self.server = await asyncio.start_server(self.ingress.accept, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.clients = []

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        await self.ingress.close()
        for writer in self.clients:
            writer.close()
            await writer.wait_closed()
        for writer in tuple(self.peers):
            writer.close()
        self.upstream.close()
        await self.upstream.wait_closed()
        self.connect_patch.stop()

    async def connect(self):
        reader, writer = await self.original_connect("127.0.0.1", self.port)
        self.clients.append(writer)
        return reader, writer

    async def wait_for_slots(self, count):
        async def wait():
            while len(self.ingress.tasks) != count:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 2)

    async def test_saturation_first_byte_timeout_and_reconnect(self):
        first, _ = await self.connect()
        await self.wait_for_slots(1)
        refused, _ = await self.connect()
        self.assertEqual(await asyncio.wait_for(refused.read(1), 1), b"")
        self.assertEqual(len(self.ingress.tasks), 1)
        self.assertEqual(await asyncio.wait_for(first.read(1), 1), b"")
        await self.wait_for_slots(0)
        reader, writer = await self.connect()
        frame = b"\x16\x03\x03synthetic-device-frame"
        writer.write(frame)
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(frame)), 1), frame)
        # Once any byte is forwarded, the first-byte deadline does not kill a session.
        await asyncio.sleep(0.25)
        writer.write(b"still-open")
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(10), 1), b"still-open")
        writer.close()
        await writer.wait_closed()
        await self.wait_for_slots(0)

    async def test_disconnect_upstream_failure_and_shutdown_release_slots(self):
        _, writer = await self.connect()
        await self.wait_for_slots(1)
        writer.close()
        await writer.wait_closed()
        await self.wait_for_slots(0)
        with patch.object(module.asyncio, "open_connection", side_effect=OSError("synthetic failure")):
            reader, writer = await self.connect()
            writer.write(b"x")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(1), 1), b"")
            await self.wait_for_slots(0)
        reader, _ = await self.connect()
        await self.wait_for_slots(1)
        await self.ingress.close()
        self.assertEqual(await asyncio.wait_for(reader.read(1), 1), b"")
        await self.wait_for_slots(0)

    def test_invalid_config_rejected(self):
        for value in ("0", "-1", "1.5", "9999999", "١"):
            with self.subTest(value=value), patch.dict(os.environ, {"TEST_BOUND": value}):
                with self.assertRaises(ValueError):
                    module.positive_int("TEST_BOUND", 32, 1024)


if __name__ == "__main__":
    unittest.main()
