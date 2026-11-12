"""Puddle pipeline without a trained model, GPU or drone: a fake detector replaces YOLO."""

import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from drone import config as cfg
from drone.app import DroneApp
from drone.downcam import pixel_to_world
from drone.navigator import PathFollower, Pose
from drone.puddles import Box, Puddle, PuddleDetector

W, H = 320, 240
GRAY = np.zeros((H, W), np.uint8)


def box_at(cx, cy, half=20, conf=0.8):
    return Box(cx - half, cy - half, cx + half, cy + half, conf)


class PuddleDetectorTests(unittest.TestCase):
    def setUp(self):
        self.found = []
        self.detector = PuddleDetector(on_new=self.found.append, model=object())
        self.addCleanup(self.detector.stop)

    def detect(self, boxes, pose=(100.0, 50.0, 80.0, 0.0), located=True, t=1.0):
        with patch("drone.puddles.run_model", return_value=boxes):
            self.detector.process(GRAY, t, pose[2], pose, located)

    def test_confirmed_after_min_hits_and_reported_once(self):
        for i in range(cfg.PUDDLE_MIN_HITS + 2):
            self.detect([box_at(W / 2, H / 2)], t=1.0 + i)
        self.assertEqual(len(self.found), 1)
        puddle = self.found[0]
        self.assertEqual(puddle.id, 1)
        self.assertEqual(puddle.hits, cfg.PUDDLE_MIN_HITS + 2)
        self.assertEqual(len(self.detector.puddles()), 1)

    def test_not_confirmed_below_min_hits(self):
        for i in range(cfg.PUDDLE_MIN_HITS - 1):
            self.detect([box_at(W / 2, H / 2)], t=1.0 + i)
        self.assertEqual(self.found, [])
        self.assertEqual(self.detector.puddles(), [])
        self.assertEqual(len(self.detector.puddles(confirmed=False)), 1)

    def test_coordinates_use_the_shared_camera_geometry(self):
        pose = (120.0, -40.0, 90.0, 30.0)
        for i in range(cfg.PUDDLE_MIN_HITS):
            self.detect([box_at(200, 80)], pose=pose, t=1.0 + i)
        expected = pixel_to_world(200, 80, W, H, pose[2], pose)[:2]
        puddle = self.found[0]
        self.assertAlmostEqual(puddle.x, expected[0], places=6)
        self.assertAlmostEqual(puddle.y, expected[1], places=6)

    def test_image_centre_is_below_the_drone(self):
        for i in range(cfg.PUDDLE_MIN_HITS):
            self.detect([box_at(W / 2, H / 2)], t=1.0 + i)
        p = self.found[0]
        self.assertAlmostEqual(p.x, 100 + cfg.CAM_OFFSET_CM[0], places=6)
        self.assertAlmostEqual(p.y, 50 + cfg.CAM_OFFSET_CM[1], places=6)

    def test_no_coordinates_without_a_located_pose(self):
        boxes = [box_at(W / 2, H / 2)]
        for i in range(cfg.PUDDLE_MIN_HITS + 1):
            self.detect(boxes, located=False, t=1.0 + i)
        self.assertEqual(self.found, [])
        self.assertIsNone(boxes[0].world)
        self.assertIsNone(self.detector.sighting_near(100, 50, 0))

    def test_box_on_the_image_edge_gives_no_position(self):
        for i in range(cfg.PUDDLE_MIN_HITS + 1):
            self.detect([Box(0, 100, 40, 140, 0.9)], t=1.0 + i)
        self.assertEqual(self.found, [])

    def test_far_apart_detections_are_two_puddles(self):
        # 1 m apart on the floor at this height: further than PUDDLE_MERGE_CM
        pose = (0.0, 0.0, 150.0, 0.0)
        for i in range(cfg.PUDDLE_MIN_HITS):
            self.detect([box_at(80, H / 2), box_at(240, H / 2)], pose=pose, t=1.0 + i)
        self.assertEqual(sorted(p.id for p in self.found), [1, 2])

    def test_reset_forgets_the_old_frame(self):
        for i in range(cfg.PUDDLE_MIN_HITS):
            self.detect([box_at(W / 2, H / 2)], t=1.0 + i)
        self.detector.reset()
        self.assertEqual(self.detector.puddles(confirmed=False), [])
        self.detect([box_at(W / 2, H / 2)], t=10.0)
        self.assertEqual(self.detector.puddles(confirmed=False)[0].id, 1)

    def test_missing_model_file_switches_detection_off(self):
        detector = PuddleDetector(path="models/does_not_exist.pt")
        self.assertFalse(detector.enabled)
        self.assertIn("geen model", detector.status)
        detector.submit(GRAY, 0, 80, (0, 0, 80, 0), True)   # must not fail


