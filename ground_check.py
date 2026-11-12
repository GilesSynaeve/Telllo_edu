"""
Ground checks on the real Tello EDU with the MOTORS OFF. Nothing here can fly:
every SDK command goes through check_ground_command(), which only lets through
'command', 'streamon'/'streamoff', 'downvision 0/1' and read queries ('...?').
takeoff, land, flip, go, rc, motoron, emergency, reboot ... raise an error before
anything is sent.

Close the GUI and other Tello apps first (they use UDP 8889). Each step is one
short run; results and images go to flight_logs/ground/.

    python ground_check.py info            # SDK, battery, temperature, telemetry rate
    python ground_check.py video           # front + downward camera: resolution, fps, images
    python ground_check.py snap NAME       # downward image + ToF/attitude + H detection
    python ground_check.py watch 15        # print ToF, yaw, pitch, roll for 15 s
    python ground_check.py info --connection sta --host 192.168.1.77
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2

from drone import config as cfg
from drone.connection import ConfiguredTello

OUT = Path(__file__).resolve().parent / "flight_logs" / "ground"
ALLOWED = {"command", "streamon", "streamoff", "downvision 0", "downvision 1"}


class GroundOnlyError(RuntimeError):
    pass


def check_ground_command(command):
    """Raise unless the SDK command cannot move the drone or change its setup."""
    text = " ".join(str(command).strip().lower().split())
    if text in ALLOWED:
        return
    words = text.split()
    if len(words) == 1 and text.endswith("?") and text not in ("wifi?", "ap?", "ssid?"):
        return   # read query (battery?, sdk?, ...)
    raise GroundOnlyError(f"ground_check: command '{text.split()[0] if text else text}' "
                          "is blocked (motors stay off)")


class GroundOnlyTello(ConfiguredTello):
    def send_command_with_return(self, command, timeout=None):
        check_ground_command(command)
        return super().send_command_with_return(command, timeout=timeout)

    def send_command_without_return(self, command):
        check_ground_command(command)
        return super().send_command_without_return(command)

    def end(self):
        # djitellopy's end() may call land(); here only the video is stopped
        try:
            if self.stream_on:
                self.streamoff()
        except Exception:
            pass
        if self.background_frame_read is not None:
            self.background_frame_read.stop()


def state(tello):
    s = dict(tello.get_current_state())
    keys = ("bat", "temph", "templ", "tof", "h", "baro", "yaw", "pitch", "roll")
    return {k: s.get(k) for k in keys}


def new_frames(reader, seconds):
    """Count new decoded frames for `seconds`; return (count, last frame)."""
    previous, count, last = reader.frame, 0, None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        frame = reader.frame
        if frame is not None and frame is not previous and getattr(frame, "size", 0):
            count, previous, last = count + 1, frame, frame
        time.sleep(0.005)
    return count, last


def save(name, frame):
    OUT.mkdir(parents=True, exist_ok=True)
    img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if frame.ndim == 3 and cfg.FRAME_IS_RGB else frame
    path = OUT / name
    cv2.imwrite(str(path), img)
    return path


def cmd_info(tello, args):
    print("SDK:", tello.send_command_with_return("sdk?", timeout=3))
    print("Batterij:", tello.send_command_with_return("battery?", timeout=3), "%")
    before = tello.get_current_state()
    count, deadline = 0, time.monotonic() + 3
    while time.monotonic() < deadline:
        current = tello.get_current_state()
        if current is not before:
            count, before = count + 1, current
        time.sleep(0.002)
    print(f"Telemetrie: {count / 3:.1f} pakketten/s (verwacht ~10)")
    print("Toestand:", json.dumps(state(tello)))


def start_video(tello):
    tello.streamon()
    reader = tello.get_frame_read()
    count, frame = new_frames(reader, 2.0)
    if frame is None:
        raise RuntimeError("geen videobeelden ontvangen (UDP 11111, firewall?)")
    return reader


def cmd_video(tello, args):
    reader = start_video(tello)
    print("Downvision 0:", tello.send_command_with_return("downvision 0", timeout=3))
    time.sleep(1.0)
    count, frame = new_frames(reader, 3.0)
    if frame is not None:
        print(f"Voorcamera: {frame.shape[1]}x{frame.shape[0]}, {count / 3:.1f} fps -> {save('front.png', frame)}")
    else:
        print("Voorcamera: geen beelden")
    print("Downvision 1:", tello.send_command_with_return("downvision 1", timeout=3))
    time.sleep(1.5)
    count, frame = new_frames(reader, 3.0)
    if frame is not None:
        print(f"Ondercamera: {frame.shape[1]}x{frame.shape[0]}, {count / 3:.1f} fps -> {save('down.png', frame)}")
    else:
        print("Ondercamera: geen beelden")


def cmd_snap(tello, args):
    from drone.downcam import DownCam, cm_per_pixel, pixel_to_world
    from drone.helipad import HelipadDetector, draw_helipad

    reader = start_video(tello)
    print("Downvision 1:", tello.send_command_with_return("downvision 1", timeout=3))
    time.sleep(1.5)
    _, frame = new_frames(reader, 1.0)
    if frame is None:
        raise RuntimeError("geen beeld van de ondercamera")
    s = state(tello)
    cam = DownCam()
    gray = cam.prepare(frame.copy())
    h, w = gray.shape
    tof = float(s["tof"]) if s.get("tof") not in (None, "") else None
    height = tof if tof and cfg.TOF_MIN_CM < tof < cfg.TOF_MAX_CM else None
    info = {"name": args.name, "state": s, "image_px": [w, h], "height_cm": height,
            "config": {k: getattr(cfg, k) for k in ("CAM_HFOV_DEG", "CAM_ROTATE_DEG",
                                                   "CAM_FORWARD_SIGN", "CAM_LEFT_SIGN")}}
    if height:
        cmp = cm_per_pixel(w, height)
        info["cm_per_px"] = cmp
        info["floor_view_cm"] = [round(w * cmp, 1), round(h * cmp, 1)]
    pads = HelipadDetector().find_candidates(gray, height)
    info["h_candidates"] = []
    for pad in pads[:3]:
        c = {"px": [round(pad.cx, 1), round(pad.cy, 1)], "score": round(pad.score, 3),
             "size_px": round(pad.size_px or 0, 1)}
        if height:
            fx, fy, _ = pixel_to_world(pad.cx, pad.cy, w, h, height, (0, 0, height, 0))
            c["offset_cm_forward_left"] = [round(fx, 1), round(fy, 1)]
        info["h_candidates"].append(c)
    vis = draw_helipad(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), pads[0] if pads else None)
    cv2.line(vis, (w // 2 - 6, h // 2), (w // 2 + 6, h // 2), (0, 0, 255), 1)
    cv2.line(vis, (w // 2, h // 2 - 6), (w // 2, h // 2 + 6), (0, 0, 255), 1)
    info["raw_image"] = str(save(f"down_{args.name}.png", frame))
    info["gray_image"] = str(save(f"down_{args.name}_gray.png", vis))
    (OUT / f"down_{args.name}.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))


def cmd_watch(tello, args):
    t_end = time.monotonic() + args.seconds
    while time.monotonic() < t_end:
        s = {k: str(v) for k, v in state(tello).items()}
        print(f"tof {s['tof']:>5}  h {s['h']:>4}  yaw {s['yaw']:>5}  pitch {s['pitch']:>4}  "
              f"roll {s['roll']:>4}  bat {s['bat']}", flush=True)
        time.sleep(0.5)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("info", "video", "snap", "watch"))
    ap.add_argument("name", nargs="?", default="test", help="snap: name of the image")
    ap.add_argument("--seconds", type=float, default=15, help="watch: duration")
    ap.add_argument("--connection", choices=("ap", "sta"), default="ap")
    ap.add_argument("--host", default=None, help="drone IPv4 (STA)")
    args = ap.parse_args(argv)
    if args.action == "watch" and args.name != "test":
        args.seconds = float(args.name)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    host = args.host or (cfg.TELLO_AP_HOST if args.connection == "ap" else cfg.TELLO_STA_HOST)
    tello = GroundOnlyTello(host=host, network_mode=args.connection)
    try:
        tello.connect()
        {"info": cmd_info, "video": cmd_video, "snap": cmd_snap, "watch": cmd_watch}[args.action](tello, args)
        return 0
    except GroundOnlyError:
        raise
    except Exception as e:
        print(f"Mislukt: {e}")
        return 1
    finally:
        tello.end()


if __name__ == "__main__":
    sys.exit(main())
