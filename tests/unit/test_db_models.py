"""Tables and models (T-101): every entity round-trips; constraints protect the data."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm.exc import StaleDataError

from reel.db import (
    PIPELINE,
    ActorKind,
    AppMeta,
    ConsentCheck,
    ConsentResult,
    CutList,
    Job,
    JobEvent,
    JobStatus,
    LibraryEntry,
    PublicationStatus,
    PublicationTarget,
    Recording,
    SchemaVersion,
    SeenFile,
    SeenFileState,
    StaffUser,
    Stage,
    StageRun,
    StageRunStatus,
    TargetKind,
    Transcript,
    TranscriptKind,
    VideoMetadata,
    validate_segments,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
T0 = datetime(2026, 11, 18, 14, 0, 0, tzinfo=UTC)
YEREVAN = ZoneInfo("Asia/Yerevan")
SEGMENTS = [
    {"start": 0.0, "end": 312.5, "action": "remove", "reason": "before class"},
    {"start": 312.5, "end": 5400.0, "action": "keep", "reason": "lesson"},
    {"start": 5400.0, "end": 6000.0, "action": "remove", "reason": "break", "confidence": 0.9},
    {"start": 6000.0, "end": 9660.0, "action": "keep", "reason": "lesson"},
]

EXPECTED_TABLES = {
    "recordings",
    "seen_files",
    "jobs",
    "stage_runs",
    "job_events",
    "cut_lists",
    "transcripts",
    "video_metadata",
    "consent_checks",
    "publication_targets",
    "library_entries",
    "staff_users",
    "schema_versions",
    "app_meta",
}


def add_recording(session, content_hash=HASH_A, **values) -> Recording:
    values.setdefault("source_path", r"C:\Lessons\Python 12\Դաս 14.mp4")
    values.setdefault("size_bytes", 3_500_000_000)
    recording = Recording(content_hash=content_hash, **values)
    session.add(recording)
    session.flush()
    return recording


def add_job(session, content_hash=HASH_A) -> Job:
    recording = add_recording(session, content_hash)
    job = Job(recording=recording)
    session.add(job)
    session.flush()
    return job


def test_schema_has_every_table(db):
    assert set(inspect(db.engine).get_table_names()) == EXPECTED_TABLES


def test_every_entity_is_stored_and_read_back(db):
    with db.transaction() as s:
        recording = add_recording(
            s,
            file_modified_at=T0,
            group_name="Python 12",
            lesson_number=14,
            recording_date=date(2026, 11, 18),
            identification={"group": "folder", "lesson": "file name", "date": "file name"},
            original_duration_seconds=9660.0,
            media_info={"video": {"codec": "h264", "width": 1920}, "audio": {"codec": "aac"}},
            detected_at=T0,
        )
        s.add(
            SeenFile(
                path=recording.source_path,
                size_bytes=recording.size_bytes,
                modified_at=T0,
                state=SeenFileState.QUEUED,
                recording=recording,
            )
        )
        job = Job(
            recording=recording,
            stage=Stage.CONSENT,
            status=JobStatus.ON_HOLD,
            attempts=1,
            hold_reason="consent_opted_out",
            hold_message="1 student opted out",
            flags=["llm_fallback"],
            started_at=T0,
        )
        s.add(job)
        s.flush()
        job_id, recording_id = job.id, recording.id
        job.stage_runs.append(
            StageRun(
                stage=Stage.TRANSCRIBE,
                attempt=1,
                status=StageRunStatus.SUCCEEDED,
                worker="worker-1",
                started_at=T0,
                finished_at=T0 + timedelta(minutes=95),
                metrics={"audio_seconds": 9660.0, "compute_seconds": 5700.2},
            )
        )
        job.events.append(
            JobEvent(
                kind="held",
                actor_kind=ActorKind.WORKER,
                actor_name="worker-1",
                stage=Stage.CONSENT,
                message="Ուսանողներից մեկը համաձայն չէ",
                data={"students": ["s-17"]},
            )
        )
        cut_list = CutList(
            version=1,
            original_duration_seconds=9660.0,
            final_duration_seconds=8747.5,
            segments=SEGMENTS,
            parameters={"silence_noise_db": -35},
        )
        job.cut_lists.append(cut_list)
        s.flush()
        job.transcripts.append(
            Transcript(
                kind=TranscriptKind.RAW,
                language="hy",
                text="Բարև ձեզ։ Այսօր կխոսենք բառարանների մասին։",
                segments=[{"start": 312.9, "end": 316.0, "text": "Բարև ձեզ։"}],
                model="faster-whisper large-v3 int8",
                details={"vad": True},
            )
        )
        job.transcripts.append(
            Transcript(
                kind=TranscriptKind.CLEAN,
                language="hy",
                text="Բարև ձեզ։",
                segments=[{"start": 0.4, "end": 3.5, "text": "Բարև ձեզ։"}],
                cut_list=cut_list,
            )
        )
        job.video_metadata = VideoMetadata(
            title="Python 12 · Lecture 14",
            summary="Բառարաններ, live coding",
            description="Բառարաններ\n\n0:00 Ներածություն",
            chapters=[{"start": 0.0, "title": "Ներածություն"}, {"start": 95.0, "title": "dict"}],
            thumbnail_path="data/jobs/1/thumbnail.jpg",
            generator={"model": "qwen3:8b", "fallback": False},
        )
        job.consent_checks.append(
            ConsentCheck(
                checked_at=T0,
                provider="file",
                group_ref="core-42",
                students=[{"id": "s-1", "status": "consented"}, {"id": "s-17", "status": "no"}],
                result=ConsentResult.OPTED_OUT,
                released_by="anna",
                released_at=T0 + timedelta(hours=1),
                release_note="s-17 is not on camera",
            )
        )
        job.publications.append(
            PublicationTarget(
                target=TargetKind.YOUTUBE,
                status=PublicationStatus.PUBLISHED,
                external_id="dQw4w9WgXcQ",
                url="https://youtu.be/dQw4w9WgXcQ",
                details={"playlist_id": "PL123", "playlist_item_id": "UExx"},
                attempts=1,
                published_at=T0,
            )
        )
        job.publications.append(
            PublicationTarget(target=TargetKind.SLACK, dry_run=True, last_error="rate limited")
        )
        job.library_entry = LibraryEntry(
            title="Python 12 · Lecture 14",
            video_url="https://youtu.be/dQw4w9WgXcQ",
            group_name="Python 12",
            lesson_number=14,
            recording_date=date(2026, 11, 18),
            chapters=[{"start": 0.0, "title": "Ներածություն"}],
        )
        s.add(StaffUser(username="Anna", display_name="Աննա", password_hash="scrypt$..."))

    with db.transaction() as s:
        recording = s.get(Recording, recording_id)
        assert recording.content_hash == HASH_A
        assert recording.source_path.endswith("Դաս 14.mp4")
        assert recording.recording_date == date(2026, 11, 18)
        assert recording.detected_at == T0 and recording.detected_at.tzinfo is UTC
        assert recording.media_info["video"]["width"] == 1920
        assert recording.identification["lesson"] == "file name"

        job = recording.job
        assert job.id == job_id
        assert (job.stage, job.status, job.attempts) == (Stage.CONSENT, JobStatus.ON_HOLD, 1)
        assert job.flags == ["llm_fallback"]
        assert job.version == 1
        assert job.created_at.tzinfo is UTC

        (run,) = job.stage_runs
        assert run.status is StageRunStatus.SUCCEEDED
        assert run.duration_seconds == 95 * 60
        assert run.metrics["compute_seconds"] == 5700.2

        (event,) = job.events
        assert event.actor_kind is ActorKind.WORKER
        assert event.message == "Ուսանողներից մեկը համաձայն չէ"

        assert job.latest_cut_list.segments == SEGMENTS
        assert job.latest_cut_list.final_duration_seconds == 8747.5

        raw = job.transcript(TranscriptKind.RAW)
        clean = job.transcript(TranscriptKind.CLEAN)
        assert raw.text.startswith("Բարև ձեզ")
        assert clean.cut_list.id == job.latest_cut_list.id

        assert job.video_metadata.chapters[1] == {"start": 95.0, "title": "dict"}
        assert job.video_metadata.summary == "Բառարաններ, live coding"

        (check,) = job.consent_checks
        assert check.result is ConsentResult.OPTED_OUT
        assert check.students_checked == 2
        assert check.released_by == "anna"

        youtube = job.publication(TargetKind.YOUTUBE)
        assert youtube.status is PublicationStatus.PUBLISHED
        slack = job.publication(TargetKind.SLACK)
        assert slack.status is PublicationStatus.PENDING and slack.dry_run
        assert job.publication(TargetKind.CLASSROOM) is None

        assert job.library_entry.lesson_number == 14

        seen = s.scalars(select(SeenFile)).one()
        assert seen.recording_id == recording_id and seen.state is SeenFileState.QUEUED

        user = s.scalars(select(StaffUser)).one()
        assert user.username == "anna" and user.is_active
        assert "scrypt" not in repr(user)


def test_publication_summary_gives_the_prd_publication_fields(db):
    with db.transaction() as s:
        job = add_job(s)
        job.publications = [
            PublicationTarget(
                target=TargetKind.YOUTUBE,
                external_id="vid1",
                url="https://youtu.be/vid1",
                details={"playlist_id": "PL9"},
            ),
            PublicationTarget(target=TargetKind.SLACK, url="https://x.slack.com/archives/C1/p1"),
            PublicationTarget(target=TargetKind.CLASSROOM, external_id="777"),
            PublicationTarget(target=TargetKind.LIBRARY, external_id="lib-5"),
        ]
        s.flush()
        assert job.publication_summary() == {
            "youtube_video_id": "vid1",
            "youtube_url": "https://youtu.be/vid1",
            "playlist_id": "PL9",
            "slack_message_link": "https://x.slack.com/archives/C1/p1",
            "classroom_post_id": "777",
            "library_entry_id": "lib-5",
        }


def test_content_hash_is_unique(db):
    with pytest.raises(IntegrityError), db.transaction() as s:
        add_recording(s, HASH_A)
        add_recording(s, HASH_A)


def test_content_hash_is_normalised_so_case_cannot_bypass_uniqueness(db):
    with db.transaction() as s:
        assert add_recording(s, HASH_A.upper()).content_hash == HASH_A
    with pytest.raises(IntegrityError), db.transaction() as s:
        add_recording(s, " " + HASH_A + " ")


def test_content_hash_may_be_empty_until_the_import_stage(db):
    with db.transaction() as s:
        add_recording(s, None)
        add_recording(s, None)
    with db.transaction() as s:
        assert len(s.scalars(select(Recording)).all()) == 2


@pytest.mark.parametrize("bad", ["abc", "g" * 64, "a" * 63])
def test_invalid_content_hash_is_rejected(db, bad):
    with db.transaction() as s, pytest.raises(ValueError, match="SHA-256"):
        add_recording(s, bad)


def test_one_job_per_recording(db):
    with pytest.raises(IntegrityError), db.transaction() as s:
        job = add_job(s)
        s.add(Job(recording_id=job.recording_id))
        s.flush()


def test_foreign_keys_are_enforced(db):
    with pytest.raises(IntegrityError), db.transaction() as s:
        s.add(Job(recording_id=999))
        s.flush()


def test_unique_rows_per_job(db):
    with db.transaction() as s:
        job_id = add_job(s).id

    def add_twice(make):
        with pytest.raises(IntegrityError), db.transaction() as s:
            s.add_all([make(), make()])
            s.flush()

    add_twice(lambda: PublicationTarget(job_id=job_id, target=TargetKind.SLACK))
    add_twice(lambda: Transcript(job_id=job_id, kind="raw", language="hy", text="x"))
    add_twice(
        lambda: CutList(
            job_id=job_id,
            version=1,
            original_duration_seconds=10,
            final_duration_seconds=5,
            segments=[],
        )
    )
    add_twice(lambda: VideoMetadata(job_id=job_id, title="t"))
    add_twice(lambda: LibraryEntry(job_id=job_id, title="t", video_url="u", group_name="g"))


def test_unique_watcher_path_and_username(db):
    with pytest.raises(IntegrityError), db.transaction() as s:
        for _ in range(2):
            s.add(SeenFile(path=r"C:\inbox\a.mp4", size_bytes=1, modified_at=T0))
        s.flush()
    with pytest.raises(IntegrityError), db.transaction() as s:
        s.add_all(
            [
                StaffUser(username="Anna", password_hash="h"),
                StaffUser(username=" anna ", password_hash="h"),
            ]
        )
        s.flush()


def test_invalid_username_is_rejected(db):
    with pytest.raises(ValueError, match="username"):
        StaffUser(username="a b", password_hash="h")


def test_check_constraints(db):
    with pytest.raises(IntegrityError), db.transaction() as s:
        add_recording(s, size_bytes=-1)
    with pytest.raises(IntegrityError), db.transaction() as s:
        add_job(s).attempts = -1
        s.flush()


def test_deleting_a_recording_deletes_its_job_and_everything_of_the_job(db):
    with db.transaction() as s:
        job = add_job(s)
        recording_id = job.recording_id
        s.add(SeenFile(path="x.mp4", size_bytes=1, modified_at=T0, recording_id=recording_id))
        job.stage_runs.append(StageRun(stage=Stage.IMPORT, attempt=1))
        job.events.append(JobEvent(kind="created"))
        job.consent_checks.append(ConsentCheck(provider="file", result=ConsentResult.PASSED))
        job.publications.append(PublicationTarget(target=TargetKind.YOUTUBE))
        job.video_metadata = VideoMetadata(title="t")
        job.library_entry = LibraryEntry(title="t", video_url="u", group_name="g")
        job.cut_lists.append(
            CutList(original_duration_seconds=1, final_duration_seconds=1, segments=[])
        )
        job.transcripts.append(Transcript(kind=TranscriptKind.RAW, language="hy", text="x"))

    # Deleted by the database itself (ON DELETE CASCADE), not only by the ORM.
    with db.transaction() as s:
        s.execute(delete(Recording).where(Recording.id == recording_id))

    with db.transaction() as s:
        for table in sorted(EXPECTED_TABLES - {"seen_files", "schema_versions", "app_meta"}):
            assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
        assert s.scalars(select(SeenFile)).one().recording_id is None


def test_orm_delete_of_a_job_removes_its_rows(db):
    with db.transaction() as s:
        job = add_job(s)
        job.events.append(JobEvent(kind="created"))
        job_id = job.id
    with db.transaction() as s:
        s.delete(s.get(Job, job_id))
    with db.transaction() as s:
        assert s.scalars(select(JobEvent)).all() == []
        assert s.scalars(select(Recording)).one() is not None


def test_ids_are_never_reused(db):
    with db.transaction() as s:
        first = add_job(s)
        ids = (first.recording_id, first.id)
    with db.transaction() as s:
        s.execute(delete(Recording))
    with db.transaction() as s:
        second = add_job(s)
        assert second.recording_id > ids[0]
        assert second.id > ids[1]


def test_naive_datetimes_are_rejected(db):
    with pytest.raises(StatementError, match="naive datetime"), db.transaction() as s:
        add_recording(s, detected_at=datetime(2026, 11, 18, 20, 0))


def test_datetimes_are_stored_in_utc_and_compare_correctly(db):
    local = datetime(2026, 11, 18, 23, 30, tzinfo=YEREVAN)  # 19:30 UTC
    with db.transaction() as s:
        add_recording(s, detected_at=local)
    with db.transaction() as s:
        stored = s.execute(text("SELECT detected_at FROM recordings")).scalar()
        assert stored.startswith("2026-11-18 19:30:00")
        recording = s.scalars(select(Recording)).one()
        assert recording.detected_at == local
        assert recording.detected_at.tzinfo is UTC
        later = datetime(2026, 11, 18, 19, 31, tzinfo=UTC)
        earlier = datetime(2026, 11, 18, 23, 29, tzinfo=YEREVAN)
        assert s.scalars(select(Recording).where(Recording.detected_at < later)).all()
        assert not s.scalars(select(Recording).where(Recording.detected_at < earlier)).all()


def test_enums_are_stored_as_values_and_unknown_values_rejected(db):
    with db.transaction() as s:
        job = add_job(s)
        job.status = "on_hold"  # plain strings are accepted when valid
    with db.transaction() as s:
        assert s.execute(text("SELECT stage, status FROM jobs")).one() == ("import", "on_hold")
        assert s.scalars(select(Job)).one().status is JobStatus.ON_HOLD
    with pytest.raises(StatementError), db.transaction() as s:
        s.scalars(select(Job)).one().status = "paused"
        s.flush()


def test_json_keeps_armenian_text_readable(db):
    with db.transaction() as s:
        job = add_job(s)
        job.events.append(JobEvent(kind="note", data={"text": "Դաս"}))
    with db.transaction() as s:
        assert s.execute(text("SELECT data FROM job_events")).scalar() == '{"text":"Դաս"}'


def test_json_rejects_nan(db):
    with pytest.raises(StatementError), db.transaction() as s:
        add_job(s).events.append(JobEvent(kind="x", data={"value": float("nan")}))
        s.flush()


def test_job_flags_are_saved_when_added(db):
    with db.transaction() as s:
        job = add_job(s)
    with db.transaction() as s:
        job = s.get(Job, job.id)
        job.add_flag("boundaries_not_found")
        job.add_flag("boundaries_not_found")
    with db.transaction() as s:
        assert s.get(Job, job.id).flags == ["boundaries_not_found"]


def test_concurrent_job_update_is_detected(db):
    """Staff saw the job earlier; a worker changed it since; the stale save must fail."""
    with db.transaction() as s:
        job_id = add_job(s).id
    first, second = db.session(), db.session()
    try:
        job_b = second.get(Job, job_id)
        second.commit()  # its read is over; job_b keeps the values it saw (version 1)
        job_a = first.get(Job, job_id)
        job_a.status = JobStatus.CANCELLED
        first.commit()
        job_b.status = JobStatus.RUNNING
        with pytest.raises(StaleDataError):
            second.commit()
    finally:
        first.close()
        second.close()
    with db.transaction() as s:
        job = s.get(Job, job_id)
        assert (job.status, job.version) == (JobStatus.CANCELLED, 2)


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        ("not a list", "must be a list"),
        ([{"start": 0, "end": 5, "action": "keep"}], "reason"),
        ([{"start": 0, "end": 5, "action": "cut", "reason": "x"}], "action"),
        ([{"start": 5, "end": 5, "action": "keep", "reason": "x"}], "start < end"),
        ([{"start": -1, "end": 5, "action": "keep", "reason": "x"}], "start < end"),
        ([{"start": "0", "end": 5, "action": "keep", "reason": "x"}], "numbers"),
        ([{"start": 0, "end": float("inf"), "action": "keep", "reason": "x"}], "numbers"),
        (
            [
                {"start": 0, "end": 10, "action": "keep", "reason": "x"},
                {"start": 9, "end": 20, "action": "remove", "reason": "y"},
            ],
            "overlaps",
        ),
    ],
)
def test_invalid_cut_lists_are_rejected(segments, message):
    with pytest.raises(ValueError, match=message):
        validate_segments(segments)
    with pytest.raises(ValueError, match=message):
        CutList(original_duration_seconds=20, final_duration_seconds=10, segments=segments)


def test_valid_cut_list_is_accepted():
    assert validate_segments(SEGMENTS) is SEGMENTS
    assert validate_segments([]) == []


def test_stage_order_and_names():
    assert [stage.value for stage in PIPELINE] == [
        "import",
        "transcribe",
        "clean",
        "chapters",
        "consent",
        "upload",
        "post",
    ]
    assert [stage.label for stage in PIPELINE] == [
        "Imported",
        "Transcribed",
        "Cleaned",
        "Chapters",
        "Consent",
        "Uploaded",
        "Posting links",
    ]
    assert JobStatus.ON_HOLD.label == "On hold"


def test_bookkeeping_tables(db):
    with db.transaction() as s:
        (version,) = s.scalars(select(SchemaVersion)).all()
        assert version.version == 1 and version.description == "initial schema"
        meta = {row.key: row.value for row in s.scalars(select(AppMeta))}
        assert set(meta) == {"instance_id", "created_at"}
        assert len(meta["instance_id"]) == 32
