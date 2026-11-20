"""The raw transcript on the cleaned timeline (T-406; FR-12, FR-13).

Every time is mapped through the cut list. Text that was cut out of the video is dropped:
- with word times, a word stays when its middle is kept; a segment without kept words is
  dropped, and its text is rebuilt from the kept words when some were dropped;
- without word times (the default, D-036), a segment stays when at least half of it is kept.
Boundary segments are clipped by the mapping itself (cut time collapses to the cut).
"""

from dataclasses import replace

from reel.cleaning.cutlist import CutList
from reel.stt import Segment, Transcription, Word

MIN_KEPT_SHARE = 0.5


def _kept(cut_list: CutList, start: float, end: float) -> bool:
    middle = start if end <= start else (start + end) / 2
    return cut_list.keeps(middle, middle + 1e-3) > 0


def remap_segment(segment: Segment, cut_list: CutList) -> Segment | None:
    """The segment on the cleaned timeline, or None when it was cut out."""
    to_clean = cut_list.to_clean
    if segment.words:
        words = tuple(
            Word(to_clean(w.start), to_clean(w.end), w.text, w.probability)
            for w in segment.words
            if _kept(cut_list, w.start, w.end)
        )
        if not words:
            return None
        text = segment.text
        if len(words) < len(segment.words):
            text = "".join(w.text for w in words).strip()
        start = min(to_clean(segment.start), words[0].start)
        end = max(to_clean(segment.end), words[-1].end)
        return replace(segment, start=start, end=end, text=text, words=words)
    if segment.duration > 0:
        if cut_list.keeps(segment.start, segment.end) < MIN_KEPT_SHARE * segment.duration:
            return None
    elif not _kept(cut_list, segment.start, segment.end):
        return None
    return replace(segment, start=to_clean(segment.start), end=to_clean(segment.end))


def remap_transcript(transcript: Transcription, cut_list: CutList) -> Transcription:
    """The clean transcript: ordered, every time inside ``[0, final duration]``."""
    segments = [
        mapped
        for segment in transcript.segments
        if (mapped := remap_segment(segment, cut_list)) is not None
    ]
    details = {
        **transcript.details,
        "remapped": {
            "from_segments": len(transcript.segments),
            "kept_segments": len(segments),
            "original_duration": round(cut_list.original_duration, 3),
            "final_duration": round(cut_list.final_duration, 3),
        },
    }
    return Transcription(transcript.language, tuple(segments), transcript.model, details)
