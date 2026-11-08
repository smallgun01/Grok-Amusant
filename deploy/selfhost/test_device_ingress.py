"""Synthetic loopback check for byte-transparent device ingress forwarding."""
import asyncio
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


def load_ingress():
    spec = importlib.util.spec_from_file_location("device_ingress_fixture", Path(__file__).with_name("device_ingress.py"))
    module = importlib.util.module_from_spec(spec)
    # The script is an executable entrypoint; prevent its main loop on import.
    with patch("asyncio.run", side_effect=lambda coroutine: coroutine.close()):
        spec.loader.exec_module(module)
    return module


class DeviceIngressTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_bytes_are_forwarded_both_ways(self):
        module = load_ingress()
        async def echo(reader, writer):
            data = await reader.read(1024)
            writer.write(data)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = upstream.sockets[0].getsockname()[1]
        original_connect = asyncio.open_connection
        async def connect_fixture(host, requested_port):
            self.assertEqual((host, requested_port), ("127.0.0.1", 8443))
            return await original_connect("127.0.0.1", port)
        try:
            with patch.dict(os.environ, {"RELAY_BIND_IP": "127.0.0.1"}), patch.object(module.asyncio, "open_connection", side_effect=connect_fixture):
                ingress = await asyncio.start_server(module.forward, "127.0.0.1", 0)
                try:
                    reader, writer = await original_connect("127.0.0.1", ingress.sockets[0].getsockname()[1])
                    writer.write(b"\x16\x03\x03synthetic-device-frame")
                    await writer.drain()
                    self.assertEqual(await asyncio.wait_for(reader.readexactly(25), 3), b"\x16\x03\x03synthetic-device-frame")
                    writer.close()
                    await writer.wait_closed()
                finally:
                    ingress.close()
                    await ingress.wait_closed()
        finally:
            upstream.close()
            await upstream.wait_closed()
