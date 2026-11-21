"""Typed schema of ``config/reel.toml`` (FR-34, NFR-9).

Every setting has a default, so a short file works. ``config/reel.example.toml`` lists all
of them with the same values; a test keeps the two in sync.

Deliberately absent:
- secrets (tokens, passwords, keys): they come from the environment or ``.env`` (NFR-5);
- the YouTube privacy status: it is always "unlisted" (D-017, PRD C-1).
"""

import re
import string
from datetime import time
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Mode = Literal["dry-run", "live"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
X264Preset = Literal[
    "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"
]

TITLE_FIELDS = frozenset({"group", "lesson", "date", "topic"})  # topic: only via {topic}
FOOTER_FIELDS = frozenset({"group", "lesson", "date", "title"})
POST_FIELDS = frozenset({"title", "url", "summary", "group", "lesson", "date", "duration"})

_HTTP_URL = re.compile(r"^https?://\S+$")
# An Armenian sentence that writes English terms in Latin letters, so Whisper does too (D-062).
TERM_EXAMPLE = (
    "Այսօր Python-ում կխոսենք dictionary-ների, hash table-ի և hash function-ի մասին։ "
    "Key-երը պետք է լինեն hashable, immutable type-եր, ոչ թե mutable, օրինակ string, tuple։"
)
# Terms heard in the lessons. With the example it stays under Whisper's ~220-token prompt limit.
TERM_VOCABULARY = (
    "Python, list, tuple, dict, dictionary, string, hash table, hash function, hashable, "
    "mutable, immutable, type, data type, object, class, function, method, index, slot, bucket, "
    "collision, reference, data structure, capacity, size, key-value, stack, stack frame, "
    "call stack, recursion, LIFO, FIFO, pop, print, input, output, error, while"
)


class Section(BaseModel):
    """Base of every section: unknown keys are errors and values are read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)


def template_fields(template: str) -> set[str]:
    """Return the ``{placeholder}`` names used in a ``str.format`` template."""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ValueError(
            f"invalid template ({exc}); write {{{{ and }}}} for literal braces"
        ) from None
    names: set[str] = set()
    for _, field_name, _, _ in parsed:
        if field_name is None:
            continue
        base = re.split(r"[.\[]", field_name, maxsplit=1)[0]
        if not base or base.isdigit():
            raise ValueError("placeholders must have a name, e.g. {title}")
        names.add(base)
    return names


def check_template(template: str, allowed: frozenset[str]) -> str:
    unknown = sorted(template_fields(template) - allowed)
    if unknown:
        listed = ", ".join("{" + name + "}" for name in sorted(allowed))
        raise ValueError(f"unknown placeholder {{{unknown[0]}}}; allowed: {listed}")
    return template


def check_phrases(phrases: tuple[str, ...]) -> tuple[str, ...]:
    cleaned = tuple(phrase.strip() for phrase in phrases)
    if not cleaned or not all(cleaned):
        raise ValueError("must be a non-empty list of non-empty phrases")
    return cleaned


def compile_regex(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from None


class PathsSettings(Section):
    base_dir: str = ""  # empty = the folder that contains config/
    data_dir: str = "data"
    database_file: str = ""  # empty = <data_dir>/reel.db
    logs_dir: str = "logs"
    models_dir: str = "models"
    groups_file: str = "config/groups.toml"
    prompts_dir: str = "config/prompts"
    ffmpeg: str = "tools/ffmpeg/bin/ffmpeg.exe"
    ffprobe: str = "tools/ffmpeg/bin/ffprobe.exe"
    ollama: str = "tools/ollama/ollama.exe"  # started by Reel when [llm] base_url is local

    @field_validator(
        "data_dir",
        "logs_dir",
        "models_dir",
        "groups_file",
        "prompts_dir",
        "ffmpeg",
        "ffprobe",
        "ollama",
    )
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @field_validator("database_file")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class IngestSettings(Section):
    watch_folders: tuple[str, ...] = ("inbox",)
    extensions: tuple[str, ...] = (".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi")
    ignore_patterns: tuple[str, ...] = ("~*", ".*", "*.part", "*.tmp", "*.crdownload")
    scan_interval_seconds: float = Field(default=15.0, ge=1, le=3600)
    stable_seconds: float = Field(default=60.0, ge=5, le=3600)
    min_duration_minutes: float = Field(default=5.0, ge=0, le=600)
    lesson_number_patterns: tuple[str, ...] = (
        r"(?i)(?:lecture|lesson|lec)[\s_.#-]*(\d{1,3})(?!\d)",
        r"(?i)(?<![a-z])L[\s_.#-]?(\d{1,3})(?!\d)",
        r"(?i)դաս[\s_.#-]*(\d{1,3})(?!\d)",
    )
    date_patterns: tuple[str, ...] = (
        r"(?<!\d)(?P<year>20\d{2})[-_.](?P<month>\d{2})[-_.](?P<day>\d{2})(?!\d)",  # 2026-11-19
        r"(?<!\d)(?P<day>\d{2})[-_.](?P<month>\d{2})[-_.](?P<year>20\d{2})(?!\d)",  # 19.11.2026
    )

    @field_validator("watch_folders")
    @classmethod
    def _folders(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        folders = tuple(folder.strip() for folder in value)
        if not folders or not all(folders):
            raise ValueError("list at least one folder; folder names must not be empty")
        return folders

    @field_validator("extensions")
    @classmethod
    def _extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        extensions = tuple(ext.strip().lower() for ext in value)
        if not extensions or any(not ext.startswith(".") or len(ext) < 2 for ext in extensions):
            raise ValueError('list extensions with the leading dot, e.g. ".mp4"')
        return extensions

    @field_validator("lesson_number_patterns")
    @classmethod
    def _lesson_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("list at least one pattern")
        for pattern in value:
            if compile_regex(pattern).groups != 1:
                raise ValueError(f"pattern {pattern!r} needs exactly one (group) around the number")
        return value

    @field_validator("date_patterns")
    @classmethod
    def _date_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            if not {"year", "month", "day"} <= set(compile_regex(pattern).groupindex):
                raise ValueError(
                    f"pattern {pattern!r} needs the named groups "
                    "(?P<year>...), (?P<month>...) and (?P<day>...)"
                )
        return value


class WorkerSettings(Section):
    count: int = Field(default=2, ge=1, le=32)
    heavy_stage_limit: int = Field(default=1, ge=1, le=16)
    max_attempts: int = Field(default=3, ge=1, le=20)
    backoff_base_seconds: float = Field(default=60.0, gt=0, le=86400)
    backoff_factor: float = Field(default=4.0, ge=1, le=100)
    backoff_max_seconds: float = Field(default=3600.0, gt=0, le=604800)
    lease_seconds: float = Field(default=300.0, ge=30, le=86400)
    stage_timeout_minutes: float = Field(default=360.0, ge=1, le=10080)
    idle_poll_seconds: float = Field(default=5.0, gt=0, le=300)
    stop_grace_seconds: float = Field(default=30.0, ge=0, le=3600)
    # Keep Windows from sleeping for being idle while a stage runs (D-056).
    keep_awake: bool = True

    @model_validator(mode="after")
    def _heavy_limit(self) -> Self:
        if self.heavy_stage_limit > self.count:
            raise ValueError("heavy_stage_limit cannot be larger than count")
        return self


class SttSettings(Section):
    provider: Literal["faster_whisper"] = "faster_whisper"
    model: str = Field(default="large-v3-turbo", min_length=1)  # T-303
    language: str = Field(default="hy", pattern=r"^[a-z]{2,3}$")
    compute_type: Literal["int8", "int8_float32", "int16", "float32", "auto"] = "int8"
    cpu_threads: int = Field(default=0, ge=0, le=256)  # 0 = automatic
    beam_size: int = Field(default=1, ge=1, le=10)  # 5 was slower and not better (T-303)
    # Decode a window again at higher temperatures when the result looks unreliable. Costly
    # for Armenian: low-confidence windows are decoded up to six times (T-303).
    temperature_fallback: bool = False
    vad: bool = True
    word_timestamps: bool = False  # +55 % transcription time (T-303)
    initial_prompt: str = TERM_VOCABULARY  # for all groups; the group's own words come first
    # English terms stay English (T-1010, D-062): the example sentence and the vocabulary go
    # to the first 30-second window, and the Armenian-letter spellings are replaced afterwards
    # (reel.stt.terms; [stt.terms] adds spellings). hotwords = true sends the prompt to every
    # window, but it takes room from the speech: a real lesson lost 10-25 % of its words.
    hotwords: bool = False
    term_example: str = Field(default=TERM_EXAMPLE, max_length=400)
    fix_terms: bool = True
    terms: dict[str, tuple[str, ...]] = {}


class LlmSettings(Section):
    provider: Literal["ollama"] = "ollama"
    base_url: str = Field(default="http://127.0.0.1:11434", pattern=_HTTP_URL.pattern)
    model: str = Field(default="gemma3:4b", min_length=1)  # T-304, D-038
    temperature: float = Field(default=0.2, ge=0, le=2)
    context_length: int = Field(default=8192, ge=2048, le=262144)  # in tokens
    timeout_seconds: float = Field(default=600.0, gt=0, le=7200)
    max_retries: int = Field(default=2, ge=0, le=10)
    # Let the project's Ollama use the integrated GPU (Intel Iris Xe via Vulkan): about 45 %
    # faster on this PC and it leaves the CPU to Whisper and FFmpeg (T-304).
    use_igpu: bool = True


class SilenceSettings(Section):
    noise_db: float = Field(default=-35.0, ge=-90, le=0)
    min_silence_seconds: float = Field(default=8.0, ge=1, le=3600)
    keep_seconds: float = Field(default=1.0, ge=0, le=30)
    require_static_video: bool = True
    freeze_noise: float = Field(default=0.003, gt=0, le=1)

    @model_validator(mode="after")
    def _keep_shorter_than_silence(self) -> Self:
        if self.keep_seconds * 2 >= self.min_silence_seconds:
            raise ValueError("keep_seconds must be less than half of min_silence_seconds")
        return self


class BoundarySettings(Section):
    start_search_minutes: float = Field(default=30.0, gt=0, le=240)
    end_search_minutes: float = Field(default=30.0, gt=0, le=240)
    anchor_window_minutes: float = Field(default=3.0, gt=0, le=60)
    fuzzy_threshold: float = Field(default=0.85, ge=0.5, le=1)
    use_llm: bool = True
    greeting_phrases: tuple[str, ...] = (
        "բարև ձեզ",
        "բարև բոլորին",
        "բարի երեկո",
        "բարի օր",
        "ողջույն",
        "ողջունում եմ",
        "hello everyone",
        "good evening",
    )
    topic_phrases: tuple[str, ...] = (
        "այսօր կխոսենք",
        "այսօր խոսելու ենք",
        "այսօր կանցնենք",
        "այսօր անցնելու ենք",
        "այսօրվա թեման",
        "մեր այսօրվա թեման",
        "այսօրվա դասի թեման",
        "սկսենք դասը",
        "today we will talk about",
        "our topic for today",
        "let's start",
    )
    closing_phrases: tuple[str, ...] = (
        "այսօրվա համար այսքանը",
        "այսքանը այսօրվա համար",
        "այսօր այսքանը",
        "շնորհակալություն ուշադրության համար",
        "շնորհակալություն",
        "կհանդիպենք",
        "հաջորդ դասին",
        "մինչ հանդիպում",
        "ցտեսություն",
        "that's all for today",
        "see you next time",
    )

    @field_validator("greeting_phrases", "topic_phrases", "closing_phrases")
    @classmethod
    def _phrases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return check_phrases(value)


class EmptyBoardSettings(Section):
    """D-040 (user): when nobody is at the board for more than ``min_empty_seconds``, the stretch
    is shortened to ``keep_seconds`` (half kept at each side), whether or not someone is heard.
    This removes the break and every other empty stretch (FR-8)."""

    enabled: bool = True
    min_empty_seconds: float = Field(default=10.0, ge=2, le=3600)
    keep_seconds: float = Field(default=5.0, ge=0, le=600)
    model: Literal["yolox_tiny", "yolox_nano"] = "yolox_tiny"  # models/person/<model>.onnx
    person_score: float = Field(default=0.4, gt=0, lt=1)
    min_person_height: float = Field(default=0.08, ge=0, lt=1)  # share of the frame height
    sample_seconds: float = Field(default=1.0, gt=0, le=10)  # keyframes are used if this dense
    hwaccel: Literal["auto", "none", "d3d11va"] = "auto"  # video decoding on the GPU

    @model_validator(mode="after")
    def _keep_shorter(self) -> Self:
        if self.keep_seconds >= self.min_empty_seconds:
            raise ValueError("keep_seconds must be smaller than min_empty_seconds")
        return self


class FramingSettings(Section):
    """D-058 (user): the picture is zoomed to where the lesson's work happens (writing, slides),
    with a small margin. One crop per recorded part, applied by the render."""

    enabled: bool = True
    margin: float = Field(default=0.03, ge=0, le=0.5)  # share of the board box, on each side
    min_margin: float = Field(default=0.015, ge=0, le=0.2)  # share of the picture, at least
    max_zoom: float = Field(default=2.0, ge=1, le=4)  # 4K -> 1080p keeps full detail up to 2x
    min_gain: float = Field(default=0.05, ge=0, lt=1)  # a crop keeping more is not worth it
    window_seconds: float = Field(default=60.0, ge=10, le=600)  # one still picture per window
    change_level: float = Field(default=12.0, gt=0, le=255)  # detail change that counts as work
    max_changed_share: float = Field(default=0.25, gt=0, le=1)  # more: the scene changed
    min_work_share: float = Field(default=0.002, ge=0, lt=1)  # less work: no crop


class CutSettings(Section):
    padding_seconds: float = Field(default=0.5, ge=0, le=10)
    min_keep_seconds: float = Field(default=2.0, ge=0, le=600)
    min_remove_seconds: float = Field(default=3.0, ge=0, le=600)
    fade_ms: int = Field(default=30, ge=0, le=1000)


class CleaningSettings(Section):
    silence: SilenceSettings = SilenceSettings()
    boundaries: BoundarySettings = BoundarySettings()
    empty_board: EmptyBoardSettings = EmptyBoardSettings()
    framing: FramingSettings = FramingSettings()
    cuts: CutSettings = CutSettings()


class AudioSettings(Section):
    loudness_target_lufs: float = Field(default=-16.0, ge=-70, le=-5)
    true_peak_db: float = Field(default=-1.5, ge=-9, le=0)
    loudness_range: float = Field(default=11.0, ge=1, le=50)


class RenderSettings(Section):
    # "auto": Intel Quick Sync on the GPU (decode, scale, encode; 3x faster here, T-405) and
    # libx264 on the CPU when that fails or cannot be used.
    video_encoder: Literal["auto", "libx264", "h264_qsv"] = "auto"
    preset: X264Preset = "veryfast"  # libx264
    crf: int = Field(default=21, ge=0, le=51)  # libx264: lower = better, bigger
    qsv_quality: int = Field(default=26, ge=1, le=51)  # h264_qsv: lower = better, bigger
    audio_bitrate: str = Field(default="160k", pattern=r"^\d{2,3}k$")
    # Taller videos (e.g. 4K from an iPhone) are scaled down to this height; smaller ones
    # are never scaled up (user, D-037).
    max_height: int = Field(default=1080, ge=360, le=4320)


class MetadataSettings(Section):
    title_template: str = "{group} · Lecture {lesson}"
    description_footer: str = ""
    min_chapters: int = Field(default=3, ge=3, le=50)
    min_chapter_seconds: float = Field(default=10.0, ge=10, le=3600)
    max_chapter_title_chars: int = Field(default=60, ge=10, le=100)
    window_minutes: float = Field(default=3.0, ge=0.5, le=30)
    # A new chapter starts exactly at such a phrase when one is near the topic change (T-501).
    chapter_start_phrases: tuple[str, ...] = (
        "այսօր անցնելու ենք",
        "այսօր կանցնենք",
        "հիմա անցնենք",
        "անցնենք հաջորդ",
        "հաջորդ թեման",
        "հիմա կխոսենք",
        "հիմա խոսենք",
        "սկսենք",
        "let's move on",
        "next topic",
    )
    thumbnail_enabled: bool = True
    # No chapters and fewer words per minute of video than this: the speech is not clear, and
    # staff decide whether to publish (with chapters they set) or cancel (T-1012). 0 = never.
    min_words_per_minute: float = Field(default=20.0, ge=0, le=300)

    @field_validator("title_template")
    @classmethod
    def _title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return check_template(value, TITLE_FIELDS)

    @field_validator("description_footer")
    @classmethod
    def _footer(cls, value: str) -> str:
        return check_template(value, FOOTER_FIELDS)

    @field_validator("chapter_start_phrases")
    @classmethod
    def _phrases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return check_phrases(value)


class ConsentSettings(Section):
    provider: Literal["file", "http"] = "file"
    file_path: str = "config/consent.json"
    http_base_url: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0, le=300)

    @model_validator(mode="after")
    def _http_needs_url(self) -> Self:
        if self.provider == "http" and not _HTTP_URL.match(self.http_base_url):
            raise ValueError(
                'http_base_url (http:// or https://) is required when provider = "http"'
            )
        return self


class AuthSettings(Section):
    """Where OAuth tokens are kept (T-701, NFR-5). Static secrets stay in the environment/.env."""

    # "windows": Windows Credential Manager (encrypted by Windows for this user account);
    # "posix": an owner-only file on a Linux server (folder 0700, file 0600; T-1018);
    # "file": a plain JSON file for development only (a warning is logged on every use).
    store: Literal["windows", "posix", "file"] = "windows"
    store_namespace: str = Field(default="Reel", pattern=r"^[A-Za-z0-9._-]{1,40}$")
    dev_file: str = "data/secrets.dev.json"
    posix_file: str = "data/secrets/tokens.json"  # used only when store = "posix"


class YoutubeSettings(Section):
    # The privacy status is always "unlisted" and deliberately not configurable (D-017).
    mode: Mode = "dry-run"
    category_id: str = "27"  # Education
    default_language: str = Field(default="hy", pattern=r"^[a-z]{2,3}$")
    create_missing_playlists: bool = False
    # A re-run with a different cleaned file uploads a new video; the old one is kept
    # (unlisted, out of the playlist) unless this is true (T-702).
    delete_replaced_videos: bool = False


class SlackSettings(Section):
    mode: Mode = "dry-run"
    message_template: str = "🎬 {title}\n{summary}\n{url}"

    @field_validator("message_template")
    @classmethod
    def _template(cls, value: str) -> str:
        return check_template(value, POST_FIELDS)


class ClassroomSettings(Section):
    mode: Mode = "dry-run"
    post_type: Literal["announcement", "material"] = "announcement"
    text_template: str = "{title}\n{summary}"

    @field_validator("text_template")
    @classmethod
    def _template(cls, value: str) -> str:
        return check_template(value, POST_FIELDS)


class LibrarySettings(Section):
    provider: Literal["local", "http"] = "local"
    http_base_url: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0, le=300)

    @model_validator(mode="after")
    def _http_needs_url(self) -> Self:
        if self.provider == "http" and not _HTTP_URL.match(self.http_base_url):
            raise ValueError(
                'http_base_url (http:// or https://) is required when provider = "http"'
            )
        return self


class NotificationSettings(Section):
    mode: Mode = "dry-run"
    slack_channel: str = ""
    events: tuple[Literal["failed", "on_hold", "flagged"], ...] = ("failed", "on_hold", "flagged")

    @model_validator(mode="after")
    def _live_needs_channel(self) -> Self:
        if self.mode == "live" and not self.slack_channel.strip():
            raise ValueError('slack_channel is required when mode = "live"')
        return self


class WebSettings(Section):
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8080, ge=1, le=65535)
    base_url: str = Field(default="http://127.0.0.1:8080", pattern=_HTTP_URL.pattern)
    session_hours: float = Field(default=12.0, gt=0, le=720)
    upload_max_gb: float = Field(default=20.0, gt=0, le=500)  # per file (T-807)
    tls: bool = False  # HTTPS with the certificate made by `reel cert` (T-1008, D-060)
    # Anyone who opens the dashboard may make an account; its uploads are only processed
    # until the admin lets it publish (T-1013, D-065).
    registration: bool = True

    @model_validator(mode="after")
    def _https_with_tls(self) -> Self:
        if self.tls and not self.base_url.startswith("https://"):
            raise ValueError("tls = true needs an https:// base_url (the address staff open)")
        return self


class ScheduleSettings(Section):
    timezone: str = "Asia/Yerevan"
    same_evening_cutoff: time = time(23, 59)

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(
                f"unknown time zone {value!r}; use a name like 'Asia/Yerevan'"
            ) from None
        return value


class RetentionSettings(Section):
    working_files_days: int = Field(default=30, ge=0, le=3650)  # 0 = keep forever
    transcripts_days: int = Field(default=365, ge=0, le=3650)  # 0 = keep forever
    delete_source_recordings: bool = False  # D-021
    source_recordings_days: int = Field(default=90, ge=7, le=3650)
    # a user's video that is only processed (T-1014): its processed video and uploaded
    # files go this many days after the job ended; 0 = keep forever
    processed_only_days: int = Field(default=30, ge=0, le=3650)


class BudgetSettings(Section):
    max_processing_minutes: float = Field(default=240.0, ge=10, le=10080)


class LoggingSettings(Section):
    level: LogLevel = "INFO"  # what is written to the log files
    console_level: LogLevel = "INFO"  # what long-running commands show in the terminal
    file_max_mb: float = Field(default=10.0, ge=0.1, le=1024)
    file_backups: int = Field(default=10, ge=1, le=100)
    job_logs: bool = True  # also write data/jobs/<job id>/job.log
    quiet_loggers: tuple[str, ...] = (
        "urllib3",
        "httpx",
        "httpcore",
        "googleapiclient.discovery_cache",
    )

    @field_validator("level", "console_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class Settings(Section):
    """All settings of ``config/reel.toml``."""

    paths: PathsSettings = PathsSettings()
    ingest: IngestSettings = IngestSettings()
    workers: WorkerSettings = WorkerSettings()
    stt: SttSettings = SttSettings()
    llm: LlmSettings = LlmSettings()
    cleaning: CleaningSettings = CleaningSettings()
    audio: AudioSettings = AudioSettings()
    render: RenderSettings = RenderSettings()
    metadata: MetadataSettings = MetadataSettings()
    consent: ConsentSettings = ConsentSettings()
    auth: AuthSettings = AuthSettings()
    youtube: YoutubeSettings = YoutubeSettings()
    slack: SlackSettings = SlackSettings()
    classroom: ClassroomSettings = ClassroomSettings()
    library: LibrarySettings = LibrarySettings()
    notifications: NotificationSettings = NotificationSettings()
    web: WebSettings = WebSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    retention: RetentionSettings = RetentionSettings()
    budget: BudgetSettings = BudgetSettings()
    logging: LoggingSettings = LoggingSettings()
