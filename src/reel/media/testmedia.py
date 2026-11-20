"""Synthetic "lessons" for tests (T-201): parts with a moving or static picture and a tone,
noise (speech stand-in) or silence, generated with FFmpeg's built-in sources."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from reel.media.ffmpeg import run_ffmpeg


@dataclass(frozen=True)
class Part:
    seconds: float
    picture: Literal["moving", "static"] = "moving"
    sound: Literal["tone", "noise", "silence"] = "noise"


# Chatter, a silent pause over a static slide, then "lesson" content.
DEFAULT_LESSON = (
    Part(3, "moving", "noise"),
    Part(4, "static", "silence"),
    Part(3, "moving", "tone"),
)


def parse_parts(text: str) -> tuple[Part, ...]:
    """``"moving:3:noise,static:4:silence"`` → parts."""
    parts = []
    for item in text.split(","):
        picture, seconds, sound = item.strip().split(":")
        parts.append(Part(float(seconds), picture, sound))  # type: ignore[arg-type]
    return tuple(parts)


def make_lesson(
    ffmpeg: Path | str,
    target: Path | str,
    parts: tuple[Part, ...] = DEFAULT_LESSON,
    *,
    size: str = "320x240",
    fps: int = 25,
    audio: bool = True,
    faststart: bool = False,
    creation_time: datetime | None = None,
) -> Path:
    """Write an H.264/AAC MP4 made of ``parts``; returns its path."""
    inputs: list[str] = []
    for part in parts:
        seconds = f"{part.seconds:g}"
        if part.picture == "moving":
            video = f"testsrc2=size={size}:rate={fps}:duration={seconds}"
        elif part.picture == "static":
            video = f"color=c=0x404040:size={size}:rate={fps}:duration={seconds}"
        else:
            raise ValueError(f"unknown picture {part.picture!r}")
        inputs += ["-f", "lavfi", "-i", video]
        if audio:
            sounds = {
                "tone": f"sine=frequency=440:sample_rate=48000:duration={seconds}",
                "noise": (
                    f"anoisesrc=color=pink:amplitude=0.25:sample_rate=48000:duration={seconds}"
                ),
                "silence": f"anullsrc=r=48000:cl=mono,atrim=duration={seconds}",
            }
            if part.sound not in sounds:
                raise ValueError(f"unknown sound {part.sound!r}")
            inputs += ["-f", "lavfi", "-i", sounds[part.sound]]
    step = 2 if audio else 1
    labels = "".join(
        f"[{n * step}:v]" + (f"[{n * step + 1}:a]" if audio else "") for n in range(len(parts))
    )
    graph = f"{labels}concat=n={len(parts)}:v=1:a={int(audio)}[v]" + ("[a]" if audio else "")
    args = ["-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[v]"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "96k", "-ac", "1"]
    if faststart:
        args += ["-movflags", "+faststart"]
    if creation_time is not None:
        args += ["-metadata", f"creation_time={creation_time.isoformat()}"]
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(ffmpeg, [*args, target])
    return target
