"""Bounded raw TCP forwarding to the private Relay TLS-PSK listener."""
import asyncio
import ipaddress
import os


def positive_int(name, default, maximum):
    value = os.environ.get(name, str(default))
    if not value.isascii() or not value.isdecimal() or len(value) > 6:
        raise ValueError(f"{name} must be a positive integer")
    number = int(value)
    if not 1 <= number <= maximum:
        raise ValueError(f"{name} is outside the allowed range")
    return number


class DeviceIngress:
    """Cap all forwarded sessions; only the first client byte has an idle deadline."""

    def __init__(self, relay_host, max_connections=32, first_byte_timeout=10):
        self.relay_host = relay_host
        self.max_connections = max_connections
        self.first_byte_timeout = first_byte_timeout
        self.tasks = {}
        self.closing = False

    def accept(self, reader, writer):
        # Synchronous callback reserves the slot before creating a task.
        if self.closing or len(self.tasks) >= self.max_connections:
            writer.close()
            return
        task = asyncio.create_task(self.forward(reader, writer))
        self.tasks[task] = writer
        task.add_done_callback(lambda done: self.tasks.pop(done, None))

    async def forward(self, reader, writer):
        upstream_writer = None
        try:
            first = await asyncio.wait_for(reader.read(65536), self.first_byte_timeout)
            if not first:
                return
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(self.relay_host, 8443), timeout=5)
            upstream_writer.write(first)
            await upstream_writer.drain()

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

    async def close(self):
        self.closing = True
        tasks = tuple(self.tasks)
        for writer in self.tasks.values():
            writer.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    address = ipaddress.ip_address(os.environ["RELAY_BIND_IP"])
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_private:
        raise ValueError("Relay listener must be private IPv4")
    ingress = DeviceIngress(str(address),
        positive_int("DEVICE_INGRESS_MAX_CONNECTIONS", 32, 1024),
        positive_int("DEVICE_INGRESS_FIRST_BYTE_TIMEOUT", 10, 300))
    server = await asyncio.start_server(ingress.accept, "0.0.0.0", 8443, limit=65536)
    try:
        async with server:
            await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()
        await ingress.close()


if __name__ == "__main__":
    asyncio.run(main())
