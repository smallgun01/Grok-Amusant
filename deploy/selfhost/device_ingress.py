"""Forward raw device TLS-PSK bytes to the private Relay listener."""
import asyncio
import ipaddress
import os

async def forward(reader, writer):
    upstream_writer = None
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(os.environ["RELAY_BIND_IP"], 8443), timeout=5)
        async def pump(source, dest):
            while data := await source.read(65536):
                dest.write(data)
                await dest.drain()
        tasks = [asyncio.create_task(pump(reader, upstream_writer)),
                 asyncio.create_task(pump(upstream_reader, writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()
        if upstream_writer:
            upstream_writer.close()

async def main():
    address = ipaddress.ip_address(os.environ["RELAY_BIND_IP"])
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_private:
        raise ValueError("Relay listener must be private IPv4")
    server = await asyncio.start_server(forward, "0.0.0.0", 8443, limit=65536)
    async with server:
        await server.serve_forever()

asyncio.run(main())
