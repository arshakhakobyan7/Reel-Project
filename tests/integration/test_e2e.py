"""End-to-end user journeys on generated recordings (T-1001; UJ-1, UJ-3, UJ-4, UJ-5).

Every stage is the real one with real FFmpeg. Only the heavy or external parts are stand-ins:
the speech model (a scripted Armenian lesson fitted to the audio), the LLM (scripted labels),
the person detector (busy picture = someone at the board) and, in the last journey, the
YouTube, Slack and Classroom services (local fakes).
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))  # the service fakes

from classroom_fake import ClassroomFake  # noqa: E402
from test_publishing_stages import Codec, SlackFake, Tokens  # noqa: E402
from youtube_fake import YoutubeFake  # noqa: E402

from reel.config import load_config  # noqa: E402
from reel.db import (  # noqa: E402
    ConsentCheck,
    CutList,
    Job,
    JobStatus,
    LibraryEntry,
    PublicationTarget,
    Recording,
    Stage,
    StageRun,
    VideoMetadata,
    init_database,
    open_database,
)
from reel.db.enums import PublicationStatus, StageRunStatus  # noqa: E402
from reel.llm import FakeLLM  # noqa: E402
from reel.media.testmedia import Part as Scene  # noqa: E402
from reel.media.testmedia import make_lesson  # noqa: E402
from reel.publish.classroom import ClassroomPublisher  # noqa: E402
from reel.publish.slack import SlackPublisher  # noqa: E402
from reel.publish.youtube import CHUNK_UNIT, YoutubePublisher  # noqa: E402
from reel.queue import JobQueue, StageRegistry, Worker, create_job  # noqa: E402
from reel.runtime import Parts, Runtime  # noqa: E402
from reel.stages.chapters import ChaptersStage  # noqa: E402
from reel.stages.cleaning import CleanStage  # noqa: E402
from reel.stages.consent import ConsentStage  # noqa: E402
from reel.stages.importing import ImportStage  # noqa: E402
from reel.stages.publishing import PostStage, UploadStage  # noqa: E402
from reel.stages.transcribing import TranscribeStage  # noqa: E402
from reel.stt import FakeTranscriber, Transcriber  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG = BIN / "ffmpeg.exe"
pytestmark = pytest.mark.skipif(not FFMPEG.is_file(), reason="run scripts/setup_ffmpeg.py first")

# chatter | lesson | nobody at the board (silent) | lesson | chatter  (120 s)
SCENES = (
    Scene(15, "moving", "noise"),
    Scene(40, "moving", "tone"),
    Scene(20, "static", "silence"),
    Scene(35, "moving", "tone"),
    Scene(10, "moving", "noise"),
)
GROUPS = """
[groups."Python 12"]
youtube_playlist_id = "PL_GROUP"
slack_channel = "C0123456789"
classroom_course_id = "course-12"
"""


def config(*, live: bool = False, min_empty: float = 10, min_silence: float = 8) -> str:
    text = f"""
