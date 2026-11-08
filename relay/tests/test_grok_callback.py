"""Offline G4 Grok Webhook/Callback regression; every Routine and report is fake."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import unittest
import urllib.request
from unittest.mock import patch
from uuid import uuid4

from fast_voice.contracts import ProviderError, ToolCallEvent, TurnCompleteEvent
from relay.dispatcher import AsyncDispatcher
from relay.grok_callback import create_grok_callback_app
from relay.grok_callback_executor import GrokCallbackExecutor, GrokConfigError, GrokRoutineConfig, _post_once
from relay.slow_path import (
    INBOX_TOOL_NAME,
    MAX_GROK_TASK_CHARS,
    SlowPathToolRouter,
    build_cancel_task_tool,
    build_check_task_status_tool,
)
from relay.task_store import AdmissionBusy, SQLiteTaskStore, TaskAdmissionRequest

CLIENT = "grok-owner"
CALLBACK_URL = "https://callback.example.com:8443/callback/results"
ROUTINE_URL = "https://routine.example.com/webhook"
PROXY_URL = "http://egress_proxy:3128"


def _report(task_id: str, *, outcome: str = "completed", speak: str = "The fixed Grok fixture answer."):
    return {
        "schema_version": "amusant-grok-pilot-0",
        "task_id": task_id,
        "reported_outcome": outcome,
        "speak": speak,
        "card": {"title": "Grok fixture", "lines": ["One fixed result."]},
        "native_result_locator": "box:/private/native/path.md",
    }


async def _asgi_call(app, payload=None, *, token="fake-token", host="callback.example.com:8443", method="POST", path="/callback/results", extra_headers=(), raw_body=None):
    body = raw_body if raw_body is not None else json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"host", host.encode("ascii")),
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"authorization", f"Bearer {token}".encode("ascii")),
        *extra_headers,
    ]
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "method": method, "path": path, "query_string": b"",
        "headers": headers, "scheme": "https", "server": ("callback.example.com", 8443),
    }, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response = next(message for message in messages if message["type"] == "http.response.body")
    return start["status"], json.loads(response["body"] or b"{}")


class GrokCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f".grok-callback-{uuid4().hex}"
        self.root.mkdir()
        self.addCleanup(self._cleanup_root)
        self.database = self.root / "ledger.sqlite3"
        self.store = SQLiteTaskStore(database_path=self.database, max_pending_tasks=8)
        self.now = datetime.now(timezone.utc)
        self.task_id = str(uuid4())
        self.store.admit(TaskAdmissionRequest(
            task_id=self.task_id,
            client_id=CLIENT,
            conversation_id="conversation-1",
            turn_id=1,
            call_id="grok-call-1",
            task="Return the fixed offline fixture answer.",
            task_kind="agent",
            backend="grok_bot",
            created_at=self.now,
            expires_at=self.now + timedelta(minutes=10),
            executor_kind="grok_callback",
        ))
        self.claimed = self.store.claim_due(now=self.now, lease_seconds=30)[0]
        self.callback_app = create_grok_callback_app(
            self.store, CLIENT, trusted_hosts=["callback.example.com"],
            expected_port=8443, max_inflight=2,
        )

    def tearDown(self):
        pass

    def _cleanup_root(self):
        for path in self.root.iterdir():
            if path.is_file() and not path.is_symlink():
                path.chmod(0o666)
            path.unlink(missing_ok=True)
        self.root.rmdir()

    def make_executor(self, transport, *, clock=None):
        return GrokCallbackExecutor(
            GrokRoutineConfig(
                routine_url=ROUTINE_URL,
                routine_token="fake-routine-ingress-token",
                callback_url=CALLBACK_URL,
                egress_proxy_url=PROXY_URL,
                callback_token_ttl_seconds=3_600,
                callback_wait_seconds=90,
            ),
            begin_submission=self.store.begin_grok_submission,
            finish_submission=self.store.finish_grok_submission,
            fail_before_send=self.store.fail,
            transport=transport,
            clock=clock or (lambda: self.now),
        )

    async def test_badge_callback_read_reconnect_and_isolation(self):
        from relay.live_bridge import InboxBadgeSessions
        hub = InboxBadgeSessions()
        hub.store = self.store
        frames = []
        async def send(frame):
            frames.append(frame)
        key = hub.register(CLIENT, send)
        await hub._sessions[key]["task"]
        self.assertEqual(frames, [{"type": "inbox", "count": 0}])
        token = "fake-token"
        self.store.begin_grok_submission(
            self.task_id, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=self.now + timedelta(minutes=1),
            callback_deadline_at=self.now + timedelta(seconds=30), now=self.now)
        app = create_grok_callback_app(self.store, CLIENT, trusted_hosts=["callback.example.com"],
                                       expected_port=8443, max_inflight=2, on_committed=hub.notify)
        status, _ = await _asgi_call(app, _report(self.task_id, outcome="not_completed"), token="wrong-token")
        self.assertEqual(status, 401)
        self.assertEqual(len(frames), 1)
        status, _ = await _asgi_call(app, _report(self.task_id, outcome="not_completed"), token=token)
        self.assertEqual(status, 200)
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1], {"type": "inbox", "count": 1})
        self.assertFalse(self.store.inbox_ready(client_id="other-owner"))
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "pending")
        status, receipt = await _asgi_call(app, _report(self.task_id, outcome="not_completed"), token=token)
        self.assertEqual(status, 200)
        self.assertTrue(receipt["duplicate"])
        self.assertEqual(len(frames), 2)
        hub.unregister(key)
        frames.clear()
        key = hub.register(CLIENT, send)
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1]["count"], 1)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="conversation-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=1, now=lambda: self.now, executor_kind="grok_callback",
            on_inbox_change=hub.notify)
        result = await router(ToolCallEvent(name=INBOX_TOOL_NAME, call_id="read",
                                            arguments={"task_id": self.task_id}), 1)
        self.assertEqual(result["delivery_state"], "read")
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1]["count"], 0)
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "read")
        self.assertTrue(await router.mark_spoken(self.task_id))
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "spoken")
        hub.unregister(key)

    async def test_timed_out_callback_still_notifies_after_durable_commit(self):
        import threading
        token = "fake-token"
        self.store.begin_grok_submission(
            self.task_id, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=self.now + timedelta(minutes=1),
            callback_deadline_at=self.now + timedelta(seconds=30), now=self.now)
        original = self.store.commit_grok_callback
        release = threading.Event()
        def delayed(**kwargs):
            release.wait(2)
            return original(**kwargs)
        self.store.commit_grok_callback = delayed
        notified = asyncio.Event()
        app = create_grok_callback_app(self.store, CLIENT, trusted_hosts=["callback.example.com"],
            expected_port=8443, max_inflight=2, store_timeout_seconds=.02,
            on_committed=lambda client: notified.set())
        try:
            status, _ = await _asgi_call(app, _report(self.task_id), token=token)
            self.assertEqual(status, 503)
            release.set()
            await asyncio.wait_for(notified.wait(), 2)
            self.assertTrue(self.store.inbox_ready(client_id=CLIENT))
        finally:
            release.set()

    async def test_completed_callback_offline_and_slow_device_do_not_block_receipt(self):
        from relay.live_bridge import InboxBadgeSessions
        hub = InboxBadgeSessions()
        hub.store = self.store
        token = "fake-token"
        self.store.begin_grok_submission(
            self.task_id, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=self.now + timedelta(minutes=1),
            callback_deadline_at=self.now + timedelta(seconds=30), now=self.now)
        started, release = asyncio.Event(), asyncio.Event()
        async def slow_send(frame):
            started.set()
            await release.wait()
        key = hub.register(CLIENT, slow_send)
        await asyncio.wait_for(started.wait(), 1)
        app = create_grok_callback_app(self.store, CLIENT, trusted_hosts=["callback.example.com"],
                                       expected_port=8443, max_inflight=2, on_committed=hub.notify)
        status, _ = await asyncio.wait_for(_asgi_call(app, _report(self.task_id), token=token), 1)
        self.assertEqual(status, 200)
        self.assertTrue(self.store.inbox_ready(client_id=CLIENT))
        hub.unregister(key)
        frames = []
        async def send(frame):
            frames.append(frame)
        key = hub.register(CLIENT, send)
        await hub._sessions[key]["task"]
        self.assertEqual(frames, [{"type": "inbox", "count": 1}])
        hub.unregister(key)

    async def test_hello_authentication_controls_badge_registration(self):
        from types import SimpleNamespace
        from relay.live_bridge import InboxBadgeSessions, make_handler
        hub = InboxBadgeSessions()
        class Store:
            def inbox_ready(self, *, client_id):
                return True
        hub.store = Store()
        class Socket:
            def __init__(self, token):
                self.request = SimpleNamespace(path="/ws", headers={"Authorization": token})
                self.frames = []
                self.closed = False
            async def close(self, **kwargs):
                self.closed = True
            async def send(self, message):
                self.frames.append(json.loads(message))
            def __aiter__(self):
                return self.messages()
            async def messages(self):
                yield json.dumps({"type": "hello", "device": "untrusted-label"})
                for _ in range(100):
                    if self.frames:
                        break
                    await asyncio.sleep(.01)
        with patch("relay.live_bridge.make_caption_normalizer", return_value=lambda text: text):
            handler = make_handler("correct-token", lambda: None,
                                   authenticated_client_id=CLIENT, inbox_badges=hub)
        wrong = Socket("Bearer wrong-token")
        await handler(wrong)
        self.assertTrue(wrong.closed)
        self.assertEqual(wrong.frames, [])
        good = Socket("Bearer correct-token")
        await handler(good)
        self.assertEqual(good.frames, [{"type": "inbox", "count": 1}])
        self.assertEqual(hub._sessions, {})

    async def test_older_unread_result_is_retained_after_latest_read(self):
        from relay.live_bridge import InboxBadgeSessions
        token = "fake-token"
        self.store.begin_grok_submission(
            self.task_id, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=self.now + timedelta(minutes=5),
            callback_deadline_at=self.now + timedelta(minutes=2), now=self.now)
        status, _ = await _asgi_call(self.callback_app, _report(self.task_id), token=token)
        self.assertEqual(status, 200)
        newer = str(uuid4())
        now = self.now + timedelta(seconds=1)
        self.store.admit(TaskAdmissionRequest(
            task_id=newer, client_id=CLIENT, conversation_id="conversation-1", turn_id=2,
            call_id="newer-call", task="Second fixture", task_kind="agent", backend="grok_bot",
            created_at=now, expires_at=now + timedelta(minutes=5), executor_kind="grok_callback"))
        self.store.claim_due(now=now, lease_seconds=30)
        self.store.begin_grok_submission(
            newer, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=now + timedelta(minutes=5),
            callback_deadline_at=now + timedelta(minutes=2), now=now)
        status, _ = await _asgi_call(self.callback_app, _report(newer), token=token)
        self.assertEqual(status, 200)
        hub = InboxBadgeSessions()
        hub.store = self.store
        frames = []
        async def send(frame):
            frames.append(frame)
        key = hub.register(CLIENT, send)
        await hub._sessions[key]["task"]
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="conversation-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=1, now=lambda: now, executor_kind="grok_callback",
            on_inbox_change=hub.notify)
        result = await router(ToolCallEvent(name=INBOX_TOOL_NAME, call_id="latest", arguments={}), 3)
        self.assertEqual(result["task_id"], newer)
        self.assertEqual(self.store.get_inbox(newer)["delivery_state"], "read")
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "pending")
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1], {"type": "inbox", "count": 0})
        hub.unregister(key)

    async def _latest_task_admission_and_result(self, outcome):
        from relay.live_bridge import InboxBadgeSessions
        token = "fake-token"
        self.store.begin_grok_submission(
            self.task_id, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=self.now + timedelta(minutes=5),
            callback_deadline_at=self.now + timedelta(minutes=2), now=self.now)
        self.assertEqual((await _asgi_call(self.callback_app, _report(self.task_id), token=token))[0], 200)
        hub = InboxBadgeSessions()
        hub.store = self.store
        frames = []
        async def send(frame):
            frames.append(frame["count"])
        key = hub.register(CLIENT, send)
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1], 1)
        now = self.now + timedelta(seconds=1)
        newer = str(uuid4())
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="conversation-2",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=1, now=lambda: now, new_task_id=lambda: newer,
            executor_kind="grok_callback", on_inbox_change=hub.notify)
        receipt = await router(ToolCallEvent(name="delegate_task", call_id="newer",
                                             arguments={"task": "Synthetic task"}), 1)
        self.assertEqual(receipt["status"], "accepted")
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1], 0)
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "pending")
        self.store.claim_due(now=now, lease_seconds=30)
        self.assertFalse(self.store.inbox_ready(client_id=CLIENT))
        self.store.begin_grok_submission(
            newer, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=now + timedelta(minutes=5),
            callback_deadline_at=now + timedelta(minutes=2), now=now)
        app = create_grok_callback_app(self.store, CLIENT, trusted_hosts=["callback.example.com"],
            expected_port=8443, max_inflight=2, on_committed=hub.notify)
        self.assertEqual((await _asgi_call(app, _report(newer, outcome=outcome), token=token))[0], 200)
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1], 1)
        result = await router(ToolCallEvent(name=INBOX_TOOL_NAME, call_id="latest", arguments={}), 2)
        self.assertEqual(result["task_id"], newer)
        await hub._sessions[key]["task"]
        self.assertEqual(frames[-1], 0)
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "pending")
        hub.unregister(key)

    async def test_latest_admission_and_completed_result_badge(self):
        await self._latest_task_admission_and_result("completed")

    async def test_latest_admission_and_not_completed_result_badge(self):
        await self._latest_task_admission_and_result("not_completed")

    async def test_badge_racing_sync_reconciles_after_new_state(self):
        from relay.live_bridge import InboxBadgeSessions
        hub = InboxBadgeSessions()
        class Store:
            ready = False
            def inbox_ready(self, *, client_id):
                return self.ready
        hub.store = Store()
        sent = []
        started = asyncio.Event()
        release = asyncio.Event()
        async def send(frame):
            sent.append(frame["count"])
            if len(sent) == 1:
                started.set()
                await release.wait()
        key = hub.register(CLIENT, send)
        await asyncio.wait_for(started.wait(), 1)
        hub.store.ready = True
        hub.notify(CLIENT)
        release.set()
        await hub._sessions[key]["task"]
        self.assertEqual(sent, [0, 1])
        hub.unregister(key)

    async def test_backend_identity_contract_and_grok_task_bound_are_server_selected(self):
        router = SlowPathToolRouter(
            store=self.store,
            client_id="identity-owner",
            conversation_id="identity-conversation",
            backend_id="gemini-live",
            default_task_kind="compare",
            max_task_ttl_seconds=600,
            acceptance_timeout_seconds=1,
            now=lambda: self.now,
            executor_kind="grok_callback",
        )
        request, error = router._build_request({"task": "Do the fixed task"}, "identity-call", 3)
        self.assertIsNone(error)
        self.assertEqual(request.backend, "grok_bot")
        self.assertEqual(request.executor_kind, "grok_callback")
        self.assertEqual(request.task_kind, "agent")
        self.assertEqual(request.result_contract_version, 2)
        _, too_long = router._build_request({"task": "x" * (MAX_GROK_TASK_CHARS + 1)}, "large-call", 4)
        self.assertEqual(too_long, {"status": "rejected", "code": "invalid_task"})

    async def test_status_and_cancel_tool_text_is_backend_truthful(self):
        class Types:
            class Behavior:
                BLOCKING = "blocking"

            class Schema:
                def __init__(self, **kwargs):
                    self.__dict__.update(kwargs)

            class FunctionDeclaration:
                def __init__(self, **kwargs):
                    self.__dict__.update(kwargs)

        grok_status = build_check_task_status_tool(Types, executor_kind="grok_callback")
        grok_cancel = build_cancel_task_tool(Types, executor_kind="grok_callback")
        hermes_status = build_check_task_status_tool(Types, executor_kind="hermes_runs")
        self.assertIn("native Run state", grok_status.description)
        self.assertIn("not_completed", grok_status.description)
        self.assertIn("no remote Stop", grok_cancel.description)
        self.assertIn("Hermes execution ended", hermes_status.description)

    async def test_default_http_transport_is_one_post_and_does_not_follow_redirect(self):
        class FakeOpener:
            def __init__(self):
                self.requests = []

            def open(self, request, timeout):
                self.requests.append((request, timeout))
                return type("Response", (), {"status": 302, "__enter__": lambda item: item,
                                              "__exit__": lambda *args: None})()

        fake_opener = FakeOpener()
        with patch("urllib.request.build_opener", return_value=fake_opener) as build:
            status = _post_once(ROUTINE_URL, b"{}", "fake-routine-token", PROXY_URL, 2.0)
        self.assertEqual(status, 302)
        self.assertEqual(len(fake_opener.requests), 1)
        self.assertEqual(fake_opener.requests[0][0].get_method(), "POST")
        proxy_handler = next(arg for arg in build.call_args.args if isinstance(arg, urllib.request.ProxyHandler))
        self.assertEqual(proxy_handler.proxies["https"], PROXY_URL)
        redirect_handler = next(arg for arg in build.call_args.args if isinstance(arg, urllib.request.HTTPRedirectHandler))
        self.assertIsNone(redirect_handler.redirect_request(None, None, 302, "Found", {}, "https://other.invalid/"))

    async def test_executor_rejects_url_embedded_credentials_and_query(self):
        for url in (
            "https://user:password@routine.example.com/webhook",
            "https://routine.example.com/webhook?token=secret",
            "https://callback.example.com:8443/callback/results?capability=secret",
        ):
            with self.subTest(url=url), self.assertRaises(GrokConfigError):
                GrokCallbackExecutor(
                    GrokRoutineConfig(
                        routine_url=url if "routine.example.com" in url else ROUTINE_URL,
                        routine_token="fake-routine-ingress-token",
                        callback_url=url if "callback.example.com" in url else CALLBACK_URL,
                        egress_proxy_url=PROXY_URL,
                    ),
                    begin_submission=self.store.begin_grok_submission,
                    finish_submission=self.store.finish_grok_submission,
                    fail_before_send=self.store.fail,
                )

    async def test_webhook_diagnostics_do_not_expose_exception_secrets(self):
        def broken(*args):
            raise TimeoutError("secret-token payload private-url")
        with self.assertLogs("relay.grok_callback_executor", level="INFO") as logs:
            outcome = await self.make_executor(broken).execute(self.claimed)
        self.assertEqual(outcome.code, "grok_acceptance_unknown")
        self.assertIn("category=timeout", logs.output[0])
        self.assertNotIn("secret-token", logs.output[0])
        self.assertNotIn("payload", logs.output[0])

    async def test_http_statuses_and_transport_errors_remain_conservative(self):
        import socket
        import ssl
        cases = [(200, "accepted", "category=http"), (401, "acceptance_unknown", "http_status=401"),
                 (403, "acceptance_unknown", "http_status=403"), (429, "acceptance_unknown", "http_status=429"),
                 (503, "acceptance_unknown", "http_status=503"), (302, "acceptance_unknown", "http_status=302"),
                 (socket.gaierror("secret"), "acceptance_unknown", "category=dns"),
                 (ssl.SSLError("secret"), "acceptance_unknown", "category=tls"),
                 (OSError("proxy secret"), "acceptance_unknown", "category=connection_or_proxy")]
        for value, expected, diagnostic in cases:
            with self.subTest(value=type(value).__name__, expected=diagnostic):
                finishes = []
                def transport(*args):
                    if isinstance(value, Exception):
                        raise value
                    return value
                executor = GrokCallbackExecutor(
                    GrokRoutineConfig(ROUTINE_URL, "fake-routine-ingress-token", CALLBACK_URL, PROXY_URL),
                    begin_submission=lambda *args, **kwargs: None,
                    finish_submission=lambda *args, **kwargs: finishes.append(kwargs["state"]),
                    fail_before_send=lambda *args, **kwargs: None, transport=transport,
                    clock=lambda: self.now,
                )
                with self.assertLogs("relay.grok_callback_executor", level="INFO") as logs:
                    await executor.execute(self.claimed)
                self.assertEqual(finishes, [expected])
                self.assertIn(diagnostic, logs.output[0])
                self.assertNotIn("secret", logs.output[0])

    async def test_reserved_host_is_rejected_before_send(self):
        with self.assertRaises(GrokConfigError):
            GrokCallbackExecutor(
                GrokRoutineConfig("https://routine.invalid/webhook", "fake-routine-ingress-token",
                                  CALLBACK_URL, PROXY_URL),
                begin_submission=self.store.begin_grok_submission,
                finish_submission=self.store.finish_grok_submission,
                fail_before_send=self.store.fail,
            )

    async def test_golden_path_callback_before_webhook_ack_and_idempotent_replay(self):
        routine_posts = 0
        business_fixtures = 0
        posted_payloads = []

        def fake_routine(url, body, bearer, proxy, timeout):
            nonlocal routine_posts, business_fixtures
            routine_posts += 1
            self.assertEqual(url, ROUTINE_URL)
            self.assertEqual(bearer, "fake-routine-ingress-token")
            self.assertEqual(proxy, PROXY_URL)
            posted = json.loads(body)
            posted_payloads.append(posted)
            business_fixtures += 1
            status, response = asyncio.run(_asgi_call(
                self.callback_app,
                _report(self.task_id, speak="The fixed Grok fixture answer."),
                token=posted["result_token"],
            ))
            self.assertEqual(status, 200)
            self.assertFalse(response["late"])
            # This callback commits before the fake Routine returns its ACK.
            return 200

        # Return the fixture to the pending state so the real Dispatcher owns
        # the claim. Grok's executor must establish its own atomic send boundary.
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE slow_tasks SET status='queued' WHERE task_id=?", (self.task_id,))
            connection.execute(
                "UPDATE dispatch_outbox SET state='pending',attempts=0,claimed_until=NULL,dispatch_started_at=NULL "
                "WHERE task_id=?",
                (self.task_id,),
            )
            connection.commit()
        finally:
            connection.close()
        executor = self.make_executor(fake_routine)
        dispatcher = AsyncDispatcher(self.store, executor, now=lambda: self.now)
        self.assertEqual(await dispatcher.dispatch_available(), 1)
        self.assertEqual((routine_posts, business_fixtures), (1, 1))
        saved = self.store.get_by_task_id(self.task_id)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["executor_kind"], "grok_callback")
        self.assertEqual(saved["result_contract_version"], 2)
        self.assertEqual(saved["grok_submission_state"], "callback_received")
        self.assertIsNotNone(saved["grok_token_expires_at"])
        self.assertIsNotNone(saved["grok_callback_deadline_at"])
        self.assertNotEqual(saved["grok_token_expires_at"], saved["grok_callback_deadline_at"])

        posted = posted_payloads[0]
        report = _report(self.task_id, speak="The fixed Grok fixture answer.")
        status, replay = await _asgi_call(self.callback_app, report, token=posted["result_token"])
        self.assertEqual(status, 200)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["receipt_id"], saved["grok_callback_receipt_id"])
        conflict = _report(self.task_id, speak="A different immutable report.")
        status, _ = await _asgi_call(self.callback_app, conflict, token=posted["result_token"])
        self.assertEqual(status, 409)

        result = self.store.get_result(self.task_id)
        self.assertTrue(result["speak"].startswith("Grok 回報："))
        self.assertNotIn("native run state is unverified", result["speak"])
        self.assertIsNone(result["artifact_ref"])
        self.assertNotIn("box:/", result["details_md"])
        connection = sqlite3.connect(self.database)
        try:
            token_hash = connection.execute(
                "SELECT grok_token_sha256 FROM slow_tasks WHERE task_id=?", (self.task_id,)
            ).fetchone()[0]
            self.assertEqual(token_hash, hashlib.sha256(posted["result_token"].encode()).hexdigest())
            self.assertNotEqual(token_hash, posted["result_token"])
            self.assertEqual(connection.execute("SELECT count(*) FROM task_results WHERE task_id=?", (self.task_id,)).fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM task_inbox WHERE task_id=?", (self.task_id,)).fetchone()[0], 1)
        finally:
            connection.close()

    async def test_not_completed_is_failed_truthful_and_still_spoken_on_explicit_readback(self):
        def fake_routine(_url, body, _bearer, _proxy, _timeout):
            posted = json.loads(body)
            status, _ = asyncio.run(_asgi_call(
                self.callback_app,
                _report(self.task_id, outcome="not_completed", speak="The request was blocked."),
                token=posted["result_token"],
            ))
            self.assertEqual(status, 200)
            return 200

        await self.make_executor(fake_routine).execute(self.claimed)
        row = self.store.get_by_task_id(self.task_id)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["last_error"], "grok_reported_not_completed")
        router = SlowPathToolRouter(
            store=self.store, client_id=CLIENT, conversation_id="conversation-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=1, now=lambda: self.now,
            executor_kind="grok_callback",
        )
        response = await router(ToolCallEvent(
            name=INBOX_TOOL_NAME,
            call_id="readback",
            arguments={"task_id": self.task_id, "speak": True},
        ), 2)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["reported_outcome"], "not_completed")
        self.assertTrue(response["speak"].startswith("Grok 回報這項工作未完成："))
        self.assertTrue(response["speak_now"])
        self.assertEqual(response["native_state"], "unknown")
        self.assertTrue(await router.mark_spoken(self.task_id))
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "spoken")

    async def test_bad_owner_token_host_echo_and_expired_capability_fail_closed(self):
        token = "fake-token"
        self.store.begin_grok_submission(
            self.task_id,
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=self.now + timedelta(minutes=1),
            callback_deadline_at=self.now + timedelta(seconds=30),
            now=self.now,
        )
        payload = _report(self.task_id)
        status, _ = await _asgi_call(self.callback_app, payload, token="wrong-token")
        self.assertEqual(status, 401)
        status, _ = await _asgi_call(self.callback_app, payload, token=token, host="attacker.invalid:8443")
        self.assertEqual(status, 400)
        status, _ = await _asgi_call(self.callback_app, payload, token=token, host="callback.example.com")
        self.assertEqual(status, 400)
        echoed = {**payload, "untrusted": f"echo={token}"}
        status, _ = await _asgi_call(self.callback_app, echoed, token=token)
        self.assertEqual(status, 400)
        other_owner_app = create_grok_callback_app(
            self.store, "other-owner", trusted_hosts=["callback.example.com"],
            expected_port=8443, max_inflight=1,
        )
        status, _ = await _asgi_call(other_owner_app, payload, token=token)
        self.assertEqual(status, 401)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE slow_tasks SET grok_token_expires_at=? WHERE task_id=?",
                ((self.now - timedelta(seconds=1)).isoformat(), self.task_id),
            )
            connection.commit()
        finally:
            connection.close()
        status, _ = await _asgi_call(self.callback_app, payload, token=token)
        self.assertEqual(status, 401)
        self.assertIsNone(self.store.get_result(self.task_id))

    async def test_timeout_restart_and_sender_crash_never_repost(self):
        attempts = 0

        def timeout_transport(*_args):
            nonlocal attempts
            attempts += 1
            return 504

        await self.make_executor(timeout_transport).execute(self.claimed)
        self.assertEqual(attempts, 1)
        self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "uncertain")
        self.store.recover_after_restart()
        self.assertEqual(self.store.claim_due(now=self.now + timedelta(minutes=1), lease_seconds=30), [])
        self.assertEqual(attempts, 1)

        # A crash after the durable send boundary and before the network call
        # is also conservative: restart marks it uncertain and never resends.
        second_id = str(uuid4())
        self.store.admit(TaskAdmissionRequest(
            task_id=second_id, client_id="another-owner", conversation_id="c2", turn_id=1,
            call_id="crash-call", task="Crash fixture", task_kind="agent", backend="grok_bot",
            created_at=self.now, expires_at=self.now + timedelta(minutes=10), executor_kind="grok_callback",
        ))
        self.store.claim_due(now=self.now, lease_seconds=30)
        token = "crash-capability"
        self.store.begin_grok_submission(
            second_id, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            token_expires_at=self.now + timedelta(hours=1),
            callback_deadline_at=self.now + timedelta(minutes=5), now=self.now,
        )
        self.store.recover_after_restart()
        self.assertEqual(self.store.get_by_task_id(second_id)["status"], "uncertain")
        self.assertEqual(self.store.claim_due(now=self.now + timedelta(minutes=1), lease_seconds=30), [])
        self.assertEqual(attempts, 1)

    async def test_cancel_before_send_is_local_after_send_requires_owner_abandon(self):
        before = self.store.request_cancel(client_id=CLIENT, task_id=self.task_id, now=self.now)
        self.assertEqual((before.status, before.code), ("cancelled", "grok_cancelled_before_send"))
        send_count = 0

        def no_send(*_args):
            nonlocal send_count
            send_count += 1
            return 200

        await self.make_executor(no_send).execute(self.claimed)
        self.assertEqual(send_count, 0)

        second_id = str(uuid4())
        self.store.admit(TaskAdmissionRequest(
            task_id=second_id, client_id="another-owner", conversation_id="c2", turn_id=1,
            call_id="after-send-call", task="After send fixture", task_kind="agent", backend="grok_bot",
            created_at=self.now, expires_at=self.now + timedelta(minutes=10), executor_kind="grok_callback",
        ))
        claimed = self.store.claim_due(now=self.now, lease_seconds=30)[0]
        captured = {}

        def accepted_without_callback(_url, body, _bearer, _proxy, _timeout):
            captured.update(json.loads(body))
            return 200

        await self.make_executor(accepted_without_callback).execute(claimed)
        cancel = self.store.request_cancel(client_id="another-owner", task_id=second_id, now=self.now)
        self.assertEqual((cancel.status, cancel.code), ("uncertain", "grok_remote_stop_unsupported"))
        abandon = self.store.operator_reconcile_uncertain(
            client_id="another-owner", task_id=second_id, terminal_state="abandoned",
            verified_run_id=None, operator_id="owner-1", evidence_ref="ticket:g4-abandon-1",
            now=self.now + timedelta(seconds=1),
        )
        self.assertEqual((abandon.status, abandon.code), ("abandoned", "grok_owner_abandoned"))
        inbox_before_late = self.store.get_inbox(second_id)
        notifications = []
        late_app = create_grok_callback_app(
            self.store, "another-owner", trusted_hosts=["callback.example.com"],
            expected_port=8443, max_inflight=2, on_committed=notifications.append,
        )
        late_body = _report(second_id, speak="Late but authenticated.")
        status, late = await _asgi_call(late_app, late_body, token=captured["result_token"])
        self.assertEqual(status, 200)
        self.assertTrue(late["late"])
        self.assertEqual(self.store.get_by_task_id(second_id)["status"], "failed")
        self.assertIsNone(self.store.get_result(second_id))
        self.assertEqual(self.store.get_inbox(second_id), inbox_before_late)
        status, duplicate = await _asgi_call(late_app, late_body, token=captured["result_token"])
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(notifications, [])
        revoked_id = str(uuid4())
        self.store.admit(TaskAdmissionRequest(
            task_id=revoked_id, client_id="revoked-owner", conversation_id="c3", turn_id=1,
            call_id="revoke-call", task="Revocation fixture", task_kind="agent", backend="grok_bot",
            created_at=self.now, expires_at=self.now + timedelta(minutes=10), executor_kind="grok_callback",
        ))
        revoked_task = self.store.claim_due(now=self.now, lease_seconds=30)[0]
        revoked_payload = {}
        def accepted_for_revoke(_url, body, _bearer, _proxy, _timeout):
            revoked_payload.update(json.loads(body))
            return 200
        await self.make_executor(accepted_for_revoke).execute(revoked_task)
        revoked = self.store.operator_reconcile_uncertain(
            client_id="revoked-owner", task_id=revoked_id, terminal_state="abandoned", verified_run_id=None,
            operator_id="owner-1", evidence_ref="ticket:g4-abandon-revoke", now=self.now,
            revoke_callback_token=True,
        )
        self.assertEqual(revoked.status, "abandoned")
        revoked_app = create_grok_callback_app(
            self.store, "revoked-owner", trusted_hosts=["callback.example.com"],
            expected_port=8443, max_inflight=1,
        )
        status, _ = await _asgi_call(
            revoked_app, _report(revoked_id), token=revoked_payload["result_token"]
        )
        self.assertEqual(status, 401)

    async def test_ptt_abandon_requires_later_single_use_confirmation(self):
        captured = {}
        def accepted(_url, body, _bearer, _proxy, _timeout):
            captured.update(json.loads(body))
            return 200
        await self.make_executor(accepted).execute(self.claimed)
        router = SlowPathToolRouter(
            store=self.store, client_id=CLIENT, conversation_id="session-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now,
        )
        async def call(turn, **args):
            return await router(ToolCallEvent("call-" + str(uuid4()), "cancel_task", args), turn)
        self.assertEqual((await call(1, confirm=True))["status"], "rejected")
        warning = await call(2)
        self.assertEqual(warning["status"], "confirmation_required")
        self.assertEqual(warning["message"], "確定放棄嗎？Grok 可能繼續執行唷。")
        self.assertEqual((await call(2, confirm=True))["status"], "rejected")
        unchanged = self.store.get_by_task_id(self.task_id)
        self.assertEqual(unchanged["status"], "running")
        self.assertEqual(unchanged["dispatch_state"], "sent")
        self.assertEqual(unchanged["last_error"], None)
        self.assertEqual((await call(3))["status"], "confirmation_required")
        self.assertEqual((await call(4, task_id="wrong", confirm=True))["status"], "rejected")
        self.assertEqual((await call(5))["status"], "confirmation_required")
        results = await asyncio.gather(call(6, confirm=True), call(6, confirm=True))
        self.assertEqual(sorted(r["status"] for r in results), ["abandoned", "rejected"])
        status, late = await _asgi_call(self.callback_app, _report(self.task_id), token=captured["result_token"])
        self.assertEqual(status, 200)
        self.assertTrue(late["late"])
        self.assertIsNone(self.store.get_result(self.task_id))
        # Only a later explicit request is admitted; confirmation itself creates no task.
        fresh_id = str(uuid4())
        fresh = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now,
            new_task_id=lambda: fresh_id)
        admitted = await fresh(ToolCallEvent("new-work", "delegate_task", {"task": "Return a synthetic answer."}), 7)
        self.assertEqual((admitted["status"], admitted["task_id"]), ("accepted", fresh_id))

    async def test_ptt_wrong_pre_send_task_id_rejects_without_releasing_slot(self):
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        before = self.store.get_by_task_id(self.task_id)
        result = await router(ToolCallEvent("wrong-id", "cancel_task", {"task_id": "stale-task-id"}), 1)
        self.assertEqual((result["status"], result["code"]), ("rejected", "task_mismatch"))
        after = self.store.get_by_task_id(self.task_id)
        for field in ("status", "dispatch_state", "dispatch_started_at", "last_error", "stop_requested_at", "grok_submission_state"):
            self.assertEqual(after[field], before[field], field)
        with self.assertRaises(AdmissionBusy):
            self.store.admit(TaskAdmissionRequest(
                task_id=str(uuid4()), client_id=CLIENT, conversation_id="new-session", turn_id=2,
                call_id="new-work-call", task="A separate synthetic task.", task_kind="agent",
                backend="grok_bot", created_at=self.now, expires_at=self.now + timedelta(minutes=10),
                executor_kind="grok_callback",
            ))

    async def test_ptt_matching_pre_send_task_id_cancels(self):
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        result = await router(ToolCallEvent("matching-id", "cancel_task", {"task_id": self.task_id}), 1)
        self.assertEqual((result["status"], result["code"]), ("cancelled", "grok_cancelled_before_send"))
        self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "failed")

    async def test_ptt_abandon_expiry_restart_and_presend(self):
        def router():
            return SlowPathToolRouter(
                store=self.store, client_id=CLIENT, conversation_id="session-1",
                backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
                acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now,
            )
        # The claimed task has not crossed the send boundary.
        result = await router()(ToolCallEvent("cancel", "cancel_task", {}), 1)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["code"], "grok_cancelled_before_send")

    async def test_ptt_expired_and_new_session_cannot_confirm(self):
        captured = {}
        def accepted(_url, body, _bearer, _proxy, _timeout):
            captured.update(json.loads(body))
            return 200
        await self.make_executor(accepted).execute(self.claimed)
        def router():
            return SlowPathToolRouter(
                store=self.store, client_id=CLIENT, conversation_id="session-1",
                backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
                acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now,
            )
        first = router()
        self.assertEqual((await first(ToolCallEvent("prepare", "cancel_task", {}), 1))["status"], "confirmation_required")
        self.assertEqual((await router()(ToolCallEvent("confirm", "cancel_task", {"confirm": True}), 2))["status"], "rejected")
        task_id, turn_id, _expiry = first._pending_abandon
        first._pending_abandon = (task_id, turn_id, 0.0)
        self.assertEqual((await first(ToolCallEvent("confirm", "cancel_task", {"confirm": True}), 2))["status"], "rejected")
        unchanged = self.store.get_by_task_id(self.task_id)
        self.assertEqual(unchanged["status"], "running")
        self.assertEqual(unchanged["dispatch_state"], "sent")
        status, callback = await _asgi_call(self.callback_app, _report(self.task_id), token=captured["result_token"])
        self.assertEqual(status, 200)
        self.assertFalse(callback["late"])
        self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "completed")

    async def test_ptt_grok_cancel_fails_closed_on_other_backend_reservation(self):
        other_store = SQLiteTaskStore(database_path=self.root / "other.sqlite3", max_pending_tasks=8)
        other_id = str(uuid4())
        other_store.admit(TaskAdmissionRequest(
            task_id=other_id, client_id=CLIENT, conversation_id="other-session", turn_id=1,
            call_id="other-call", task="Synthetic task.", task_kind="agent", backend="hermes",
            created_at=self.now, expires_at=self.now + timedelta(minutes=10), executor_kind="hermes_runs",
        ))
        router = SlowPathToolRouter(store=other_store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        result = await router(ToolCallEvent("wrong-lane", "cancel_task", {}), 1)
        self.assertEqual((result["status"], result["code"]), ("unavailable", "active_task_unavailable"))
        self.assertEqual(other_store.get_by_task_id(other_id)["status"], "queued")

    async def test_ptt_stale_id_and_other_owner_do_not_release(self):
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        def router(owner):
            return SlowPathToolRouter(store=self.store, client_id=owner, conversation_id="session",
                backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
                acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        owner = router(CLIENT)
        self.assertEqual((await owner(ToolCallEvent("stale", "cancel_task", {"task_id": "stale"}), 1))["code"], "task_mismatch")
        self.assertEqual((await router("different-owner")(ToolCallEvent("other", "cancel_task", {}), 1))["status"], "not_found")
        self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "running")
        self.assertEqual((await owner(ToolCallEvent("prepare", "cancel_task", {}), 2))["status"], "confirmation_required")
        # A different tool action supersedes the pending confirmation.
        await owner(ToolCallEvent("status", "check_task_status", {}), 3)
        self.assertEqual((await owner(ToolCallEvent("confirm", "cancel_task", {"confirm": True}), 4))["status"], "rejected")
        self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "running")

    async def test_ptt_confirmation_misrouted_to_delegate_is_never_admitted(self):
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        self.assertEqual((await router(ToolCallEvent("prepare", "cancel_task", {}), 1))["status"], "confirmation_required")
        with patch.object(self.store, "admit", side_effect=AssertionError("must not admit")):
            result = await router(ToolCallEvent("misroute", "delegate_task", {"task": "synthetic confirmation"}), 2)
        self.assertEqual(result["code"], "pending_confirmation_requires_cancel_tool")
        self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "running")

    async def test_fake_live_two_distinct_ptt_turns(self):
        from fast_voice.tests.test_live_bridge import FakeProvider, TOKEN
        from relay.live_bridge import make_handler, MIN_UTTERANCE_BYTES
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        fake = FakeProvider(auto_complete=False)
        with patch("relay.live_bridge.make_caption_normalizer", return_value=lambda value: value):
            server = await serve(make_handler(TOKEN, lambda: fake, slow_path_tool_handler=router), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            async def until(predicate):
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 3)
                    if isinstance(frame, str) and predicate(json.loads(frame)):
                        return
            try:
                for turn, args in ((1, {}), (2, {"confirm": True})):
                    await ws.send(json.dumps({"type": "ptt_start"}))
                    await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                    await ws.send(json.dumps({"type": "ptt_end"}))
                    await until(lambda x: x.get("state") == "thinking")
                    await fake.callbacks["tool"](ToolCallEvent(f"cancel-{turn}", "cancel_task", args))
                    await fake.callbacks["tool_batch_complete"]()
                    await fake.emit_response()
                    await until(lambda x: x.get("type") == "turn_complete")
                self.assertEqual([x[1]["status"] for x in fake.tool_results], ["confirmation_required", "abandoned"])
                self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "failed")
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()

    async def test_superseded_prepare_cannot_leave_confirmation_armed(self):
        import threading
        from fast_voice.tests.test_live_bridge import FakeProvider, TOKEN
        from relay.live_bridge import make_handler, MIN_UTTERANCE_BYTES
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        started, release = threading.Event(), threading.Event()
        prepare = self.store.prepare_owner_cancel
        def blocked_prepare(**kwargs):
            started.set()
            if not release.wait(3):
                raise TimeoutError("fixture release timed out")
            return prepare(**kwargs)
        self.store.prepare_owner_cancel = blocked_prepare
        providers = [FakeProvider(auto_complete=False), FakeProvider(auto_complete=False)]
        with patch("relay.live_bridge.make_caption_normalizer", return_value=lambda value: value):
            server = await serve(make_handler(TOKEN, lambda: providers.pop(0), slow_path_tool_handler=router), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            first, second = providers[0], providers[1]
            async def until(predicate):
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 3)
                    if isinstance(frame, str) and predicate(json.loads(frame)):
                        return
            try:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda x: x.get("state") == "thinking")
                pending_call = asyncio.create_task(first.callbacks["tool"](ToolCallEvent("prepare", "cancel_task", {})))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda x: x.get("state") == "thinking")
                release.set()
                await pending_call
                await second.callbacks["tool"](ToolCallEvent("confirm", "cancel_task", {"confirm": True}))
                self.assertEqual(second.tool_results[-1][1]["status"], "rejected")
                self.assertEqual(self.store.get_by_task_id(self.task_id)["status"], "running")
            finally:
                release.set()
                await ws.close()
                server.close()
                await server.wait_closed()

    async def test_interrupted_prepare_cannot_arm_confirmation_after_store_returns(self):
        import threading
        from fast_voice.tests.test_live_bridge import FakeProvider, TOKEN
        from relay.live_bridge import make_handler, MIN_UTTERANCE_BYTES
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        started, release = threading.Event(), threading.Event()
        prepare = self.store.prepare_owner_cancel
        def blocked_prepare(**kwargs):
            started.set()
            if not release.wait(3):
                raise TimeoutError("fixture release timed out")
            return prepare(**kwargs)
        self.store.prepare_owner_cancel = blocked_prepare
        fake = FakeProvider(auto_complete=False)
        with patch("relay.live_bridge.make_caption_normalizer", return_value=lambda value: value):
            server = await serve(make_handler(
                TOKEN, lambda: fake, slow_path_tool_handler=router,
            ), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            async def until(predicate):
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 3)
                    if isinstance(frame, str) and predicate(json.loads(frame)):
                        return
            try:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                pending_call = asyncio.create_task(
                    fake.callbacks["tool"](ToolCallEvent("prepare-interrupted", "cancel_task", {}))
                )
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                await fake.callbacks["interrupted"](object())
                release.set()
                await pending_call
                self.assertIsNone(router._pending_abandon)
                self.assertEqual(fake.tool_results, [])
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=True))

                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                await fake.callbacks["tool"](
                    ToolCallEvent("confirm-after-interrupt", "cancel_task", {"confirm": True})
                )
                self.assertEqual(fake.tool_results[-1][1]["code"], "confirmation_unavailable")
                row = self.store.get_by_task_id(self.task_id)
                self.assertEqual((row["status"], row["dispatch_state"]), ("running", "sent"))
            finally:
                release.set()
                await ws.close()
                server.close()
                await server.wait_closed()

    async def test_failed_warning_delivery_cannot_leave_confirmation_armed(self):
        from fast_voice.tests.test_live_bridge import FakeProvider, TOKEN
        from relay.live_bridge import make_handler, MIN_UTTERANCE_BYTES
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)

        class FailingToolResultProvider(FakeProvider):
            def __init__(self):
                super().__init__(auto_complete=False)
                self.pending_at_delivery = False

            async def send_tool_result(self, call_id, result):
                self.pending_at_delivery = router._pending_abandon is not None
                raise ProviderError("gemini", "tool_result_unavailable", "synthetic provider failure")

        providers = [FailingToolResultProvider(), FakeProvider(auto_complete=False)]
        first, second = providers
        with patch("relay.live_bridge.make_caption_normalizer", return_value=lambda value: value):
            server = await serve(make_handler(
                TOKEN, lambda: providers.pop(0), slow_path_tool_handler=router,
            ), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            async def until(predicate):
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 3)
                    if isinstance(frame, str) and predicate(json.loads(frame)):
                        return json.loads(frame)
            try:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                await first.callbacks["tool"](ToolCallEvent("prepare-failed", "cancel_task", {}))
                self.assertTrue(first.pending_at_delivery)
                self.assertIsNone(router._pending_abandon)
                await until(lambda item: item.get("state") == "idle")

                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                await second.callbacks["tool"](
                    ToolCallEvent("confirm-after-failure", "cancel_task", {"confirm": True})
                )
                self.assertEqual(second.tool_results[-1][1]["code"], "confirmation_unavailable")
                row = self.store.get_by_task_id(self.task_id)
                self.assertEqual((row["status"], row["dispatch_state"]), ("running", "sent"))
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()

    async def test_warning_ack_timeout_clears_pending_abandon(self):
        from fast_voice.tests.test_live_bridge import FakeProvider, TOKEN
        from relay.live_bridge import make_handler, MIN_UTTERANCE_BYTES
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        providers = [FakeProvider(auto_complete=False), FakeProvider(auto_complete=False)]
        first, second = providers
        with patch("relay.live_bridge.make_caption_normalizer", return_value=lambda value: value):
            server = await serve(make_handler(
                TOKEN, lambda: providers.pop(0), slow_path_tool_handler=router,
                post_tool_ack_timeout_seconds=0.05, post_tool_ack_max_duration_seconds=0.1,
            ), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            async def until(predicate):
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 3)
                    if isinstance(frame, str) and predicate(json.loads(frame)):
                        return json.loads(frame)
            try:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                await first.callbacks["tool"](ToolCallEvent("prepare-timeout", "cancel_task", {}))
                self.assertEqual(first.tool_results[-1][1]["status"], "confirmation_required")
                self.assertIsNotNone(router._pending_abandon)
                await until(lambda item: item.get("state") == "idle")
                self.assertIsNone(router._pending_abandon)

                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                await second.callbacks["tool"](
                    ToolCallEvent("confirm-after-timeout", "cancel_task", {"confirm": True})
                )
                self.assertEqual(second.tool_results[-1][1]["code"], "confirmation_unavailable")
                row = self.store.get_by_task_id(self.task_id)
                self.assertEqual((row["status"], row["dispatch_state"]), ("running", "sent"))
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()

    async def test_cancelled_warning_turn_clears_pending_abandon(self):
        from fast_voice.tests.test_live_bridge import FakeProvider, TOKEN
        from relay.live_bridge import make_handler, MIN_UTTERANCE_BYTES
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve
        await self.make_executor(lambda *_args: 200).execute(self.claimed)
        router = SlowPathToolRouter(store=self.store, client_id=CLIENT, conversation_id="session",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now)
        fake = FakeProvider(auto_complete=False)
        with patch("relay.live_bridge.make_caption_normalizer", return_value=lambda value: value):
            server = await serve(make_handler(
                TOKEN, lambda: fake, slow_path_tool_handler=router,
            ), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            async def until(predicate):
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 3)
                    if isinstance(frame, str) and predicate(json.loads(frame)):
                        return
            try:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                await fake.callbacks["tool"](ToolCallEvent("prepare-cancelled", "cancel_task", {}))
                self.assertEqual(fake.tool_results[-1][1]["status"], "confirmation_required")
                self.assertIsNotNone(router._pending_abandon)
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=True))
                self.assertIsNone(router._pending_abandon)

                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                await until(lambda item: item.get("state") == "thinking")
                await fake.callbacks["tool"](
                    ToolCallEvent("confirm-after-cancel", "cancel_task", {"confirm": True})
                )
                self.assertEqual(fake.tool_results[-1][1]["code"], "confirmation_unavailable")
                row = self.store.get_by_task_id(self.task_id)
                self.assertEqual((row["status"], row["dispatch_state"]), ("running", "sent"))
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()

    async def test_ptt_abandon_callback_wins(self):
        captured = {}
        def accepted(_url, body, _bearer, _proxy, _timeout):
            captured.update(json.loads(body))
            return 200
        await self.make_executor(accepted).execute(self.claimed)
        router = SlowPathToolRouter(
            store=self.store, client_id=CLIENT, conversation_id="session-1",
            backend_id="grok_bot", default_task_kind="agent", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=3, executor_kind="grok_callback", now=lambda: self.now,
        )
        prepared = await router(ToolCallEvent("prepare", "cancel_task", {}), 1)
        self.assertEqual(prepared["status"], "confirmation_required")
        status, _ = await _asgi_call(self.callback_app, _report(self.task_id), token=captured["result_token"])
        self.assertEqual(status, 200)
        self.assertTrue(self.store.inbox_ready(client_id=CLIENT))
        result = await router(ToolCallEvent("confirm", "cancel_task", {"confirm": True}), 2)
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(self.store.get_result(self.task_id))
        self.assertTrue(self.store.inbox_ready(client_id=CLIENT))
        self.assertEqual(self.store.get_inbox(self.task_id)["delivery_state"], "pending")

    async def test_cancel_racing_webhook_ack_stays_uncertain(self):
        def accepted_after_owner_cancel(_url, _body, _bearer, _proxy, _timeout):
            receipt = self.store.request_cancel(
                client_id=CLIENT, task_id=self.task_id, now=self.now
            )
            self.assertEqual((receipt.status, receipt.code), ("uncertain", "grok_remote_stop_unsupported"))
            return 200

        await self.make_executor(accepted_after_owner_cancel).execute(self.claimed)
        record = self.store.get_by_task_id(self.task_id)
        self.assertEqual(record["status"], "uncertain")
        self.assertEqual(record["dispatch_state"], "uncertain")
        self.assertEqual(record["last_error"], "grok_remote_stop_unsupported")
        self.assertEqual(record["grok_submission_state"], "accepted")

    async def test_callback_receiver_method_body_size_and_json_guards(self):
        status, _ = await _asgi_call(self.callback_app, method="GET")
        self.assertEqual(status, 405)
        status, _ = await _asgi_call(self.callback_app, raw_body=b"{" + b"x" * 16_384)
        self.assertEqual(status, 413)
        status, _ = await _asgi_call(
            self.callback_app,
            payload=_report(self.task_id),
            extra_headers=((b"content-encoding", b"gzip"),),
        )
        self.assertEqual(status, 415)
        status, _ = await _asgi_call(
            self.callback_app,
            raw_body=(b'{"schema_version":"amusant-grok-pilot-0","schema_version":"amusant-grok-pilot-0"}'),
        )
        self.assertEqual(status, 400)

    async def test_malformed_json_types_return_400_without_mutating_ledger(self):
        def ledger_snapshot():
            connection = sqlite3.connect(self.database)
            try:
                return {
                    table: connection.execute(f"SELECT * FROM {table}").fetchall()
                    for table in ("slow_tasks", "dispatch_outbox", "task_results", "task_inbox")
                }
            finally:
                connection.close()

        before = ledger_snapshot()
        for field, value in (
            ("reported_outcome", []),
            ("reported_outcome", {}),
            ("reported_outcome", [{"nested": []}]),
            ("task_id", {"nested": []}),
            ("speak", ["invalid"]),
            ("card", {"title": {}, "lines": ["invalid"]}),
            ("card", {"title": "valid", "lines": [["invalid"]]}),
        ):
            with self.subTest(field=field, value=value):
                report = _report(self.task_id)
                report[field] = value
                status, response = await _asgi_call(self.callback_app, report)
                self.assertEqual(status, 400)
                self.assertFalse(response["accepted"])
                self.assertEqual(ledger_snapshot(), before)

    async def test_callback_receiver_supports_asgi_lifespan_without_binding_listener(self):
        incoming = [
            {"type": "lifespan.startup"},
            {"type": "lifespan.shutdown"},
        ]
        outgoing = []

        async def receive():
            return incoming.pop(0)

        async def send(message):
            outgoing.append(message)

        await self.callback_app({"type": "lifespan"}, receive, send)
        self.assertEqual(outgoing, [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ])


class GrokMigrationTests(unittest.TestCase):
    def test_v9_rows_gain_additive_v10_metadata_without_rewrite(self):
        root = Path(__file__).parent / f".grok-migration-{uuid4().hex}"
        root.mkdir()
        try:
            database = root / "legacy-v9.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    "CREATE TABLE slow_tasks (task_id TEXT PRIMARY KEY, executor_kind TEXT NOT NULL, status TEXT NOT NULL, task_text TEXT NOT NULL);"
                    "INSERT INTO slow_tasks VALUES ('legacy-hermes', 'hermes_runs', 'uncertain', 'historical task');"
                    "PRAGMA user_version=9;"
                )
            finally:
                connection.close()
            SQLiteTaskStore(database_path=database, max_pending_tasks=8)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 10)
                self.assertEqual(connection.execute(
                    "SELECT task_id,executor_kind,status,task_text,grok_submission_state "
                    "FROM slow_tasks WHERE task_id='legacy-hermes'"
                ).fetchone(), ("legacy-hermes", "hermes_runs", "uncertain", "historical task", "not_sent"))
                columns = {row[1] for row in connection.execute("PRAGMA table_info(slow_tasks)")}
                self.assertTrue({"grok_token_sha256", "grok_callback_deadline_at", "grok_callback_receipt_id"} <= columns)
            finally:
                connection.close()
        finally:
            for path in root.iterdir():
                if path.is_file() and not path.is_symlink():
                    path.chmod(0o666)
                path.unlink(missing_ok=True)
            root.rmdir()


if __name__ == "__main__":
    unittest.main()
