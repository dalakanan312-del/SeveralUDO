from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import clock_sync_proxy


class _TrackerReceiver(BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": self.rfile.read(length),
        }
        payload = b'{"ok":true,"tracker_global_day":88}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


class ClockSyncGatewayTests(unittest.TestCase):
    def setUp(self):
        self.receiver = ThreadingHTTPServer(("127.0.0.1", 0), _TrackerReceiver)
        self.receiver_thread = threading.Thread(target=self.receiver.serve_forever, daemon=True)
        self.receiver_thread.start()
        self.original_target = clock_sync_proxy.TARGET_URL
        clock_sync_proxy.TARGET_URL = f"http://127.0.0.1:{self.receiver.server_port}/api/clock/report"
        self.gateway = ThreadingHTTPServer(("127.0.0.1", 0), clock_sync_proxy.ClockSyncGateway)
        self.gateway_thread = threading.Thread(target=self.gateway.serve_forever, daemon=True)
        self.gateway_thread.start()

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.receiver.shutdown()
        self.receiver.server_close()
        self.gateway_thread.join(timeout=2)
        self.receiver_thread.join(timeout=2)
        clock_sync_proxy.TARGET_URL = self.original_target

    def test_health_and_legacy_report_forwarding(self):
        base = f"http://127.0.0.1:{self.gateway.server_port}"
        with request.urlopen(base + "/health", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["ok"])

        body = b'{"game_day":88,"hour":9,"minute":30}'
        outbound = request.Request(
            base + "/v1/clock",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer private-test-token"},
            method="POST",
        )
        with request.urlopen(outbound, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["tracker_global_day"], 88)
        self.assertEqual(_TrackerReceiver.received["path"], "/api/clock/report")
        self.assertEqual(_TrackerReceiver.received["authorization"], "Bearer private-test-token")
        self.assertEqual(_TrackerReceiver.received["body"], body)

        with self.assertRaises(error.HTTPError) as missing:
            request.urlopen(base + "/missing", timeout=2)
        self.assertEqual(missing.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
