"""Jetson UDP link over real loopback sockets; no drone, no external network."""

import json
import socket
import threading
import unittest

from drone.jetson_link import JetsonLink


class JetsonLinkTests(unittest.TestCase):
    def make_link(self, jetson_host=None, listen_host="127.0.0.1"):
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        self.client.settimeout(2)
        self.addCleanup(self.client.close)
        self.received = []
        self.got = threading.Event()

        def on_message(msg):
            self.received.append(msg)
            self.got.set()
            if msg["type"] == "ping":
                link.send({"type": "pong", "t": msg.get("t")})

        link = JetsonLink(on_message, listen_host, 0, jetson_host, self.client.getsockname()[1])
        self.addCleanup(link.close)
        self.port = link.sock.getsockname()[1]
        return link

    def send(self, payload):
        self.client.sendto(payload if isinstance(payload, bytes) else json.dumps(payload).encode(),
                           ("127.0.0.1", self.port))

    def test_ping_round_trip_goes_back_to_the_sender(self):
        self.make_link()
        self.send({"type": "ping", "t": 1})
        data, _ = self.client.recvfrom(65535)
        self.assertEqual(json.loads(data), {"type": "pong", "t": 1})

    def test_invalid_json_and_missing_type_are_ignored(self):
        link = self.make_link()
        self.send(b"not json")
        self.send({"no_type": True})
        self.send({"type": "ping"})
        self.assertTrue(self.got.wait(2))
        self.assertEqual([m["type"] for m in self.received], ["ping"])
        self.assertEqual(link.rx_count, 1)

    def test_fixed_jetson_ip_rejects_other_senders(self):
        self.make_link(jetson_host="192.168.55.1")
        self.send({"type": "takeoff"})
        self.assertFalse(self.got.wait(0.7))

    def test_without_jetson_host_only_local_senders_may_command(self):
        link = self.make_link()
        self.assertTrue(link.accepts("127.0.0.1"))
        self.assertFalse(link.accepts("192.168.1.50"))

    def test_fixed_jetson_ip_is_the_only_accepted_sender(self):
        link = self.make_link(jetson_host="192.168.1.50")
        self.assertTrue(link.accepts("192.168.1.50"))
        self.assertFalse(link.accepts("192.168.1.51"))
        self.assertFalse(link.accepts("127.0.0.1"))

    def test_send_without_known_jetson_is_dropped(self):
        link = JetsonLink(lambda m: None, "127.0.0.1", 0, None, 9)
        self.addCleanup(link.close)
        self.assertFalse(link.send({"type": "status"}))


if __name__ == "__main__":
    unittest.main()
