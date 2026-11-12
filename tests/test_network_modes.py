"""All network and hardware entry points are mocked; no drone is contacted."""

import contextlib
import io
import queue
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from djitellopy import Tello

from drone.app import CommandRejected, DroneApp
from drone.connection import ConfiguredTello, network_hint
from drone.network import (ConnectionOptions, NetworkSetupError, discover_drones,
                           provision_sta, validate_credentials)
from drone.navigator import Pose
from drone.telemetry import Telemetry
from drone.video import NoVideoReader, VideoCapability, station_video
from gui.connection_dialog import ConnectionDialog
from tello_gui import TelloGUI, main


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class NetworkSetupTests(unittest.TestCase):
    def test_options_validate_mode_and_ip(self):
        self.assertEqual(ConnectionOptions().host, "192.168.10.1")
        self.assertEqual(ConnectionOptions("sta", " 192.168.1.77 ").host, "192.168.1.77")
        for mode, host in (("bad", "192.168.1.77"), ("sta", ""), ("ap", "127.0.0.1"),
                           ("sta", "255.255.255.255"), ("sta", "224.0.0.1")):
            with self.subTest(mode=mode, host=host), self.assertRaises(NetworkSetupError):
                ConnectionOptions(mode, host)

    def test_credentials_reject_ambiguous_or_invalid_input_without_echo(self):
        for ssid, password in (("my home", "SECRET123"), ("Home", "SECRET 123"),
                               ("Home\nap", "SECRET123"), ("x" * 33, "SECRET123"),
                               ("Home", "short"), ("Home", "\ud800SECRET123")):
            with self.subTest(ssid=ssid):
                with self.assertRaises(NetworkSetupError) as error:
                    validate_credentials(ssid, password)
                self.assertNotIn(password, str(error.exception))

    def test_provision_sends_once_without_sdk_logs_or_password_output(self):
        output = io.StringIO()
        with patch("drone.network.socket.socket") as factory, \
             patch("drone.network._reply", side_effect=["ok", "ok"]), \
             patch.object(Tello, "send_control_command") as sdk, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertTrue(provision_sta("Home", "SECRET123"))
            sock = factory.return_value.__enter__.return_value
            self.assertEqual(sock.sendto.call_args_list, [
                call(b"command", ("192.168.10.1", 8889)),
                call(b"ap Home SECRET123", ("192.168.10.1", 8889))])
            sock.bind.assert_called_once_with(("", 8889))
            sdk.assert_not_called()
        self.assertNotIn("SECRET123", output.getvalue())

    def test_reboot_without_ack_is_uncertain_not_success_or_retry(self):
        with patch("drone.network.socket.socket") as factory, \
             patch("drone.network._reply", side_effect=["ok", socket.timeout()]):
            self.assertFalse(provision_sta("Home", "SECRET123"))
            self.assertEqual(factory.return_value.__enter__.return_value.sendto.call_count, 2)

    def test_failure_does_not_echo_device_reply_or_os_error_with_secret(self):
        for reply in ("error ap Home SECRET123", socket.timeout()):
            with self.subTest(reply=type(reply).__name__), \
                 patch("drone.network.socket.socket"), \
                 patch("drone.network._reply", side_effect=[reply]), \
                 self.assertRaises(NetworkSetupError) as error:
                provision_sta("Home", "SECRET123")
            self.assertNotIn("SECRET123", str(error.exception))
        with patch("drone.network.socket.socket", side_effect=OSError("SECRET123")), \
             self.assertRaises(NetworkSetupError) as error:
            provision_sta("Home", "SECRET123")
        self.assertNotIn("SECRET123", str(error.exception))
        with patch("drone.network.socket.socket"), \
             patch("drone.network._reply", side_effect=["ok", "error ap Home SECRET123"]), \
             self.assertRaises(NetworkSetupError) as error:
            provision_sta("Home", "SECRET123")
        self.assertNotIn("SECRET123", str(error.exception))

    def test_discovery_requires_bounded_private_ipv4_subnet(self):
        with patch("drone.network.socket.socket") as factory:
            for subnet in ("192.168.0.0/16", "8.8.8.0/24", "127.0.0.0/24", "::1/128", "invalid"):
                with self.subTest(subnet=subnet), self.assertRaises(NetworkSetupError):
                    discover_drones(subnet)
            factory.assert_not_called()

    def test_discovery_checks_sdk_and_sender_and_sends_no_flight_commands(self):
        clock = Clock()
        responses = iter([(b"30", ("192.168.1.1", 8889)),  # no SDK handshake yet
                          ConnectionResetError("ICMP port unreachable"),
                          (b"ok", ("192.168.1.1", 9999)),
                          (b"ok", ("192.168.2.1", 8889)),
                          (b"ok", ("192.168.1.1", 8889)),
                          (b"30", ("192.168.1.1", 8889)),
                          (b"ok", ("192.168.1.2", 8889)),
                          (b"3.0", ("192.168.1.2", 8889))])

        def receive(_size):
            clock.sleep(0.05)
            value = next(responses, None)
            if value is None:
                raise socket.timeout()
            if isinstance(value, Exception):
                raise value
            return value

        with patch("drone.network.socket.socket") as factory, \
             patch("drone.network.time.monotonic", clock.monotonic):
            sock = factory.return_value.__enter__.return_value
            sock.recvfrom.side_effect = receive
            self.assertEqual(discover_drones("192.168.1.0/30", timeout=1),
                             [("192.168.1.1", "30"), ("192.168.1.2", "3.0")])
            self.assertEqual({item.args[0] for item in sock.sendto.call_args_list}, {b"command", b"sdk?"})

    def test_sta_diagnostics_do_not_require_the_direct_ap_subnet(self):
        with patch("drone.connection.socket.socket") as factory:
            factory.return_value.__enter__.return_value.getsockname.return_value = ("192.168.1.2", 12345)
            self.assertNotIn("TELLO-", network_hint("192.168.1.77", "sta"))

    def test_sensitive_sdk_commands_are_blocked_before_library_logging(self):
        with patch.object(ConfiguredTello, "end"), \
             patch.object(Tello, "__init__") as init, \
             patch.object(Tello, "send_command_with_return") as send, \
             patch.object(Tello, "send_control_command") as control, \
             patch.object(Tello, "send_command_without_return") as send_no_reply:
            client = ConfiguredTello(host="192.168.1.77", network_mode="sta")
            client.end = Mock()  # bound-method test references can outlive the patch
            init.assert_called_once_with(host="192.168.1.77")
            for command in ("ap Home SECRET123", "wifi Home SECRET123", "  AP Home SECRET123", "ap?"):
                for method in (client.send_command_with_return, client.send_command_without_return,
                               client.send_control_command):
                    with self.assertRaises(ValueError) as error:
                        method(command)
                    self.assertNotIn("SECRET123", str(error.exception))
            send.assert_not_called()
            send_no_reply.assert_not_called()
            control.assert_not_called()
            del client


