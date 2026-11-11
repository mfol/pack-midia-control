import importlib.util
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "pack_midia_control.py"
media_control = None
if SOURCE.is_file():
    spec = importlib.util.spec_from_file_location("pack_midia_control", SOURCE)
    media_control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media_control)


class MediaControlTests(unittest.TestCase):
    def test_source_module_is_in_src(self):
        self.assertTrue(SOURCE.is_file(), "expected src/pack_midia_control.py")

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_only_expected_media_keys_are_mapped(self):
        expected = {"previous": 0xB1, "play-pause": 0xB3, "next": 0xB0}
        for action, virtual_key in expected.items():
            with self.subTest(action=action):
                events = media_control.build_input_sequence(action)
                self.assertEqual(events[0].ki.wVk, virtual_key)
                self.assertEqual(events[1].ki.wVk, virtual_key)
                self.assertEqual(events[1].ki.dwFlags, media_control.KEYEVENTF_KEYUP)

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_unknown_command_is_rejected(self):
        with self.assertRaises(ValueError):
            media_control.build_input_sequence("launch-shell")

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_sender_can_be_injected_without_controlling_a_player(self):
        calls = []
        def fake_send(count, events, size):
            calls.append((count, [events[i].ki.wVk for i in range(count)]))
            return count
        self.assertTrue(media_control.send_media_action("play-pause", sender=fake_send))
        self.assertEqual(calls, [(2, [0xB3, 0xB3])])

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_local_http_endpoint_dispatches_allowlisted_action(self):
        calls = []
        def fake_send(count, events, size):
            calls.append([events[i].ki.wVk for i in range(count)])
            return count
        class TestHandler(media_control.MediaHandler):
            command_sender = staticmethod(fake_send)
        server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            req = Request(base + "/media/next", data=b"{}", headers={"Origin":"null", "Content-Type":"application/json"}, method="POST")
            with urlopen(req, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "null")
            self.assertEqual(payload, {"ok": True, "action":"next"})
            self.assertEqual(calls, [[0xB0, 0xB0]])
            bad = Request(base + "/media/next", data=b"{}", headers={"Origin":"https://example.com", "Content-Type":"application/json"}, method="POST")
            with self.assertRaises(HTTPError) as err:
                urlopen(bad, timeout=3)
            self.assertEqual(err.exception.code, 403)
            err.exception.close()
            self.assertEqual(calls, [[0xB0, 0xB0]])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_summarize_usage_picks_current_month_agents_and_active_block(self):
        from datetime import date
        monthly = {"monthly": [
            {"period": "2026-10", "totalCost": 9, "totalTokens": 1},
            {"period": "2026-11", "totalCost": 12.345, "totalTokens": 500, "inputTokens": 100, "outputTokens": 50,
             "agents": [
                 {"agent": "codex", "totalCost": 2, "totalTokens": 100, "modelsUsed": ["gpt"]},
                 {"agent": "claude", "totalCost": 10.345, "totalTokens": 400, "modelsUsed": ["opus"]},
             ]},
        ]}
        blocks = {"blocks": [
            {"isActive": False, "costUSD": 1},
            {"isActive": True, "costUSD": 0.5, "totalTokens": 70, "endTime": "x",
             "projection": {"remainingMinutes": 30}, "burnRate": {"costPerHour": 3.456}},
        ]}
        out = media_control.summarize_usage(monthly, blocks, date(2026, 11, 11))
        self.assertEqual(out["period"], "2026-11")
        self.assertEqual(out["cost"], 12.35)
        self.assertEqual([a["agent"] for a in out["agents"]], ["claude", "codex"])
        self.assertEqual(out["block"]["remainingMinutes"], 30)
        self.assertEqual(out["block"]["costPerHour"], 3.46)
        empty = media_control.summarize_usage({"monthly": []}, None, date(2026, 11, 11))
        self.assertEqual((empty["cost"], empty["agents"], empty["block"]), (0, [], None))

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_usage_cache_refreshes_in_background_and_reuses_result(self):
        calls = []
        def loader():
            calls.append(1)
            return {"cost": 1.0}
        now = [0.0]
        cache = media_control.UsageCache(loader=loader, ttl=60, clock=lambda: now[0])
        self.assertEqual(cache.snapshot(), {"ok": False, "pending": True})
        cache.thread.join(timeout=3)
        first = cache.snapshot()
        self.assertTrue(first["ok"])
        self.assertEqual(first["cost"], 1.0)
        self.assertEqual(len(calls), 1)
        now[0] = 61
        cache.snapshot()
        cache.thread.join(timeout=3)
        self.assertEqual(len(calls), 2)

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_usage_cache_reports_error_when_nothing_loaded(self):
        def loader():
            raise RuntimeError("boom")
        cache = media_control.UsageCache(loader=loader)
        cache.snapshot()
        cache.thread.join(timeout=3)
        self.assertEqual(cache.snapshot()["error"], "boom")

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_usage_endpoint_requires_allowed_origin(self):
        class FakeCache:
            def snapshot(self):
                return {"ok": True, "cost": 3}
        class TestHandler(media_control.MediaHandler):
            usage = FakeCache()
            limits_reader = staticmethod(lambda: {"claude": None, "codex": {"fiveHour": {"used": 5}}})
        server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(Request(base + "/usage", headers={"Origin": "null"}), timeout=3) as response:
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(body["cost"], 3)
                self.assertEqual(body["limits"]["codex"]["fiveHour"]["used"], 5)
                self.assertIn("now", body)
            with self.assertRaises(HTTPError) as err:
                urlopen(Request(base + "/usage", headers={"Origin": "https://example.com"}), timeout=3)
            self.assertEqual(err.exception.code, 403)
            err.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_codex_limits_come_from_latest_token_count_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026" / "11" / "11"
            day.mkdir(parents=True)
            old = {"timestamp": "t1", "payload": {"type": "token_count", "rate_limits": {
                "primary": {"used_percent": 1.0, "window_minutes": 300, "resets_at": 10},
                "secondary": {"used_percent": 2.0, "window_minutes": 10080, "resets_at": 20}}}}
            new = {"timestamp": "t2", "payload": {"type": "token_count", "rate_limits": {
                "primary": {"used_percent": 17.0, "window_minutes": 300, "resets_at": 111},
                "secondary": {"used_percent": 18.0, "window_minutes": 10080, "resets_at": 222}}}}
            (day / "rollout-a.jsonl").write_text(
                json.dumps(old) + "\n{not json\n" + json.dumps({"payload": {"type": "other"}}) + "\n" + json.dumps(new) + "\n",
                encoding="utf-8")
            limits = media_control.read_codex_limits(Path(tmp))
            self.assertEqual(limits["fiveHour"], {"used": 17.0, "resetsAt": 111, "minutes": 300})
            self.assertEqual(limits["weekly"]["used"], 18.0)
            self.assertEqual(limits["recordedAt"], "t2")
            self.assertIsNone(media_control.read_codex_limits(Path(tmp) / "missing"))

    @unittest.skipIf(media_control is None, "source module not created yet")
    def test_claude_limits_come_from_statusline_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-limits.json"
            self.assertIsNone(media_control.read_claude_limits(path))
            path.write_text(json.dumps({"saved_at": 1000, "five_hour": {"used_percentage": 7, "resets_at": 99},
                                        "seven_day": {"used_percentage": 63, "resets_at": 199}}), encoding="utf-8")
            limits = media_control.read_claude_limits(path)
            self.assertEqual(limits["fiveHour"], {"used": 7.0, "resetsAt": 99, "minutes": 300})
            self.assertEqual(limits["weekly"]["used"], 63.0)

    def test_statusline_extracts_only_limits_and_renders_text(self):
        statusline = load_statusline()
        payload = {"model": {"display_name": "Opus"}, "cwd": "C:/secret",
                   "rate_limits": {"five_hour": {"used_percentage": 7.2, "resets_at": 5},
                                   "seven_day": {"used_percentage": 63, "resets_at": 9}}}
        limits = statusline.extract_limits(payload, now=42)
        self.assertEqual(set(limits), {"saved_at", "five_hour", "seven_day"})
        self.assertEqual(statusline.render(payload, limits), "Opus | 5h 7% · 7d 63%")
        self.assertIsNone(statusline.extract_limits({"model": {}}))
        self.assertEqual(statusline.render({}, None), "Claude")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sub" / "limits.json"
            statusline.save_limits(limits, target)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["five_hour"]["used_percentage"], 7.2)


def load_statusline():
    path = ROOT / "src" / "claude_statusline.py"
    spec = importlib.util.spec_from_file_location("claude_statusline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
