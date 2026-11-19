"""Session telemetry is saved after landing, without ever disturbing the flight code."""

import csv
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from drone.app import DroneApp
from drone.navigator import Pose
from drone.telemetry import FIELDS, Telemetry


class TelemetryLogTests(unittest.TestCase):
    def make_app(self):
        app = DroneApp.__new__(DroneApp)
        app.pose = Pose()
        app.abort = threading.Event()
        app.airborne = True
        app.tello = Mock()
        app.follower = Mock()
        app.link = Mock()
        return app

    def test_landing_writes_the_session_csv(self):
        app = self.make_app()
        telemetry = Telemetry.__new__(Telemetry)
        telemetry._lock = threading.Lock()
        telemetry.samples = [{"t": 1.0, "x": 10.5, "y": -3.0, "odo": True, "position_valid": True}]
        app.telemetry = telemetry
        with tempfile.TemporaryDirectory() as folder, \
                patch("drone.app.cfg.TELEMETRY_LOG_DIR", folder):
            app.safe_land()
            self.assertFalse(app.airborne)
            files = os.listdir(folder)
            self.assertEqual(len(files), 1)
            with open(os.path.join(folder, files[0]), newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["x"], "10.5")
            self.assertEqual(rows[0]["position_valid"], "True")
            app.save_telemetry()                       # same session: same file, rewritten
            self.assertEqual(len(os.listdir(folder)), 1)

    def test_missing_or_failing_telemetry_never_raises(self):
        app = self.make_app()
        app.safe_land()                                # no telemetry attribute at all
        app.telemetry = Mock()
        app.telemetry.export_csv.side_effect = OSError("disk full")
        self.assertIsNone(app.save_telemetry())

    def test_hold_trace_is_saved_as_json(self):
        import json
        app = self.make_app()
        app.follower.last_hold_result = {"target_cm": (0, 0, 80), "reason": "timeout",
                                         "trace": [[0.0, 5.0, 1.0, 80.0, -4.0, -0.8, 0.0]]}
        with tempfile.TemporaryDirectory() as folder, \
                patch("drone.app.cfg.TELEMETRY_LOG_DIR", folder):
            path = app.save_hold_trace("takeoff_recenter")
            with open(path, encoding="utf-8") as stream:
                saved = json.load(stream)
        self.assertEqual(saved["label"], "takeoff_recenter")
        self.assertEqual(saved["trace"][0][4], -4.0)
        self.assertIn("vx_cmd", saved["columns"])
        app.follower.last_hold_result = None
        self.assertIsNone(app.save_hold_trace("x"))

    def test_position_valid_is_exported(self):
        self.assertIn("position_valid", FIELDS)


if __name__ == "__main__":
    unittest.main()
