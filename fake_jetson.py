"""
Stand-in for the Jetson: test the UDP/JSON link without the real Jetson.

Run it next to the GUI (preferably `python tello_gui.py --sim`):

    python fake_jetson.py listen                 # show every message from the laptop
    python fake_jetson.py ping                   # one ping, wait for the pong
    python fake_jetson.py mission json_flights/mission_example.json
    python fake_jetson.py abort                  # Abort = land

Ports follow drone/config.py: commands go to LISTEN_PORT (9000), the laptop
answers on JETSON_PORT (9001). `mission` makes a connected drone TAKE OFF:
use it with --sim, or only after the safety checklist of the README.
"""

import argparse
import json
import socket
import sys
import time

from drone import config as cfg


def open_socket(port, laptop):
    # Same machine: stay on loopback (no firewall prompt, nothing exposed on the LAN)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1" if laptop.startswith("127.") else "0.0.0.0", port))
    return sock


def show(msg, addr, hide_status):
    if msg.get("type") == "status" and hide_status:
        return
    if msg.get("type") in ("puddle", "puddle_photo"):
        print(f"PLAS {msg.get('id')}: x={msg.get('x')} cm, y={msg.get('y')} cm, "
              f"zekerheid {msg.get('confidence')}", msg.get("photo", ""))
    else:
        print(f"{time.strftime('%H:%M:%S')} {addr[0]}: {json.dumps(msg, ensure_ascii=False)}")


def receive(sock, seconds, hide_status, until=None):
    deadline = None if seconds is None else time.monotonic() + seconds
    while deadline is None or time.monotonic() < deadline:
        sock.settimeout(0.5 if deadline is None else max(0.01, deadline - time.monotonic()))
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            msg = json.loads(data.decode("utf-8"))
        except ValueError:
            print(f"⚠️  geen JSON van {addr[0]}: {data[:80]!r}")
            continue
        show(msg, addr, hide_status)
        if until is not None and msg.get("type") == until:
            return msg
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("listen", "ping", "mission", "abort"))
    ap.add_argument("file", nargs="?", help="mission JSON (for 'mission')")
    ap.add_argument("--laptop", default="127.0.0.1", help="IP of the laptop running tello_gui.py")
    ap.add_argument("--show-status", action="store_true", help="also print the 1 Hz status messages")
    ap.add_argument("--seconds", type=float, default=None, help="stop listening after this time")
    args = ap.parse_args(argv)

    sock = open_socket(cfg.JETSON_PORT, args.laptop)
    target = (args.laptop, cfg.LISTEN_PORT)
    hide = not args.show_status
    if args.action == "listen":
        print(f"Luisteren op UDP {cfg.JETSON_PORT} (Ctrl+C = stoppen)")
        try:
            receive(sock, args.seconds, hide)
        except KeyboardInterrupt:
            pass
        return 0
    if args.action == "ping":
        sock.sendto(json.dumps({"type": "ping", "t": time.time()}).encode(), target)
        pong = receive(sock, args.seconds or 3, hide, until="pong")
        print("pong ontvangen" if pong else "geen pong: draait de GUI? klopt LISTEN_HOST/JETSON_HOST?")
        return 0 if pong else 1
    if args.action == "abort":
        sock.sendto(b'{"type": "abort"}', target)
        print("abort verstuurd")
        return 0
    if not args.file:
        ap.error("mission needs a JSON file")
    with open(args.file, encoding="utf-8") as stream:
        mission = json.load(stream)
    mission.setdefault("type", "mission")
    print("⚠️  Een missie laat een verbonden drone OPSTIJGEN. Ctrl+C binnen 3 s = annuleren.")
    try:
        time.sleep(3)
    except KeyboardInterrupt:
        print("geannuleerd")
        return 1
    sock.sendto(json.dumps(mission).encode(), target)
    try:
        receive(sock, args.seconds, hide, until="mission_done")
    except KeyboardInterrupt:
        sock.sendto(b'{"type": "abort"}', target)
        print("abort verstuurd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
