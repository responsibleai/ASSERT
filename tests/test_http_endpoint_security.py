# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Regression tests for HTTP endpoint SSRF protections."""

import socket
import unittest
from unittest.mock import patch

try:
    import aiohttp
    from aiohttp import web
except ImportError:  # pragma: no cover - optional dependency
    aiohttp = None
    web = None

from assert_ai.core.model_client import Message
from assert_ai.core.session import HTTPEndpointSession


@unittest.skipIf(web is None, "aiohttp not installed")
class HTTPEndpointSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def test_open_closes_resolver_if_connector_setup_fails(self) -> None:
        class FakeResolver:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        resolver = FakeResolver()
        session = HTTPEndpointSession(endpoint="http://localhost:8080/target")

        with (
            patch.object(aiohttp, "DefaultResolver", return_value=resolver),
            patch.object(
                aiohttp,
                "TCPConnector",
                side_effect=RuntimeError("setup failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                await session.open()

        self.assertTrue(resolver.closed)

    async def test_open_closes_connector_and_resolver_if_session_setup_fails(self) -> None:
        class FakeResolver:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        class FakeConnector:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        resolver = FakeResolver()
        connector = FakeConnector()
        session = HTTPEndpointSession(endpoint="http://localhost:8080/target")

        with (
            patch.object(aiohttp, "DefaultResolver", return_value=resolver),
            patch.object(aiohttp, "TCPConnector", return_value=connector),
            patch.object(
                aiohttp,
                "ClientSession",
                side_effect=RuntimeError("setup failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                await session.open()

        self.assertTrue(connector.closed)
        self.assertTrue(resolver.closed)

    async def test_redirect_is_rejected_before_destination_request(self) -> None:
        destination_reached = False

        async def redirect(_request):
            raise web.HTTPTemporaryRedirect(location="/private")

        async def private(_request):
            nonlocal destination_reached
            destination_reached = True
            return web.json_response({"response": "should not be reached"})

        app = web.Application()
        app.router.add_post("/start", redirect)
        app.router.add_post("/private", private)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        session = HTTPEndpointSession(endpoint=f"http://localhost:{port}/start")
        await session.open()
        try:
            with self.assertRaisesRegex(RuntimeError, "redirect"):
                await session.run_turn([Message(role="user", content="probe")])
            self.assertFalse(destination_reached)
        finally:
            await session.close()
            await runner.cleanup()

    async def test_connection_time_private_dns_answer_is_rejected(self) -> None:
        destination_reached = False

        async def private(_request):
            nonlocal destination_reached
            destination_reached = True
            return web.json_response({"response": "should not be reached"})

        app = web.Application()
        app.router.add_post("/target", private)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        resolution_count = 0

        def fake_getaddrinfo(host, requested_port, *args, **kwargs):
            nonlocal resolution_count
            resolution_count += 1
            if resolution_count == 1:
                return [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        ("93.184.216.34", 0),
                    )
                ]
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", requested_port),
                )
            ]

        try:
            with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
                session = HTTPEndpointSession(
                    endpoint=f"http://rebind.test:{port}/target"
                )
                await session.open()
                try:
                    with self.assertRaisesRegex(RuntimeError, "Connection error"):
                        await session.run_turn([Message(role="user", content="probe")])
                    self.assertFalse(destination_reached)
                finally:
                    await session.close()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
