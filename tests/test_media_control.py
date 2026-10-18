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


if __name__ == "__main__":
    unittest.main()
