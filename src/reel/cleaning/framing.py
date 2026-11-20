"""Frame the board: the picture is zoomed to where the lesson's work happens (D-058).

The user's rule: the video's edges come close to the board, with a small margin. A
classroom may have a whiteboard, a projector screen, or a whole wall to write on, so Reel
does not look for a board's shape. It looks for where the work leaves traces during the
lesson: writing appears or is wiped, slides change. The outermost traces give the box.

How, on the pictures of the board check (about one per second, 416 px wide, T-403):

1. People are masked out: every person the detector sees, enlarged a little.
2. Every ``window_seconds`` the masked pictures are reduced to their per-pixel median: a
   still picture of the room without the people moving in it.
3. The fine detail of each still (the picture minus its blur: writing, slide text) is
   compared with the previous still. Pixels whose detail changed by more than
   ``change_level`` are work. A window where most of the picture changed (the camera moved,
   the lights changed) is skipped.
4. The work box is the box around the work pixels, without isolated specks, without small
   traces that touch the picture's bottom edge (the heads of the people near the camera,
   which the detector may miss), and without small groups of traces on their own (a laptop
   on a table, a door): traces closer than ``GROUP_RADIUS`` form a group, and groups with
   less than ``KEEP_SHARE`` of all the work are left out. Writing is never trimmed away
   inside a kept group: a cut-off word is worse than a little more room.
5. The whole board is kept (the user's rule, D-058): starting from the work box, the
   surface it was written on (its brightness, taken from the unwritten parts of the work box)
   is followed outwards, row by row and column by column, while it covers most of the span.
   Thin gaps (the rails between sliding panels) are crossed; thin dark poles and legs cover
   too little of a row to pull the box along. When the surface cannot be told from its
   surroundings (a whole wall to write on, a white board on a white wall), it would fill the
   picture, and only the work box is used.
   :func:`crop_rect` adds the margin to the board (or work) box, widens it to the video's
   shape and limits the zoom.

One box per recorded part. Parts of the same size (one camera) get one crop, around all
their work, so the picture does not jump where they are joined. The render crops and scales
in its single encode.
"""

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from reel.config.schema import FramingSettings

BLUR_RADIUS = 4  # pixels of the 416 px analysis picture
PERSON_GROW = (0.15, 0.10)  # people's boxes are enlarged by this share (width, height)
GROUP_RADIUS = 2  # pixels of the 416 px picture: closer traces are one group
SURFACE_TOLERANCE = (20.0, 0.4)  # brightness: at least 20, or 40 % of board-to-room contrast
SURFACE_COVER = 0.5  # a row or column belongs to the board when the surface covers this much
SURFACE_GAP = (0.03, 0.04)  # gaps crossed: rows (share of height), columns (share of width)
SURFACE_TOO_BIG = 0.92  # a surface this wide and tall is the room, not a board
KEEP_SHARE = 0.02  # smaller groups (share of all the work) are left out
BOTTOM_ROWS = 0.015  # traces reaching this share of the picture's bottom touch the edge ...
BOTTOM_LIMIT = 0.3  # ... and are dropped when they are less than this share of all the work
GRAY = np.array([0.114, 0.587, 0.299], np.float32)  # BGR


