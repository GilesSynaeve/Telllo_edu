"""STA video is a runtime capability, never inferred from SDK acknowledgement."""

from dataclasses import dataclass
import time

from . import config as cfg


class NoVideoReader:
    frame = None


@dataclass
class VideoCapability:
    available: bool
    downward: bool
    reader: object
    warning: str = ""


NO_STA_VIDEO = ("STA sans caméra inférieure confirmée : odométrie visuelle, missions, saltos avec retour "
                "et atterrissage H désactivés. La vidéo STA dépend du firmware/SDK ; "
                "revenir en AP pour les fonctions visuelles.")


def front_camera_first(tello):
    """Select the front camera before the video stream starts.

    Ground test 2026-11-12: a stream started while the drone was still on the
    downward camera (left from an earlier session) decoded only as corrupted
    320x720 frames; starting on the front camera and then switching gave clean
    320x240 frames. A failure here is not fatal: the caller still switches later.
    """
    try:
        return str(tello.send_command_with_return("downvision 0", timeout=3)).strip().lower() == "ok"
    except Exception:
        return False


def station_video(tello, enabled=False):
    if not enabled:
        return VideoCapability(False, False, NoVideoReader(), NO_STA_VIDEO)
    reader = None
    started = False
    front_camera_first(tello)
    try:
        response = tello.send_command_with_return("streamon", timeout=3)
        if str(response).strip().lower() != "ok":
            return VideoCapability(False, False, NoVideoReader(), NO_STA_VIDEO)
        tello.stream_on = started = True
        downward = str(tello.send_command_with_return("downvision 1", timeout=3)).strip().lower() == "ok"
        # djitellopy/PyAV also bounds stream opening (5s in the installed version).
        reader = tello.get_frame_read()
        previous = reader.frame
        frames = 0
        deadline = time.monotonic() + cfg.STA_VIDEO_PROBE_TIMEOUT_S
        while time.monotonic() < deadline:
            current = reader.frame
            if current is not None and current is not previous and getattr(current, "size", 0):
                frames += 1
                previous = current
                if frames >= 2:
                    return VideoCapability(True, downward, reader, "" if downward else NO_STA_VIDEO)
            time.sleep(0.05)
    except Exception:
        pass
    if reader is not None:
        try:
            reader.stop()
        except Exception:
            pass
    if started:
        try:
            tello.send_command_with_return("streamoff", timeout=1)
        except Exception:
            pass
        tello.stream_on = False
    return VideoCapability(False, False, NoVideoReader(), NO_STA_VIDEO)
