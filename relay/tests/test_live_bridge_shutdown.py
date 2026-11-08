from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4
from unittest.mock import patch

from websockets.sync.client import connect as websocket_connect

from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "posix", "SIGTERM process behavior is exercised on POSIX CI")
class LiveBridgeSigtermProcessTests(unittest.TestCase):
    def test_sigterm_closes_listener_drains_boundedly_and_keeps_run_id_without_repost(self):
        state = {"posts": 0, "gets": 0}
        state_lock = threading.Lock()
        post_seen = threading.Event()
        get_started = threading.Event()

        class HermesHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def _send(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_connection_close()
                self.end_headers()
                self.wfile.write(body)

            def send_connection_close(self):
                self.send_header("Connection", "close")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                with state_lock:
                    state["posts"] += 1
                self._send({"run_id": "offline-sigterm-run"})
                post_seen.set()

            def do_GET(self):
                get_started.set()
                with state_lock:
                    state["gets"] += 1
                # Keep one real executor request in flight while SIGTERM arrives.
                time.sleep(0.8)
                self._send({"status": "running"})

        hermes = ThreadingHTTPServer(("127.0.0.1", 0), HermesHandler)
        hermes.daemon_threads = True
        hermes_thread = threading.Thread(target=hermes.serve_forever, daemon=True)
        hermes_thread.start()

        with tempfile.TemporaryDirectory(prefix="amusant-sigterm-") as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            task_id = str(uuid4())
            now = datetime.now(timezone.utc)
            store = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
            store.admit(TaskAdmissionRequest(
                task_id=task_id,
                client_id="offline-owner",
                conversation_id="offline-conversation",
                turn_id=1,
                call_id="offline-call-1",
                task="summarize the local shutdown fixture",
                task_kind="summarize",
                backend="offline-hermes",
                created_at=now,
                expires_at=now + timedelta(minutes=5),
                executor_kind="hermes_runs",
            ))

            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                relay_port = listener.getsockname()[1]

            env = dict(os.environ)
            env.update({
                "AMUSANT_DEVICE_TOKEN": "offline-device-token-123456",
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "offline-unused-gemini-key",
                "AMUSANT_CLIENT_ID": "offline-owner",
                "AMUSANT_BACKEND_ID": "offline-backend",
                "AMUSANT_TASK_STORE": "sqlite",
                "AMUSANT_TASK_DB": str(db_path),
                "AMUSANT_SLOW_EXECUTOR": "hermes_runs",
                "AMUSANT_HERMES_RUNS_URL": f"http://127.0.0.1:{hermes.server_port}",
                "AMUSANT_FACADE_API_KEY": "offline-hermes-api-key-123",
                "AMUSANT_HERMES_ALLOW_PLAINTEXT": "1",
                "AMUSANT_HERMES_RUN_DEADLINE_SECONDS": "60",
                "AMUSANT_HERMES_POLL_CYCLE_SECONDS": "10",
                "AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS": "2",
                "AMUSANT_HERMES_POLL_INTERVAL_SECONDS": "0.1",
                "AMUSANT_HERMES_STOP_GRACE_SECONDS": "1",
                "AMUSANT_HERMES_STOP_MAX_ATTEMPTS": "2",
                "AMUSANT_HERMES_STOP_RETRY_BASE_SECONDS": "0.1",
                "AMUSANT_HERMES_STOP_RETRY_MAX_SECONDS": "0.2",
                "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS": "0.2",
            })
            process = subprocess.Popen(
                [sys.executable, "-m", "relay.live_bridge", "--host", "127.0.0.1", "--port", str(relay_port)],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            def wait_for_listener(child, port):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and child.poll() is None:
                    try:
                        with websocket_connect(
                            f"ws://127.0.0.1:{port}/listener-probe",
                            open_timeout=0.5,
                            close_timeout=0.5,
                        ):
                            return
                    except OSError:
                        time.sleep(0.02)
                self.fail("Relay listener did not start")

            try:
                wait_for_listener(process, relay_port)
                self.assertTrue(post_seen.wait(10), "the seeded task was not submitted to the local Hermes fixture")
                self.assertTrue(get_started.wait(5), "the Hermes Run was not polled")

                run_id_deadline = time.monotonic() + 5
                task_row = None
                while time.monotonic() < run_id_deadline:
                    task_row = store.get_by_task_id(task_id)
                    if task_row and task_row.get("backend_run_id") == "offline-sigterm-run":
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(task_row)
                self.assertEqual(task_row.get("backend_run_id"), "offline-sigterm-run")

                started = time.monotonic()
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=6)
                elapsed = time.monotonic() - started
                self.assertEqual(process.returncode, 0, msg=f"stdout={stdout}\nstderr={stderr}")
                self.assertLess(elapsed, 5)
                self.assertNotIn("Traceback", stderr)
                self.assertNotIn("CancelledError", stderr)

                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", relay_port), timeout=0.2)
                time.sleep(0.05)
                with state_lock:
                    self.assertEqual(state["posts"], 1, "shutdown must not submit the same task twice")

                reopened = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
                recovered = reopened.get_by_task_id(task_id)
                self.assertEqual(recovered["backend_run_id"], "offline-sigterm-run")

                with state_lock:
                    prior_gets = state["gets"]
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    restart_port = listener.getsockname()[1]
                restarted = subprocess.Popen(
                    [sys.executable, "-m", "relay.live_bridge", "--host", "127.0.0.1", "--port", str(restart_port)],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    wait_for_listener(restarted, restart_port)
                    get_deadline = time.monotonic() + 5
                    while time.monotonic() < get_deadline:
                        with state_lock:
                            resumed_gets = state["gets"]
                            submitted = state["posts"]
                        if resumed_gets > prior_gets:
                            break
                        if restarted.poll() is not None:
                            break
                        time.sleep(0.02)
                    self.assertGreater(resumed_gets, prior_gets, "restart should resume the known Run by GET")
                    self.assertEqual(submitted, 1, "a persisted Run ID must never be submitted again")
                    restarted.send_signal(signal.SIGTERM)
                    restart_stdout, restart_stderr = restarted.communicate(timeout=6)
                    self.assertEqual(restarted.returncode, 0, msg=f"stdout={restart_stdout}\nstderr={restart_stderr}")
                    self.assertNotIn("Traceback", restart_stderr)
                    self.assertNotIn("CancelledError", restart_stderr)
                    with self.assertRaises(OSError):
                        socket.create_connection(("127.0.0.1", restart_port), timeout=0.2)
                finally:
                    if restarted.poll() is None:
                        restarted.kill()
                        restarted.communicate(timeout=5)

                with state_lock:
                    self.assertEqual(state["posts"], 1, "the restarted Relay must continue GET-only")
                with sqlite3.connect(db_path) as connection:
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
                hermes.shutdown()
                hermes.server_close()
                hermes_thread.join(timeout=2)


@unittest.skipUnless(os.name == "posix", "SIGTERM process behavior is exercised on POSIX CI")
class GrokBridgeSigtermProcessTests(unittest.TestCase):
    def test_degraded_fast_shutdown_and_restart_preserve_queued_task(self):
        with tempfile.TemporaryDirectory(prefix="amusant-grok-shutdown-") as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            task_id = str(uuid4())
            now = datetime.now(timezone.utc)
            store = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
            store.admit(TaskAdmissionRequest(
                task_id=task_id, client_id="synthetic-owner", conversation_id="synthetic-conversation",
                turn_id=1, call_id="synthetic-call", task="synthetic task", task_kind="agent",
                backend="grok_bot", created_at=now, expires_at=now + timedelta(minutes=5),
                executor_kind="grok_callback",
            ))
            env = dict(os.environ)
            env.update({
                "AMUSANT_DEVICE_TOKEN": "synthetic-device-token",
                "AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "synthetic-unused-key",
                "AMUSANT_CLIENT_ID": "synthetic-owner", "AMUSANT_BACKEND_ID": "grok_bot",
                "AMUSANT_TASK_STORE": "sqlite", "AMUSANT_TASK_DB": str(db_path),
                "AMUSANT_SLOW_EXECUTOR": "grok_callback",
                "AMUSANT_GROK_CALLBACK_URL": "https://callback.example.invalid/callback/results",
                "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS": "0.2",
            })
            for _ in range(2):
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                process = subprocess.Popen(
                    [sys.executable, "-m", "relay.live_bridge", "--host", "127.0.0.1", "--port", str(port)],
                    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            with websocket_connect(
                                f"ws://127.0.0.1:{port}/listener-probe",
                                additional_headers={"Authorization": "Bearer synthetic-device-token"},
                                open_timeout=0.5, close_timeout=0.5, proxy=None,
                            ):
                                break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail("synthetic Relay listener did not start")
                    started = time.monotonic()
                    process.send_signal(signal.SIGTERM)
                    stdout, stderr = process.communicate(timeout=6)
                    self.assertEqual(process.returncode, 0, msg=f"stdout={stdout}\nstderr={stderr}")
                    self.assertLess(time.monotonic() - started, 5)
                    self.assertNotIn("Traceback", stderr)
                    with self.assertRaises(OSError):
                        socket.create_connection(("127.0.0.1", port), timeout=0.2)
                    reopened = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
                    task = reopened.get_by_task_id(task_id)
                    self.assertEqual(task["task_id"], task_id)
                    self.assertEqual(task["status"], "queued")
                    self.assertEqual(task["dispatch_state"], "pending")
                    self.assertIsNone(task["backend_run_id"])
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)


class ActiveGrokDispatcherShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_dispatcher_drains_and_restart_never_resends_ambiguous_submission(self):
        from relay import live_bridge
        from relay.dispatcher import AsyncDispatcher

        with tempfile.TemporaryDirectory(prefix="synthetic-active-grok-") as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            task_id = str(uuid4())
            now = datetime.now(timezone.utc)
            store = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
            store.admit(TaskAdmissionRequest(
                task_id=task_id, client_id="synthetic-owner", conversation_id="synthetic-conversation",
                turn_id=1, call_id="synthetic-call", task="synthetic task", task_kind="agent",
                backend="grok_bot", created_at=now, expires_at=now + timedelta(minutes=5),
                executor_kind="grok_callback",
            ))
            entered = asyncio.Event()
            cancelled = asyncio.Event()
            calls = []
            starts = []
            stops = []
            signals = []
            dispatcher_started = asyncio.Event()

            class FakeGrokExecutor:
                async def execute(self, task):
                    calls.append(task.task_id)
                    current = datetime.now(timezone.utc)
                    await asyncio.to_thread(
                        store.begin_grok_submission, task.task_id,
                        token_sha256=hashlib.sha256(b"synthetic-capability").hexdigest(),
                        token_expires_at=current + timedelta(minutes=4),
                        callback_deadline_at=current + timedelta(minutes=2), now=current,
                    )
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()

            @asynccontextmanager
            async def fake_callback(_app, _host, _port):
                task = asyncio.create_task(asyncio.Event().wait())
                try:
                    yield task
                finally:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

            start_original, stop_original = AsyncDispatcher.start, AsyncDispatcher.stop
            async def traced_start(dispatcher):
                await start_original(dispatcher)
                starts.append(dispatcher)
                dispatcher_started.set()
            async def traced_stop(dispatcher):
                stops.append(dispatcher)
                await stop_original(dispatcher)
            def install(stop_event, _loop):
                signals.append(stop_event)
                return ()
            env = {
                "AMUSANT_DEVICE_TOKEN": "synthetic-device-token",
                "AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "synthetic-unused-key",
                "AMUSANT_CLIENT_ID": "synthetic-owner", "AMUSANT_BACKEND_ID": "grok_bot",
                "AMUSANT_TASK_STORE": "sqlite", "AMUSANT_TASK_DB": str(db_path),
                "AMUSANT_SLOW_EXECUTOR": "grok_callback",
                "AMUSANT_GROK_CALLBACK_URL": "https://callback.example.com/callback/results",
                "AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": "callback.example.com",
                "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS": "0.2",
            }
            executor = FakeGrokExecutor()
            with (patch.dict(os.environ, env, clear=True),
                  patch("relay.application.build_task_store", return_value=store),
                  patch("relay.application.build_live_provider", return_value=object()),
                  patch("relay.application.load_grok_callback_executor", return_value=executor),
                  patch("relay.callback_listener.callback_listener_config", return_value=("127.0.0.1", 8645)),
                  patch("relay.callback_listener.serve_callback", side_effect=fake_callback),
                  patch("relay.live_bridge.install_shutdown_handlers", side_effect=install),
                  patch("relay.live_bridge.remove_shutdown_handlers"),
                  patch.object(AsyncDispatcher, "start", traced_start),
                  patch.object(AsyncDispatcher, "stop", traced_stop)):
                first = asyncio.create_task(live_bridge.run("127.0.0.1", 0, "synthetic-device-token"))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    self.assertEqual(len(starts), 1)
                    self.assertEqual(calls, [task_id])
                    self.assertEqual(store.get_by_task_id(task_id)["grok_submission_state"], "sending")
                    signals[0].set()
                    await asyncio.wait_for(first, 5)
                    self.assertEqual(stops, starts)
                    self.assertTrue(cancelled.is_set())
                    dispatcher_started.clear()
                    second = asyncio.create_task(live_bridge.run("127.0.0.1", 0, "synthetic-device-token"))
                    try:
                        await asyncio.wait_for(dispatcher_started.wait(), 5)
                        self.assertEqual(len(starts), 2)
                        row = store.get_by_task_id(task_id)
                        self.assertEqual(row["status"], "uncertain")
                        self.assertEqual(row["dispatch_state"], "uncertain")
                        await asyncio.sleep(0.1)
                        self.assertEqual(calls, [task_id])
                        signals[1].set()
                        await asyncio.wait_for(second, 5)
                        self.assertEqual(stops, starts)
                    finally:
                        if not second.done():
                            second.cancel()
                            await asyncio.gather(second, return_exceptions=True)
                finally:
                    if not first.done():
                        first.cancel()
                        await asyncio.gather(first, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
