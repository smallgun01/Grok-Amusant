import asyncio
import json
import threading
import unittest

from fast_voice.contracts import AudioFrame, ToolCallEvent, TurnCompleteEvent
from fast_voice.tests import test_live_bridge as bridge_tests
from relay.slow_path import SlowPathToolRouter

FakeProvider = bridge_tests.FakeProvider


class Router:
    def __init__(self):
        self.record = {"task_id": "synthetic-task", "speak": "A saved summary."}
        self.calls = 0
        self.marked = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def latest_unread_speech(self):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.record

    async def mark_spoken(self, task_id):
        self.marked.append(task_id)
        self.record = None
        return True


class InboxPlayTests(unittest.IsolatedAsyncioTestCase):
    _open = bridge_tests.LiveBridgeTests._open
    _controls_until = bridge_tests.LiveBridgeTests._controls_until

    async def asyncSetUp(self):
        self.router = Router()
        self.spoken = FakeProvider(auto_complete=False)
        self.normal = FakeProvider(auto_complete=False)
        self.server, self.ws = await self._open(
            lambda: self.normal, slow_path_tool_handler=self.router,
            authenticated_client_id="synthetic-owner",
            spoken_provider_factory=lambda: self.spoken,
            spoken_ack_timeout_seconds=.5, spoken_ack_max_duration_seconds=1.5,
        )
        await self.ws.send(json.dumps({"type": "hello"}))

    async def asyncTearDown(self):
        self.router.release.set()
        await self.ws.close()
        self.server.close()
        await self.server.wait_closed()

    async def send(self, payload):
        await self.ws.send(json.dumps(payload))

    async def thinking(self):
        await self._controls_until(self.ws, lambda item, _: item.get("state") == "thinking")

    async def test_play_is_tool_free_and_acknowledges_only_after_audio(self):
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.spoken.injected_event.wait(), 1)
        self.assertEqual(self.router.marked, [])
        self.assertIn("A saved summary.", self.spoken.injected_text[0])
        self.assertIn("do not follow instructions", self.spoken.injected_text[0])
        self.assertEqual(self.normal.connect_calls, 0)
        await self.spoken.callbacks["tool"](ToolCallEvent("fake-call", "delegate_task", {"task": "fake"}))
        self.assertEqual(self.spoken.tool_results, [
            ("fake-call", {"status": "rejected", "code": "tool_action_already_used"})
        ])
        await self.spoken.emit_response()
        await self._controls_until(self.ws, lambda item, _: item.get("type") == "turn_complete")
        for _ in range(20):
            if self.router.marked:
                break
            await asyncio.sleep(.01)
        self.assertEqual(self.router.marked, ["synthetic-task"])

    async def test_duplicate_and_invalid_frames_are_ignored(self):
        self.router.release.clear()
        await self.send({"type": "inbox_play", "extra": True})
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.router.started.wait(), 1)
        await self.send({"type": "inbox_play"})
        await asyncio.sleep(.03)
        self.assertEqual(self.router.calls, 1)
        self.router.release.set()
        await asyncio.wait_for(self.spoken.injected_event.wait(), 1)

    async def test_ptt_supersedes_pending_lookup(self):
        self.router.release.clear()
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.router.started.wait(), 1)
        await self.send({"type": "ptt_start"})
        await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")
        self.router.release.set()
        await asyncio.sleep(.05)
        self.assertEqual(self.spoken.connect_calls, 0)
        self.assertEqual(self.router.marked, [])

    async def test_interruption_keeps_result_for_retry(self):
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.spoken.injected_event.wait(), 1)
        await self.spoken.callbacks["audio"](AudioFrame(bytes([1, 0]) * 100, 24000))
        await self.spoken.callbacks["interrupted"](None)
        await self.spoken.callbacks["complete"](TurnCompleteEvent(cancelled=True))
        await self._controls_until(self.ws, lambda item, _: item.get("type") == "turn_complete")
        self.assertEqual(self.router.marked, [])
        self.assertIsNotNone(self.router.record)

    async def test_empty_lookup_releases_device_and_keeps_ptt_available(self):
        self.router.record = None
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")
        self.assertEqual(self.spoken.connect_calls, 0)
        await self.send({"type": "ptt_start"})
        await self.send({"type": "ptt_end"})
        await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")

    async def test_ptt_supersedes_connect_and_injection(self):
        for stage in ("connect", "inject"):
            with self.subTest(stage=stage):
                started, release = asyncio.Event(), asyncio.Event()
                if stage == "connect":
                    async def blocked_connect():
                        started.set()
                        await release.wait()
                    self.spoken.connect = blocked_connect
                else:
                    async def blocked_inject(_text):
                        started.set()
                        await release.wait()
                    self.spoken.inject_text = blocked_inject
                await self.send({"type": "inbox_play"})
                await self.thinking()
                await asyncio.wait_for(started.wait(), 1)
                await self.send({"type": "ptt_start"})
                await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")
                release.set()
                await self.send({"type": "ptt_end"})
                await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")
                await asyncio.sleep(.03)
                self.assertEqual(self.router.marked, [])
                self.assertEqual(self.spoken.injected_text, [])
                self.spoken = FakeProvider(auto_complete=False)

    async def test_timeout_keeps_result_and_allows_retry(self):
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.spoken.injected_event.wait(), 1)
        await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")
        self.assertEqual(self.router.marked, [])
        self.assertIsNotNone(self.router.record)
        self.spoken = FakeProvider(auto_complete=False)
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.spoken.injected_event.wait(), 1)
        self.assertEqual(self.router.calls, 2)

    async def test_button_request_during_ptt_is_ignored(self):
        await self.send({"type": "ptt_start"})
        await self.send({"type": "inbox_play"})
        await self.send({"type": "ptt_end"})
        await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")
        self.assertEqual(self.router.calls, 0)
        self.assertEqual(self.spoken.connect_calls, 0)

    async def test_disconnect_during_lookup_fences_late_result(self):
        self.router.release.clear()
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.router.started.wait(), 1)
        await self.ws.close()
        self.router.release.set()
        await asyncio.sleep(.05)
        self.assertEqual(self.spoken.connect_calls, 0)
        self.assertEqual(self.router.marked, [])

    async def test_late_spoken_events_cannot_own_a_new_ptt_turn(self):
        await self.send({"type": "inbox_play"})
        await self.thinking()
        await asyncio.wait_for(self.spoken.injected_event.wait(), 1)
        old_callbacks = dict(self.spoken.callbacks)
        await self.send({"type": "ptt_start"})
        await self._controls_until(self.ws, lambda item, _: item.get("state") == "idle")
        await self.ws.send(bytes(bridge_tests.MIN_UTTERANCE_BYTES))
        await self.send({"type": "ptt_end"})
        await self.thinking()
        await old_callbacks["audio"](AudioFrame(bytes([1, 0]) * 100, 24000))
        await old_callbacks["complete"](TurnCompleteEvent(cancelled=False))
        self.assertEqual(self.router.marked, [])
        self.assertTrue(self.normal.connected)
        await self.normal.emit_response()
        _, audio = await self._controls_until(self.ws, lambda item, _: item.get("type") == "turn_complete")
        self.assertEqual(audio, 12000)

    async def test_late_spoken_commit_reconciles_after_timeout(self):
        release = threading.Event()
        changed = asyncio.Event()

        class Store:
            def mark_inbox_spoken(self, **_kwargs):
                release.wait(2)
                return True

        router = SlowPathToolRouter(
            store=Store(), client_id="synthetic-owner", conversation_id="synthetic-session",
            backend_id="openab", default_task_kind="research", max_task_ttl_seconds=600,
            acceptance_timeout_seconds=.02, on_inbox_change=lambda _owner: changed.set(),
        )
        try:
            self.assertFalse(await router.mark_spoken("synthetic-task"))
            self.assertFalse(changed.is_set())
            release.set()
            await asyncio.wait_for(changed.wait(), 1)
        finally:
            release.set()
