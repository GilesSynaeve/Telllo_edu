"""Idle position hold: pure control logic and its safety stops, no drone."""

import math
import threading
import unittest
from unittest.mock import Mock, patch

from drone import config as cfg
from drone.app import DroneApp, FlightCommandQueue
from drone.navigator import IdleHold, PathFollower, Pose


def snap(x, y, t, measured=True, valid=True, age=0.05):
    return {"pose": (x, y, 80.0, 0.0), "measured": measured, "position_valid": valid,
            "measurement_time": None if age is None else t - age}


class IdleHoldLogicTests(unittest.TestCase):
    def test_anchors_where_the_drone_is_and_stays_still(self):
        hold = IdleHold()
        v = hold.step(snap(12, -5, 100.0), 100.0)
        self.assertEqual(hold.target, (12, -5))
        self.assertAlmostEqual(math.hypot(*v), 0.0)

    def test_pushes_back_towards_the_spot_with_a_speed_limit(self):
        hold = IdleHold()
        hold.reset((0, 0))
        vx, vy = hold.step(snap(30, 0, 100.0), 100.0)
        self.assertLess(vx, 0)
        self.assertAlmostEqual(vy, 0.0)
        self.assertLessEqual(math.hypot(vx, vy), cfg.IDLE_HOLD_MAX_CMS + 1e-9)
        vx, vy = hold.step(snap(0, -8, 100.1), 100.1)
        self.assertGreater(vy, 0)

    def test_inside_the_deadband_it_only_brakes(self):
        hold = IdleHold()
        hold.reset((0, 0))
        v = hold.step(snap(1, 1, 100.0), 100.0)
        self.assertAlmostEqual(math.hypot(*v), 0.0)   # at rest, close: nothing to do

    def test_stale_or_missing_measurement_never_steers(self):
        hold = IdleHold()
        hold.reset((0, 0))
        self.assertEqual(hold.step(snap(30, 0, 100.0, measured=False), 100.0), (0.0, 0.0))
        self.assertEqual(hold.step(snap(30, 0, 100.0, age=1.0), 100.0), (0.0, 0.0))
        self.assertEqual(hold.step(snap(30, 0, 100.0, age=None), 100.0), (0.0, 0.0))
        self.assertEqual(hold.target, (0, 0))          # the spot is kept for when tracking returns

    def test_lost_mission_frame_drops_the_target(self):
        hold = IdleHold()
        hold.reset((0, 0))
        self.assertIsNone(hold.step(snap(30, 0, 100.0, valid=False), 100.0))
        self.assertIsNone(hold.target)

    def test_too_far_from_the_spot_switches_off_for_the_flight(self):
        hold = IdleHold()
        hold.reset((0, 0))
        v = hold.step(snap(cfg.IDLE_HOLD_MAX_ERR_CM + 5, 0, 100.0), 100.0)
        self.assertEqual(v, (0.0, 0.0))
        self.assertTrue(hold.disabled_reason)
        self.assertIsNone(hold.step(snap(5, 0, 101.0), 101.0))   # stays off

    def test_growing_error_means_wrong_axes_and_switches_off(self):
        hold = IdleHold()
        hold.reset((0, 0))
        t, x = 100.0, 8.0
        while hold.disabled_reason is None and t < 110:
            hold.step(snap(x, 0, t), t)
            t += 1.0 / cfg.RC_HZ
            x += 0.4                                    # every correction makes it worse
        self.assertTrue(hold.disabled_reason)
        self.assertLess(x, cfg.IDLE_HOLD_MAX_ERR_CM)    # stopped by the growth check, early
        self.assertIn("camera", hold.disabled_reason)

    def simulate(self, sign, wind=0.0, start=25.0, seconds=6.0):
        """1-D drone: moves sign * command + wind. sign=-1 is an inverted camera axis."""
        hold = IdleHold()
        hold.reset((0, 0))
        dt, t, x, worst = 1.0 / cfg.RC_HZ, 100.0, start, start
        while t < 100.0 + seconds and hold.disabled_reason is None:
            v = hold.step(snap(x, 0, t), t)
            x += (sign * v[0] + wind) * dt
            worst = max(worst, abs(x))
            t += dt
        return hold, x, worst, t - 100.0

    def test_inverted_axis_is_caught_quickly_by_the_direction_check(self):
        hold, x, worst, seconds = self.simulate(sign=-1, start=10.0)
        self.assertIn("tegen de stuurrichting", hold.disabled_reason)
        self.assertLess(seconds, 2.0)
        self.assertLess(worst, cfg.IDLE_HOLD_MAX_ERR_CM)   # stopped before the last-resort limit

    def test_inverted_axis_far_from_the_spot_is_stopped_by_the_distance_limit(self):
        hold, x, worst, seconds = self.simulate(sign=-1, start=25.0)
        self.assertTrue(hold.disabled_reason)
        self.assertLess(seconds, 1.5)
        self.assertLess(worst, cfg.IDLE_HOLD_MAX_ERR_CM + 3)

    def test_correct_axis_converges_even_against_a_steady_wind(self):
        hold, x, worst, _ = self.simulate(sign=1, wind=5.0, seconds=20.0)
        self.assertIsNone(hold.disabled_reason)
        self.assertLess(abs(x), cfg.IDLE_HOLD_DEADBAND_CM + 2)   # the integral cancels the wind

    def test_gust_stronger_than_a_small_correction_is_not_a_wrong_axis(self):
        hold, x, _, _ = self.simulate(sign=1, wind=9.0, start=4.0, seconds=4.0)
        self.assertIsNone(hold.disabled_reason)

    def test_shrinking_error_is_not_a_fault(self):
        hold = IdleHold()
        hold.reset((0, 0))
        t, x = 100.0, 25.0
        for _ in range(100):
            hold.step(snap(x, 0, t), t)
            t += 1.0 / cfg.RC_HZ
            x *= 0.97
        self.assertIsNone(hold.disabled_reason)

    def test_config_switch_turns_it_off(self):
        hold = IdleHold()
        with patch("drone.navigator.cfg.IDLE_HOLD", False):
            self.assertIsNone(hold.step(snap(0, 0, 100.0), 100.0))