@dataclass(frozen=True)
class Box:
    """A box in shares of the picture (0..1)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class Crop:
    """A crop in source pixels (as displayed), even numbers."""

    x: int
    y: int
    width: int
    height: int

    def zoom(self, source_width: int) -> float:
        return source_width / self.width


@dataclass(frozen=True)
class FramingResult:
    box: Box | None  # where the work happened; None: nothing found
    note: str
    windows: int = 0
    skipped: int = 0
    work_share: float = 0.0  # of the picture
    details: dict[str, Any] = field(default_factory=dict)
    board: Box | None = None  # the whole surface the work was on, when it could be told apart

    @property
    def frame(self) -> Box | None:
        """What must stay in the picture: the board and all the work."""
        return union([self.box, self.board])

    def to_dict(self) -> dict[str, Any]:
        box = self.box
        board = self.board
        return {
            "box": None if box is None else [round(v, 4) for v in (box.x0, box.y0, box.x1, box.y1)],
            "board": None
            if board is None
            else [round(v, 4) for v in (board.x0, board.y0, board.x1, board.y1)],
            "note": self.note,
            "windows": self.windows,
            "skipped": self.skipped,
            "work_share": round(self.work_share, 4),
            **self.details,
        }


def _box_blur(picture: np.ndarray, radius: int) -> np.ndarray:
    """Mean over a (2r+1) square, edges repeated (two passes of cumulative sums)."""
    size = 2 * radius + 1
    out = picture
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius + 1, radius)
        sums = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis, dtype=np.float64)
        upper = np.take(sums, np.arange(size, sums.shape[axis]), axis=axis)
        lower = np.take(sums, np.arange(0, sums.shape[axis] - size), axis=axis)
        out = ((upper - lower) / size).astype(np.float32)
    return out


def _neighbours(mask: np.ndarray) -> np.ndarray:
    """How many of the 3 x 3 square around each pixel (itself included) are set."""
    return np.rint(_box_blur(mask.astype(np.float32), 1) * 9).astype(np.int32)


def _groups(mask: np.ndarray) -> np.ndarray:
    """Label the 8-connected groups of set pixels (0 = not set), row runs joined by union-find."""
    parent = [0]

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    labels = np.zeros(mask.shape, np.int32)
    previous: list[tuple[int, int, int]] = []
    for y in range(mask.shape[0]):
        edges = np.diff(np.concatenate(([0], mask[y].astype(np.int8), [0])))
        current = []
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
            parent.append(len(parent))
            run = len(parent) - 1
            current.append((int(start), int(end), run))
            for above_start, above_end, above in previous:
                if above_start <= end and above_end >= start:  # touching, diagonals included
                    a, b = find(run), find(above)
                    if a != b:
                        parent[a] = b
            labels[y, start:end] = run
        previous = current
    roots = np.array([find(item) for item in range(len(parent))], np.int32)
    return roots[labels]


def union(boxes: Sequence[Box | None]) -> Box | None:
    """The box around all the given boxes (None ones left out)."""
    found = [box for box in boxes if box is not None]
    if not found:
        return None
    return Box(
        min(b.x0 for b in found), min(b.y0 for b in found),
        max(b.x1 for b in found), max(b.y1 for b in found),
    )  # fmt: skip


def _grow(coverage: np.ndarray, low: int, high: int, gap: int) -> tuple[int, int]:
    """Widen [low, high) over the covered places next to it, across gaps of up to ``gap``."""

    def walk(start: int, step: int) -> int | None:
        edge, misses, index = None, 0, start
        while 0 <= index < len(coverage):
            if coverage[index] >= SURFACE_COVER:
                edge, misses = index, 0
            else:
                misses += 1
                if misses > gap:
                    break
            index += step
        return edge

    before, after = walk(low - 1, -1), walk(high, 1)
    return (low if before is None else before), (high if after is None else after + 1)


def board_box(
    background: np.ndarray, work: np.ndarray, box: tuple[int, int, int, int]
) -> tuple[tuple[int, int, int, int] | None, str]:
    """The surface the work box lies on, grown to its edges (pixels), or None and why."""
    height, width = background.shape
    x0, y0, x1, y1 = box
    plain = background[y0:y1, x0:x1][~work[y0:y1, x0:x1]]
    if plain.size < 50:
        return None, "too little plain surface"
    colour = float(np.median(plain))
    outside = np.ones((height, width), bool)
    outside[y0:y1, x0:x1] = False
    room = float(np.median(background[outside])) if outside.any() else colour
    low, share = SURFACE_TOLERANCE
    similar = np.abs(background - colour) <= max(low, share * abs(room - colour))
    gap_rows, gap_columns = round(SURFACE_GAP[0] * height), round(SURFACE_GAP[1] * width)
    for _ in range(4):  # rows and columns in turn, until nothing changes
        y0n, y1n = _grow(similar[:, x0:x1].mean(axis=1), y0, y1, gap_rows)
        x0n, x1n = _grow(similar[y0n:y1n, :].mean(axis=0), x0, x1, gap_columns)
        if (x0n, y0n, x1n, y1n) == (x0, y0, x1, y1):
            break
        x0, y0, x1, y1 = x0n, y0n, x1n, y1n
    if (x1 - x0) >= SURFACE_TOO_BIG * width and (y1 - y0) >= SURFACE_TOO_BIG * height:
        return None, "the surface cannot be told from the room"
    return (x0, y0, x1, y1), f"board surface (brightness {colour:.0f}, room {room:.0f})"


def _touching_bottom(mask: np.ndarray) -> np.ndarray:
    """The set pixels connected (8 neighbours) to the bottom rows: grown from there."""
    rows = max(2, round(BOTTOM_ROWS * mask.shape[0]))
    region = np.zeros_like(mask)
    region[-rows:] = mask[-rows:]
    while True:
        grown = (_neighbours(region) > 0) & mask
        if np.array_equal(grown, region):
            return region
        region = grown


class WorkCollector:
    """Collects the analysis pictures of one recorded part and finds the work box."""

    def __init__(self, settings: FramingSettings, size: tuple[int, int], sample_seconds: float):
        self.settings = settings
        self.width, self.height = size
        self.window = max(5, round(settings.window_seconds / max(sample_seconds, 0.05)))
        self._frames: list[np.ndarray] = []
        self._masks: list[np.ndarray] = []
        self._previous: np.ndarray | None = None
        self.work = np.zeros((self.height, self.width), np.uint16)
        self.background: np.ndarray | None = None  # the room without people, latest known
        self.windows = 0
        self.skipped = 0

    def add(self, frame: np.ndarray, people: Sequence[Sequence[float]] = ()) -> None:
        """One BGR picture (``height`` x ``width``) and the people's boxes in its pixels."""
        gray = frame[: self.height, : self.width].astype(np.float32) @ GRAY
        mask = np.zeros((self.height, self.width), bool)
        for x0, y0, x1, y1 in people:
            grow_x, grow_y = (x1 - x0) * PERSON_GROW[0], (y1 - y0) * PERSON_GROW[1]
            left, right = max(0, int(x0 - grow_x)), min(self.width, math.ceil(x1 + grow_x))
            top, bottom = max(0, int(y0 - grow_y)), min(self.height, math.ceil(y1 + grow_y))
            if right > left and bottom > top:
                mask[top:bottom, left:right] = True
        self._frames.append(np.rint(gray).astype(np.uint8))
        self._masks.append(mask)
        if len(self._frames) >= self.window:
            self._close_window()

    def _close_window(self) -> None:
        frames, masks = np.stack(self._frames), np.stack(self._masks)
        self._frames, self._masks = [], []
        stack = frames.astype(np.float32)
        stack[masks] = np.nan
        known = (~masks).sum(axis=0) >= max(3, int(0.3 * len(frames)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels: hidden all along
            still = np.nanmedian(stack, axis=0)
        filled = np.where(known, still, np.nanmean(still[known]) if known.any() else 0.0)
        detail = filled - _box_blur(filled.astype(np.float32), BLUR_RADIUS)
        # The blur mixes the filled-in hidden pixels into their neighbours: those are unknown too.
        near_hidden = _box_blur((~known).astype(np.float32), BLUR_RADIUS) > 0
        detail[near_hidden] = np.nan
        if self.background is None:
            self.background = filled.astype(np.float32)
        else:
            self.background = np.where(known, still, self.background).astype(np.float32)
        previous, self._previous = self._previous, detail
        self.windows += 1
        if previous is None:
            return
        both = ~np.isnan(detail) & ~np.isnan(previous)
        if not both.any():
            return
        with np.errstate(invalid="ignore"):
            changed = (np.abs(detail - previous) > self.settings.change_level) & both
        if changed.sum() > self.settings.max_changed_share * both.sum():
            self.skipped += 1  # the whole scene changed: camera moved, lights
            return
        self.work += changed

    def finish(self) -> FramingResult:
        if len(self._frames) >= max(3, self.window // 3):
            self._close_window()
        self._frames, self._masks = [], []
        seen = self.work > 0
        solid = seen & (_neighbours(seen) >= 3)  # isolated specks are noise
        details: dict[str, Any] = {"analysis_size": [self.width, self.height]}
        bottom = _touching_bottom(solid)
        if bottom.any() and bottom.sum() < BOTTOM_LIMIT * solid.sum():
            details["dropped_at_bottom"] = round(float(bottom.sum() / solid.sum()), 4)
            solid &= ~bottom
        share = float(solid.mean())
        if self.windows < 2:
            return FramingResult(None, "too short to compare", self.windows, self.skipped,
                                 share, details)  # fmt: skip
        if share < self.settings.min_work_share:
            return FramingResult(None, "no writing or slide changes were seen", self.windows,
                                 self.skipped, share, details)  # fmt: skip
        near = _box_blur(solid.astype(np.float32), GROUP_RADIUS) > 0
        labels = _groups(near) * solid
        ids, counts = np.unique(labels[labels > 0], return_counts=True)
        kept = ids[counts >= KEEP_SHARE * counts.sum()]
        details["groups"] = [int(len(ids)), int(len(kept))]
        ys, xs = np.nonzero(np.isin(labels, kept))
        pixels = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        box = Box(pixels[0] / self.width, pixels[1] / self.height,
                  pixels[2] / self.width, pixels[3] / self.height)  # fmt: skip
        board = None
        if self.background is not None:
            found, details["board_note"] = board_box(self.background, solid, pixels)
            if found is not None:
                board = Box(found[0] / self.width, found[1] / self.height,
                            found[2] / self.width, found[3] / self.height)  # fmt: skip
        return FramingResult(box, "work found", self.windows, self.skipped, share, details,
                             board)  # fmt: skip


def _even(value: float) -> int:
    return max(0, int(value) // 2 * 2)


def crop_rect(
    box: Box | None, width: int, height: int, settings: FramingSettings
) -> tuple[Crop | None, str]:
    """The crop of a ``width`` x ``height`` picture around the work ``box``: the margin added,
    widened to the picture's shape, at most ``max_zoom``. None when it is not worth it."""
    if box is None:
        return None, "nothing to frame"
    aspect = width / height
    left, right = box.x0 * width, box.x1 * width
    top, bottom = box.y0 * height, box.y1 * height
    margin_x = max(settings.margin * (right - left), settings.min_margin * width)
    margin_y = max(settings.margin * (bottom - top), settings.min_margin * height)
    left, right = left - margin_x, right + margin_x
    top, bottom = top - margin_y, bottom + margin_y
    crop_w, crop_h = right - left, bottom - top
    if crop_w / crop_h < aspect:
        crop_w = crop_h * aspect
    else:
        crop_h = crop_w / aspect
    crop_w = max(crop_w, width / settings.max_zoom)
    crop_w = min(crop_w, width, height * aspect)
    crop_h = crop_w / aspect
    if crop_w >= (1 - settings.min_gain) * width:
        return None, "the work fills the picture"
    center_x, center_y = (left + right) / 2, (top + bottom) / 2
    x = min(max(center_x - crop_w / 2, 0.0), width - crop_w)
    y = min(max(center_y - crop_h / 2, 0.0), height - crop_h)
    crop = Crop(_even(x), _even(y), _even(crop_w), _even(crop_h))
    return crop, f"zoom {crop.zoom(width):.2f}x"