class VideoProbeTests(unittest.TestCase):
    def probe(self, frames, replies=("ok", "ok"), stop_error=False):
        clock = Clock()
        remaining = iter(frames)
        current = [None]

        class Reader:
            stop = Mock(side_effect=RuntimeError() if stop_error else None)

            @property
            def frame(self):
                current[0] = next(remaining, current[0])
                return current[0]

        reader = Reader()
        tello = Mock()
        # first reply: front_camera_first() ("downvision 0") before streamon
        tello.send_command_with_return.side_effect = ["ok"] + list(replies) + ["ok"]
        tello.get_frame_read.return_value = reader
        with patch("drone.video.time.monotonic", clock.monotonic), \
             patch("drone.video.time.sleep", clock.sleep), \
             patch("drone.video.cfg.STA_VIDEO_PROBE_TIMEOUT_S", 0.2):
            capability = station_video(tello, True)
        return capability, tello, reader

    def test_sta_default_never_attempts_video(self):
        tello = Mock()
        result = station_video(tello)
        self.assertFalse(result.available)
        self.assertIsNone(result.reader.frame)
        self.assertEqual(tello.mock_calls, [])

    def test_ack_but_frozen_placeholder_is_not_video(self):
        result, tello, reader = self.probe([np.zeros((20, 20, 3))])
        self.assertFalse(result.available)
        reader.stop.assert_called_once()
        tello.send_command_with_return.assert_called_with("streamoff", timeout=1)

    def test_fresh_decoded_frames_and_downvision_enable_navigation(self):
        result, _, reader = self.probe([np.ones((20, 20, 3)) for _ in range(3)])
        self.assertTrue(result.available)
        self.assertTrue(result.downward)
        self.assertEqual(result.warning, "")
        reader.stop.assert_not_called()

    def test_stream_starts_on_the_front_camera_then_switches_down(self):
        _, tello, _ = self.probe([np.ones((20, 20, 3)) for _ in range(3)])
        sent = [c.args[0] for c in tello.send_command_with_return.call_args_list]
        self.assertEqual(sent[:3], ["downvision 0", "streamon", "downvision 1"])

    def test_front_camera_request_failure_does_not_block_video(self):
        tello = Mock()
        tello.send_command_with_return.side_effect = [RuntimeError("timeout"), "ok", "ok"]
        reader = Mock()
        reader.frame = None
        frames = iter([np.ones((2, 2, 3)) for _ in range(3)])
        type(reader).frame = property(lambda self: next(frames, None))
        tello.get_frame_read.return_value = reader
        self.assertTrue(station_video(tello, True).available)

    def test_front_video_does_not_enable_downward_navigation(self):
        result, _, _ = self.probe([np.ones((20, 20, 3)) for _ in range(3)], ("ok", "error"))
        self.assertTrue(result.available)
        self.assertFalse(result.downward)
        self.assertTrue(result.warning)

    def test_rejected_stream_never_opens_reader(self):
        result, tello, _ = self.probe([], ("error",))
        self.assertFalse(result.available)
        tello.get_frame_read.assert_not_called()

    def test_reader_open_or_stop_failure_still_degrades_cleanly(self):
        tello = Mock()
        tello.send_command_with_return.return_value = "ok"
        tello.get_frame_read.side_effect = RuntimeError("no stream")
        self.assertFalse(station_video(tello, True).available)
        self.assertFalse(self.probe([None], stop_error=True)[0].available)


