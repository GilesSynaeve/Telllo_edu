"""The ground-check tool must never be able to start the motors."""

import unittest

from ground_check import GroundOnlyError, check_ground_command


class GroundOnlyTests(unittest.TestCase):
    def test_setup_and_read_commands_pass(self):
        for cmd in ("command", "streamon", "streamoff", "downvision 0", "downvision 1",
                    "battery?", "sdk?", "temp?", " Battery? "):
            with self.subTest(cmd=cmd):
                check_ground_command(cmd)

    def test_every_motion_or_setup_change_is_blocked(self):
        for cmd in ("takeoff", "land", "flip f", "go 20 0 0 20", "rc 0 0 0 0", "rc 10 0 0 0",
                    "motoron", "emergency", "reboot", "up 20", "cw 90", "stop", "speed 50",
                    "ap ssid secret", "wifi?", "ap?", "downvision 2", "streamon; takeoff", ""):
            with self.subTest(cmd=cmd):
                with self.assertRaises(GroundOnlyError):
                    check_ground_command(cmd)

    def test_blocked_message_does_not_echo_arguments(self):
        with self.assertRaises(GroundOnlyError) as ctx:
            check_ground_command("ap myssid mypassword")
        self.assertNotIn("mypassword", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