[paths]
ffmpeg = "{(BIN / "ffmpeg.exe").as_posix()}"
ffprobe = "{(BIN / "ffprobe.exe").as_posix()}"
prompts_dir = "{(ROOT / "config" / "prompts").as_posix()}"
[ingest]
min_duration_minutes = 0.5
stable_seconds = 5
scan_interval_seconds = 1
[cleaning.boundaries]
use_llm = false
[cleaning.empty_board]
min_empty_seconds = {min_empty}
[cleaning.silence]
min_silence_seconds = {min_silence}
[render]
video_encoder = "libx264"
preset = "ultrafast"
[workers]
count = 2
max_attempts = 2
backoff_base_seconds = 0.01
backoff_factor = 1.0
idle_poll_seconds = 0.02
"""
    if live:
        text += '[youtube]\nmode = "live"\n[slack]\nmode = "live"\n[classroom]\nmode = "live"\n'
    return text


def person_if_busy(frame: np.ndarray, content_height: int) -> float:
    return 0.9 if frame[:content_height].std() > 10 else 0.0


def scripted(prompt, schema):
    if "title" in schema.model_fields:
        return {"title": prompt.split("\n- ")[1].split("\n")[0]}
    if "dictionary" in prompt:
        return {"label": "Dictionary և նրա մեթոդները", "words": ["dictionary-ին"]}
    return {"label": "Ցուցակներ և for ցիկլ", "words": ["ցուցակը"]}


def registry(*, transcriber=None, publishers=None) -> StageRegistry:
    upload, post = UploadStage(), PostStage()
    if publishers is not None:
        upload, post = publishers
    return StageRegistry([
        ImportStage(),
        TranscribeStage(lambda cfg: transcriber or FakeTranscriber()),
        CleanStage(detector=person_if_busy),
        ChaptersStage(lambda cfg: FakeLLM([scripted] * 500)),
        ConsentStage(),
        upload,
        post,
    ])  # fmt: skip


def video_url(job_id: int):
    return select(PublicationTarget.url).where(
        PublicationTarget.job_id == job_id, PublicationTarget.target == "youtube"
    )


@pytest.fixture(scope="module")
def recording(tmp_path_factory) -> Path:
    return make_lesson(FFMPEG, tmp_path_factory.mktemp("e2e") / "lesson.mp4", SCENES)


class Site:
    def __init__(self, project: Path, write_config, recording: Path, **options) -> None:
        self.project, self.write_config = project, write_config
        self.source = recording
        write_config(GROUPS, "groups.toml")  # before the configuration is loaded
        self.reconfigure(**options)
        self.consent()
        init_database(self.cfg.paths.database_file)
        self.db = open_database(self.cfg.paths.database_file)

    def reconfigure(self, **options) -> None:
        self.write_config(config(**options))
        self.cfg = load_config(self.project / "config" / "reel.toml")

    def consent(self, *statuses: str) -> None:
        students = [{"id": f"s{i}", "status": s} for i, s in enumerate(statuses or ("granted",))]
        (self.project / "config" / "consent.json").write_text(
            json.dumps({"Python 12": {"group_ref": "core-12", "students": students}}), "utf-8"
        )

    def add(self, name: str = "Python 12/Lecture 14 2026-11-19.mp4") -> int:
        """Queue the lesson the way the watcher does."""
        path = self.project / "inbox" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.source.read_bytes())
        with self.db.transaction() as s:
            recording = Recording(source_path=str(path), size_bytes=path.stat().st_size)
            s.add(recording)
            return create_job(s, recording).id

    def work(self, stages: StageRegistry) -> list:
        attention: list = []
        worker = Worker(self.db, stages, lambda: self.cfg, notifier=attention.append)
        worker.start(until_idle=True)
        assert worker.join(300), "the pipeline did not finish"
        return attention

    def job(self, job_id: int) -> Job:
        with self.db.transaction() as s:
            job = s.get(Job, job_id)
            s.expunge(job)
            return job

    def count(self, model, **where) -> int:
        with self.db.transaction() as s:
            query = select(func.count()).select_from(model)
            for key, value in where.items():
                query = query.where(getattr(model, key) == value)
            return s.scalar(query)

    def runs(self, job_id: int, stage: Stage) -> list[StageRunStatus]:
        with self.db.transaction() as s:
            query = select(StageRun.status).where(
                StageRun.job_id == job_id, StageRun.stage == stage
            )
            return list(s.scalars(query.order_by(StageRun.id)))


@pytest.fixture
def site(project, write_config, recording):
    sites = []

    def make(**options) -> Site:
        made = Site(project, write_config, recording, **options)
        sites.append(made)
        return made

    yield make
    for made in sites:
        made.db.dispose()


def test_uj1_a_recording_dropped_in_the_folder_reaches_dry_run_publications(site):
    reel = site()
    target = reel.project / "inbox" / "Python 12" / "Lecture 14 2026-11-19.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(reel.source.read_bytes())
    runtime = Runtime(reel.db, lambda: reel.cfg, parts=Parts(web=False), registry=registry())
    runtime.start()
    try:
        deadline = time.monotonic() + 300
        job = None
        while time.monotonic() < deadline:
            with reel.db.transaction() as s:
                job = s.scalar(select(Job))
                if job is not None and job.status in {JobStatus.DONE, JobStatus.FAILED,
                                                      JobStatus.ON_HOLD}:  # fmt: skip
                    break
            time.sleep(0.2)
    finally:
        runtime.stop(timeout=30)
    job = reel.job(job.id)
    assert job.status is JobStatus.DONE, (job.status, job.stage, job.last_error, job.hold_message)
    with reel.db.transaction() as s:
        recording = s.get(Recording, job.recording_id)
        assert (recording.group_name, recording.lesson_number) == ("Python 12", 14)
        cut = s.scalar(select(CutList).where(CutList.job_id == job.id))
        assert 60 < cut.final_duration_seconds < cut.original_duration_seconds - 20  # cleaned
        meta = s.scalar(select(VideoMetadata).where(VideoMetadata.job_id == job.id))
        assert meta.title and "Python 12" in meta.title
        rows = s.scalars(select(PublicationTarget).where(PublicationTarget.job_id == job.id)).all()
        assert {row.target.value for row in rows} == {"youtube", "slack", "classroom", "library"}
        states = {row.target.value: (row.status.value, row.dry_run) for row in rows}
        assert all(row.status is PublicationStatus.PUBLISHED and row.dry_run for row in rows), (
            states
        )
    assert (reel.cfg.paths.data_dir / "jobs" / str(job.id) / "cleaned.mp4").is_file()


def test_uj3_a_consent_hold_is_released_by_staff_and_the_job_finishes(site):
    reel = site()
    reel.consent("granted", "denied")
    job_id = reel.add()
    attention = reel.work(registry())
    job = reel.job(job_id)
    assert job.status is JobStatus.ON_HOLD and job.stage is Stage.CONSENT
    assert job.hold_reason == "consent_opted_out" and [a.kind for a in attention] == ["held"]
    assert reel.count(PublicationTarget) == 0  # nothing published while held
    JobQueue(reel.db).release(job_id, "anna", advance=True, note="the student is not on camera")
    reel.work(registry())
    assert reel.job(job_id).status is JobStatus.DONE
    with reel.db.transaction() as s:
        check = s.scalar(select(ConsentCheck).where(ConsentCheck.job_id == job_id))
        assert check.released_by == "anna" and check.release_note.startswith("the student")


class Broken(Transcriber):
    name = "broken"

    def transcribe(self, audio, **kwargs):
        raise RuntimeError("the speech model crashed")


def test_uj4_a_failure_is_retried_fails_and_is_fixed_by_a_rerun(site):
    reel = site()
    job_id = reel.add()
    attention = reel.work(registry(transcriber=Broken()))
    job = reel.job(job_id)
    assert job.status is JobStatus.FAILED and job.stage is Stage.TRANSCRIBE
    assert "the speech model crashed" in job.last_error and [a.kind for a in attention] == [
        "failed"
    ]
    assert reel.runs(job_id, Stage.TRANSCRIBE) == [StageRunStatus.FAILED] * 2  # retried once
    JobQueue(reel.db).rerun(job_id, "anna", Stage.TRANSCRIBE, note="model fixed")
    reel.work(registry())  # the fixed transcriber
    assert reel.job(job_id).status is JobStatus.DONE
    assert reel.runs(job_id, Stage.IMPORT) == [StageRunStatus.SUCCEEDED]  # not repeated


def test_uj5_a_wrong_cut_is_fixed_and_the_rerun_posts_nothing_twice(site):
    with YoutubeFake() as youtube, ClassroomFake() as classroom:
        slack = SlackFake()
        reel = site(live=True)
        codec = Codec()

        def publishers():
            return (
                UploadStage(
                    lambda settings, access: YoutubePublisher(
                        settings, access, api_url=youtube.url + "/api/",
                        upload_url=youtube.url + "/upload/", chunk_size=CHUNK_UNIT,
                        sleep=lambda seconds: None, session_codec=codec,
                    ),
                    tokens_factory=lambda cfg: Tokens(),
                ),
                PostStage(
                    slack_factory=lambda settings, token: SlackPublisher(settings, client=slack),
                    classroom_factory=lambda settings, access: ClassroomPublisher(
                        settings, access, api_url=classroom.url),
                    tokens_factory=lambda cfg: Tokens(),
                ),
            )  # fmt: skip

        job_id = reel.add()
        reel.work(registry(publishers=publishers()))
        assert reel.job(job_id).status is JobStatus.DONE
        first = reel.job(job_id)
        with reel.db.transaction() as s:
            before = s.scalar(
                select(CutList).where(CutList.job_id == job_id)
            ).final_duration_seconds
            url = s.scalar(video_url(job_id))
        assert first.status is JobStatus.DONE and url and len(slack.messages) == 1

        # Staff: "the quiet board part was cut, but the teacher was writing there": the
        # empty-board and silence rules are relaxed so that such a part is kept.
        reel.reconfigure(live=True, min_empty=60, min_silence=60)
        JobQueue(reel.db).rerun(job_id, "anna", Stage.CLEAN, note="board part wrongly cut")
        reel.work(registry(publishers=publishers()))
        assert reel.job(job_id).status is JobStatus.DONE
        with reel.db.transaction() as s:
            versions = s.scalars(select(CutList).where(CutList.job_id == job_id)
                                 .order_by(CutList.version)).all()  # fmt: skip
            assert [v.version for v in versions] == [1, 2]
            assert versions[1].final_duration_seconds > before + 10  # the board part is kept
            new_url = s.scalar(video_url(job_id))
        assert new_url != url  # a new cleaned file is a new video (the old one is unlisted)
        assert len(slack.messages) == 1 and new_url in slack.messages[0]["text"]  # updated
        assert len(classroom.active()) == 1  # replaced, not added
        assert reel.count(LibraryEntry) == 1
