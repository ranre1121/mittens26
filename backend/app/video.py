"""Video decoding with PyAV (bundled FFmpeg).

Timestamps always come from the frames' presentation timestamps, never from
frame_index / nominal_fps, because phone and CCTV exports are often variable
frame rate (the case videos are "29+ FPS").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import av
from PIL import Image


class InvalidVideo(Exception):
    pass


@dataclass(frozen=True)
class VideoInfo:
    duration_sec: float
    width: int
    height: int
    avg_fps: float
    codec: str


def probe(path: Path) -> VideoInfo:
    """Open the container, check it has a decodable video stream, read metadata."""
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise InvalidVideo("file has no video stream")
            stream = container.streams.video[0]
            duration = None
            if stream.duration is not None and stream.time_base is not None:
                duration = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                duration = container.duration / av.time_base
            first = next(container.decode(stream), None)
            if first is None:
                raise InvalidVideo("no decodable video frames")
            if not duration or duration <= 0:
                raise InvalidVideo("could not determine video duration")
            return VideoInfo(
                duration_sec=duration,
                width=first.width,
                height=first.height,
                avg_fps=float(stream.average_rate or 0),
                codec=stream.codec_context.name,
            )
    except InvalidVideo:
        raise
    except (av.error.FFmpegError, OSError, StopIteration) as exc:
        # FFmpeg messages include the server-side file path; keep it out of API responses.
        raise InvalidVideo(f"cannot decode video ({type(exc).__name__})") from exc


def sample(path: Path, fps: float) -> Iterator[tuple[float, Image.Image]]:
    """Yield (timestamp_sec, frame) at roughly `fps`, using real frame times."""
    step = 1.0 / fps
    next_t = 0.0
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in container.decode(stream):
            if frame.time is None or frame.time + 1e-6 < next_t:
                continue
            yield float(frame.time), frame.to_image()
            next_t = frame.time + step


def frames_at(path: Path, timestamps: list[float]) -> list[tuple[float, Image.Image]]:
    """Return the first frame at or after each requested time, with its actual timestamp."""
    out: list[tuple[float, Image.Image]] = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for target in timestamps:
            # Seek to the keyframe before the target, then decode forward to it.
            container.seek(max(0, int(target / stream.time_base)), stream=stream, backward=True)
            chosen = None
            for frame in container.decode(stream):
                if frame.time is None:
                    continue
                chosen = frame
                if frame.time + 1e-3 >= target:
                    break
            if chosen is not None:
                out.append((float(chosen.time), chosen.to_image()))
    return out


def _probe_child(path, sender):
    try:
        sender.send((True, probe(path)))
    except Exception:
        sender.send((False, None))
    finally:
        sender.close()


def probe_bounded(path: Path, timeout: float = 10) -> VideoInfo:
    import multiprocessing
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_probe_child, args=(path, sender))
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            raise InvalidVideo("video probing timed out")
        try:
            valid, info = receiver.recv()
        except EOFError:
            raise InvalidVideo("video probing failed") from None
        if not valid:
            raise InvalidVideo("cannot decode video")
        return info
    finally:
        receiver.close()
        process.join(timeout=.1)
        if process.is_alive():
            process.kill()
            process.join()
