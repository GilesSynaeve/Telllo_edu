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


DOWN_SHAPE = (240, 320)   # the downward camera stream


def corrupted_fraction(frame):
    """Share of 'coloured' pixels. The downward camera is monochrome (R=G=B), so
    green/purple blocks are H.264 decoding errors (lost video packets)."""
    if frame.ndim != 3:
        return 0.0
    f = frame.astype("int16")
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    return float(((abs(g - r) > 40) | (abs(g - b) > 40)).mean())


def new_frames(reader, seconds, check=None):
    """Count new decoded frames for `seconds`.

    Returns (count, last frame, last frame passing check, frames failing check).
    """
    previous, count, last, good, bad = reader.frame, 0, None, None, 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        frame = reader.frame
        if frame is not None and frame is not previous and getattr(frame, "size", 0):
            count, previous, last = count + 1, frame, frame
            if check is None or check(frame):
                good = frame
            else:
                bad += 1
        time.sleep(0.005)
    return count, last, good, bad


def healthy_down(frame):
    return frame.shape[:2] == DOWN_SHAPE and corrupted_fraction(frame) < 0.01


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


def start_video(tello, attempts=3):
    """Start the stream; over STA the decoder often fails to start, so retry."""
    for attempt in range(1, attempts + 1):
        try:
            tello.streamon()
            reader = tello.get_frame_read()
            count, frame, _, _ = new_frames(reader, 2.0)
            if frame is not None:
                if attempt > 1:
                    print(f"ℹ️  video gestart bij poging {attempt}")
                return reader
            print(f"ℹ️  poging {attempt}: geen beelden")
        except Exception as e:
            print(f"ℹ️  poging {attempt}: {e}")
        if tello.background_frame_read is not None:
            tello.background_frame_read.stop()
            tello.background_frame_read = None
        try:
            tello.streamoff()
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"video na {attempts} pogingen niet gestart (UDP 11111, Wi-Fi-kwaliteit?)")


def cmd_video(tello, args):
    reader = start_video(tello)
    print("Downvision 0:", tello.send_command_with_return("downvision 0", timeout=3))
    time.sleep(1.0)
    count, frame, _, _ = new_frames(reader, 3.0)
    if frame is not None:
        print(f"Voorcamera: {frame.shape[1]}x{frame.shape[0]}, {count / 3:.1f} fps -> {save('front.png', frame)}")
    else:
        print("Voorcamera: geen beelden")
    print("Downvision 1:", tello.send_command_with_return("downvision 1", timeout=3))
    time.sleep(1.5)
    seconds = args.seconds if args.seconds != 15 else 10
    count, frame, good, bad = new_frames(reader, seconds, healthy_down)
    if frame is not None:
        print(f"Ondercamera: {frame.shape[1]}x{frame.shape[0]}, {count / seconds:.1f} fps, "
              f"{bad}/{count} beelden beschadigd of verkeerd formaat ({100 * bad / count:.0f}%)")
        print("  laatste gezonde beeld ->", save("down.png", good) if good is not None else "geen")
    else:
        print("Ondercamera: geen beelden")


def cmd_snap(tello, args):
    from drone.downcam import DownCam, cm_per_pixel, pixel_to_world
    from drone.helipad import HelipadDetector, draw_helipad

    reader = start_video(tello)
    print("Downvision 1:", tello.send_command_with_return("downvision 1", timeout=3))
    time.sleep(1.5)
    _, _, frame, bad = new_frames(reader, 2.0, healthy_down)
    if frame is None:
        raise RuntimeError(f"geen gezond beeld van de ondercamera ({bad} beschadigd/verkeerd formaat)")
    if bad:
        print(f"ℹ️  {bad} beschadigde beelden overgeslagen")
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