class ApplicationCapabilityTests(unittest.TestCase):
    def make_app(self, mode="sta"):
        tello = Mock(network_mode=mode, probe_video=False)
        tello.get_battery.return_value = 80
        with patch("drone.app.JetsonLink"):
            return DroneApp(tello, record_dir="unused-test-dataset")

    def test_sta_without_video_starts_sensors_but_not_odometry_or_detector(self):
        app = self.make_app()
        with patch("drone.app.threading.Thread") as thread, \
             patch("drone.app.Odometry") as odometry, patch("drone.app.Telemetry") as telemetry:
            app.start()
            odometry.assert_not_called()
            self.assertNotIn(app.detect_loop, [item.kwargs["target"] for item in thread.call_args_list])
            self.assertTrue(telemetry.call_args.kwargs["sensor_only"])
        app.tello.streamon.assert_not_called()
        app.tello.get_frame_read.assert_not_called()
        self.assertFalse(app.vision_navigation_available)
        self.assertFalse(app.pose.position_valid)
        self.assertFalse(app.recording)

    def test_ap_keeps_original_video_and_tracking_startup(self):
        app = self.make_app("ap")
        app.tello.send_command_with_return.return_value = "ok"
        with patch("drone.app.threading.Thread") as thread, \
             patch("drone.app.Odometry") as odometry, patch("drone.app.Telemetry") as telemetry, \
             patch("drone.app.station_video") as probe:
            app.start()
            app.tello.streamon.assert_called_once()
            # front camera before the stream starts, downward camera after
            self.assertEqual(app.tello.send_command_with_return.call_args_list,
                             [call("downvision 0", timeout=3), call("downvision 1")])
            names = [c[0] for c in app.tello.mock_calls]
            self.assertLess(names.index("send_command_with_return"), names.index("streamon"))
            app.tello.get_frame_read.assert_called_once()
            odometry.assert_called_once()
            self.assertIn(app.detect_loop, [item.kwargs["target"] for item in thread.call_args_list])
            self.assertFalse(telemetry.call_args.kwargs["sensor_only"])
            probe.assert_not_called()

    def test_sta_with_confirmed_downward_video_starts_tracking(self):
        app = self.make_app()
        with patch("drone.app.threading.Thread"), patch("drone.app.Odometry") as odometry, \
             patch("drone.app.Telemetry"), \
             patch("drone.app.station_video", return_value=VideoCapability(True, True, Mock())):
            app.start()
            self.assertTrue(app.vision_navigation_available)
            odometry.assert_called_once()

    def test_gui_and_jetson_cannot_queue_or_execute_visual_commands_without_video(self):
        app = self.make_app()
        app.vision_navigation_available = False
        for kind in ("mission", "flip", "helipad_land", "downvision"):
            with self.subTest(kind=kind):
                self.assertFalse(app.submit({"type": kind}))
                with self.assertRaises(CommandRejected):
                    app.handle_command({"type": kind})
        self.assertTrue(app.commands.empty())
        self.assertFalse(app.commands.flip_pending.is_set())
        self.assertEqual(app.tello.mock_calls, [])

    def test_direct_visual_methods_cannot_bypass_guard_or_take_off(self):
        app = self.make_app()
        app.vision_navigation_available = False
        for method, args in ((app.helipad_test, ()), (app.run_mission, ({},)),
                             (app.land_on_seen_helipad, ((0, 0, 80), 30)),
                             (app.land_on_helipad, ((0, 0),)), (app.toggle_recording, ())):
            with self.subTest(method=method.__name__), self.assertRaises(CommandRejected):
                method(*args)
        self.assertEqual(app.tello.mock_calls, [])

    def test_basic_commands_remain_usable_in_sensor_only_mode(self):
        app = self.make_app()
        app.vision_navigation_available = False
        self.assertIsNot(app.submit({"type": "takeoff"}), False)
        self.assertEqual(app.commands.get_nowait()["type"], "takeoff")
        self.assertIsNot(app.submit({"type": "land"}), False)
        self.assertEqual(app.commands.get_nowait()["type"], "land")
        app.submit({"type": "ping", "t": 123})
        app.link.send.assert_called_with({"type": "pong", "t": 123})
        self.assertEqual(app.tello.mock_calls, [])

    def test_sensor_only_takeoff_never_starts_a_recentering_movement(self):
        app = self.make_app()
        app.vision_navigation_available = False
        app.follower.hold_position = Mock()
        app.read_height = Mock(return_value=80)
        app.takeoff()  # tello is a Mock; no network or real flight
        app.tello.takeoff.assert_called_once()
        app.follower.hold_position.assert_not_called()
        self.assertTrue(app.airborne)
        self.assertEqual(app.state, "flying")
        self.assertFalse(app.pose.position_valid)

    def test_reset_does_not_revalidate_unmeasured_sta_coordinates(self):
        app = self.make_app()
        app.vision_navigation_available = False
        app.reset_environment()
        self.assertFalse(app.pose.position_valid)

    def test_sta_front_only_camera_is_displayable_but_never_starts_navigation(self):
        app = self.make_app()
        reader = Mock()
        with patch("drone.app.threading.Thread"), patch("drone.app.Odometry") as odometry, \
             patch("drone.app.Telemetry"), \
             patch("drone.app.station_video", return_value=VideoCapability(True, False, reader, "Pas de caméra inférieure")):
            app.start()
        self.assertTrue(app.video_available)
        self.assertIs(app.frame_reader, reader)
        self.assertFalse(app.vision_navigation_available)
        odometry.assert_not_called()

    def test_sensor_only_telemetry_updates_height_without_inventing_xy_or_terrain(self):
        tello = Mock()
        tello.get_current_state.return_value = {"tof": 80, "baro": 1.5, "yaw": 20, "bat": 90}
        pose = Pose()
        with patch("drone.telemetry.threading.Thread"):
            telemetry = Telemetry(tello, pose, lambda: True, sensor_only=True)
        for i in range(20):
            sample = telemetry._sample(i / 10, 0.1)
        self.assertEqual(sample["z"], 80)
        self.assertEqual(pose.get()[:2], (0, 0))
        self.assertFalse(sample["position_valid"])
        self.assertFalse(pose.local_reference_valid)
        self.assertEqual(telemetry.terrain.snapshot(), [])