class IdleHoldAppTests(unittest.TestCase):
    def make_app(self):
        app = DroneApp.__new__(DroneApp)
        app.pose = Pose()
        app.pose.set(20, 0, 80, 0)
        app.pose.measured = True
        app.pose.measurement_time = 1000.0
        app.abort = threading.Event()
        app.tello = Mock()
        app.follower = PathFollower(app.tello, app.pose, app.abort)
        app.follower.yaw_hold = False
        app.link = Mock()
        app.idle_hold = IdleHold()
        app.idle_hold.reset((0, 0))
        app.downvision_enabled = True
        app._last_cmd_time = 0
        return app

    def test_step_sends_a_bounded_rc_command_towards_the_spot(self):
        app = self.make_app()
        with patch("drone.app.time.time", return_value=1000.05):
            self.assertTrue(app._idle_step())
        lr, fb, ud, yaw = app.tello.send_rc_control.call_args.args
        self.assertLess(fb, 0)                          # drone is 20 cm ahead: fly backwards
        self.assertGreaterEqual(fb, -cfg.IDLE_HOLD_MAX_CMS / cfg.RC_CMS_PER_UNIT - 1)
        self.assertEqual((lr, ud), (0, 0))              # never changes the height

    def test_no_steering_without_the_downward_camera(self):
        app = self.make_app()
        app.downvision_enabled = False
        self.assertFalse(app._idle_step())
        app.tello.send_rc_control.assert_not_called()

    def test_switch_off_is_reported_once(self):
        app = self.make_app()
        app.pose.set(cfg.IDLE_HOLD_MAX_ERR_CM + 10, 0, 80, 0)
        with patch("drone.app.time.time", return_value=1000.05):
            app._idle_step()
            app._idle_step()
        self.assertEqual(app.link.send.call_count, 1)
        self.assertEqual(app.link.send.call_args.args[0]["ref"], "idle_hold")

    def test_a_queued_command_runs_before_any_hold_step(self):
        app = self.make_app()
        app.airborne, app.state, app.running = True, "flying", True
        app.commands = FlightCommandQueue()
        app.commands.put({"type": "land"})
        app._idle_step = Mock()
        seen = []

        def handle(cmd):
            seen.append((cmd["type"], app.idle_hold.target))
            app.running = False
        app.handle_command = handle
        app.mission_loop()
        self.assertEqual(seen, [("land", None)])        # hold point cleared, land handled first
        app._idle_step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