class PuddleMessageAndPhotoTests(unittest.TestCase):
    def make_app(self):
        app = DroneApp.__new__(DroneApp)
        app.pose = Pose()
        app.pose.set(0, 0, 80, 0)
        app.pose.measured = True
        app.abort = threading.Event()
        app.link = Mock()
        app.mission_id = "m1"
        app.tello = Mock()
        app.follower = PathFollower(app.tello, app.pose, app.abort)
        app.follower.yaw_hold = False
        app.puddle_detector = Mock()
        app.puddle_detector.sighting_near.return_value = None
        app.save_puddle_photo = Mock()
        patch("drone.app.time.sleep").start()
        self.addCleanup(patch.stopall)
        return app

    def puddle(self, x=30.0, y=0.0):
        with patch("drone.puddles.time.time", return_value=0):
            p = Puddle(1, x, y, 20, 20, 0.9)
        p.hits = cfg.PUDDLE_MIN_HITS
        return p

    def test_confirmed_puddle_is_sent_to_the_jetson(self):
        app = self.make_app()
        app._puddle_found(self.puddle(30, -12))
        msg = app.link.send.call_args.args[0]
        self.assertEqual((msg["type"], msg["id"], msg["x"], msg["y"], msg["mission"]),
                         ("puddle", 1, 30.0, -12.0, "m1"))
        self.assertIn("confidence", msg)

    def test_no_blind_detour_without_a_measured_position(self):
        app = self.make_app()
        app.pose.measured = False
        app.follower.move_to = Mock()
        puddle = self.puddle()
        app.photograph_puddle(puddle)
        self.assertTrue(puddle.photo_tried)
        app.follower.move_to.assert_not_called()
        app.save_puddle_photo.assert_not_called()
        for call in app.tello.send_rc_control.call_args_list:
            self.assertEqual(call.args, (0, 0, 0, 0))

    def test_no_detour_to_a_puddle_outside_the_geofence(self):
        app = self.make_app()
        app.photograph_puddle(self.puddle(x=cfg.GEOFENCE["x"][1] + 50))
        app.save_puddle_photo.assert_not_called()
        self.assertFalse(any(any(c.args) for c in app.tello.send_rc_control.call_args_list))

    def test_detour_stops_when_the_drone_moves_away(self):
        app = self.make_app()
        real_send = app.follower.send_velocity

        def drift_away(vx, vy, vz, **kw):   # wrong camera signs: every step goes the other way
            x, y, z, yaw = app.pose.get()
            app.pose.set(x - 6, y, z, yaw)
            real_send(vx, vy, vz, **kw)
        app.follower.send_velocity = drift_away
        app.photograph_puddle(self.puddle(x=30))
        app.save_puddle_photo.assert_not_called()
        self.assertLess(app.pose.get()[0], 0)
        self.assertGreater(app.pose.get()[0], -30)   # stopped after about 15 cm, not 15 s
        self.assertEqual(app.tello.send_rc_control.call_args.args, (0, 0, 0, 0))

    def test_centred_puddle_is_photographed(self):
        app = self.make_app()

        def arrive(vx, vy, vz, **kw):   # an ideal drone: goes straight to the puddle
            app.pose.set(30, 0, 80, 0)
        app.follower.send_velocity = arrive
        app.puddle_detector.sighting_near.side_effect = lambda x, y, since: (since + 0.1, 30, 0, 0.9)
        with patch("drone.app.time.time", side_effect=(i * 0.1 for i in range(10000))):
            app.photograph_puddle(self.puddle(x=30))
        app.save_puddle_photo.assert_called_once()
        self.assertLess(app.save_puddle_photo.call_args.args[1], cfg.PUDDLE_CENTER_TOL_CM)


if __name__ == "__main__":
    unittest.main()