class NetworkInterfaceTests(unittest.TestCase):
    def dialog_stub(self):
        dialog = ConnectionDialog.__new__(ConnectionDialog)
        dialog._busy = False
        dialog.ssid, dialog.password = Value("Home"), Value("SECRET123")
        dialog.status = Value("")
        dialog._events = queue.Queue()
        dialog.refresh_buttons = Mock()
        return dialog

    def test_password_field_cleared_before_network_worker_runs(self):
        dialog = self.dialog_stub()

        def start(kind, work):
            self.assertEqual(dialog.password.get(), "")
            self.assertEqual(kind, "configure")
            work()

        dialog._start = start
        with patch("gui.connection_dialog.provision_sta") as provision:
            dialog.configure_sta()
            provision.assert_called_once_with("Home", "SECRET123")

    def test_unexpected_worker_exception_cannot_expose_password(self):
        dialog = self.dialog_stub()

        def failing():
            raise RuntimeError("SECRET123")

        with patch("gui.connection_dialog.threading.Thread") as thread:
            dialog._start("configure", failing)
            thread.call_args.kwargs["target"]()
        self.assertNotIn("SECRET123", dialog._events.get_nowait()[2])

    def test_discovery_refreshes_unedited_subnet_after_pc_switches_wifi(self):
        for entered, expected in (("192.168.10.0/24", "192.168.1.0/24"),
                                  ("10.0.0.0/24", "10.0.0.0/24")):
            with self.subTest(entered=entered):
                dialog = self.dialog_stub()
                dialog.mode = Value("sta")
                dialog._auto_subnet = "192.168.10.0/24"
                dialog.subnet = Value(entered)
                dialog._start = lambda kind, work: work()
                with patch("gui.connection_dialog.suggested_subnet", return_value="192.168.1.0/24"), \
                     patch("gui.connection_dialog.discover_drones") as discover:
                    dialog.discover()
                    discover.assert_called_once_with(expected)

    def test_reboot_wait_disables_connect_and_retains_ack_uncertainty(self):
        for acknowledged in (True, False):
            with self.subTest(acknowledged=acknowledged):
                dialog = self.dialog_stub()
                dialog.mode, dialog.host = Value("ap"), Value("192.168.10.1")
                dialog.top = Mock()
                dialog.configure_btn, dialog.discover_btn, dialog.connect_btn = Mock(), Mock(), Mock()
                dialog._reboot_until = 0
                dialog.refresh_buttons = lambda: ConnectionDialog.refresh_buttons(dialog)
                dialog._events.put(("configure", acknowledged, None))
                clock = Clock()
                with patch("gui.connection_dialog.time.monotonic", clock.monotonic):
                    dialog.poll()
                    self.assertEqual(dialog.mode.get(), "sta")
                    self.assertIn("redémarrage", dialog.status.get())
                    self.assertEqual("non confirmé" in dialog.status.get(), not acknowledged)
                    dialog.connect_btn.configure.assert_called_with(state="disabled")
                    clock.sleep(100)
                    dialog.poll()
                    dialog.connect_btn.configure.assert_called_with(state="normal")
                    self.assertIn("Rejoindre le Wi-Fi domestique", dialog.status.get())

    def test_return_ap_is_a_local_choice_with_physical_reset_instructions(self):
        dialog = self.dialog_stub()
        dialog.mode, dialog.host = Value("sta"), Value("192.168.1.77")
        with patch("gui.connection_dialog.provision_sta") as provision:
            dialog.return_ap()
            self.assertEqual(dialog.mode.get(), "ap")
            self.assertEqual(dialog.host.get(), "192.168.10.1")
            self.assertIn("5 secondes", dialog.status.get())
            provision.assert_not_called()

    def test_closing_dialog_clears_password_and_cancels_timer(self):
        dialog = self.dialog_stub()
        dialog.top = Mock()
        dialog._poll_id = "timer-id"
        dialog.cancel()
        self.assertEqual(dialog.password.get(), "")
        dialog.top.after_cancel.assert_called_once_with("timer-id")
        dialog.top.destroy.assert_called_once()

    def test_vision_buttons_and_keyboard_handlers_cannot_use_missing_camera(self):
        gui = TelloGUI.__new__(TelloGUI)
        gui.app = SimpleNamespace(vision_navigation_available=False, video_warning="STA sans caméra", submit=Mock())
        gui.vision_buttons = [Mock(), Mock(), Mock()]
        gui.record_btn, gui.detect_check, gui.show_dets, gui.toast = Mock(), Mock(), Mock(), Mock()
        gui.cam_seg = SimpleNamespace(buttons={"Voor": Mock(), "Onder": Mock()})
        gui._apply_capabilities()
        for button in gui.vision_buttons + [gui.record_btn, gui.detect_check] + list(gui.cam_seg.buttons.values()):
            button.configure.assert_called_once_with(state="disabled")
        self.assertFalse(gui._flip_availability()[0])
        gui.set_camera_mode(True)
        gui.toggle_record()
        gui.app.submit.assert_not_called()

    def test_cancel_startup_does_not_construct_sdk_or_application(self):
        with patch("tello_gui.sys.argv", ["tello_gui.py"]), patch("tello_gui.tk.Tk"), \
             patch("tello_gui.choose_connection", return_value=None), \
             patch("drone.connection.ConfiguredTello") as sdk, patch("tello_gui.DroneApp") as app:
            main()
            sdk.assert_not_called()
            app.assert_not_called()

    def test_startup_selection_finishes_before_sdk_binds_port(self):
        order = []
        options = ConnectionOptions("sta", "192.168.1.77", True)
        with patch("tello_gui.sys.argv", ["tello_gui.py"]), patch("tello_gui.tk.Tk"), \
             patch("tello_gui.choose_connection", side_effect=lambda root: order.append("dialog") or options), \
             patch("drone.connection.ConfiguredTello", side_effect=lambda **kw: order.append(kw) or Mock()), \
             patch("tello_gui.DroneApp"), patch("tello_gui.TelloGUI"):
            main()
        self.assertEqual(order, ["dialog", {"host": "192.168.1.77", "network_mode": "sta", "probe_video": True}])


if __name__ == "__main__":
    unittest.main()
