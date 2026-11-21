"""A user's upload that is only processed: nothing is checked for consent, uploaded or posted
(T-1013, D-065)."""

from sqlalchemy import func, select

from reel.config import load_config
from reel.db import (
    ConsentCheck,
    Job,
    JobStatus,
    PublicationTarget,
    Recording,
    Stage,
    StageRun,
    init_database,
    open_database,
)
from reel.queue import StageRegistry, Worker, create_job
from reel.queue.stage import NOT_PUBLISHED
from reel.stages.consent import ConsentStage
from reel.stages.publishing import PostStage, UploadStage

CONFIG = """
[workers]
max_attempts = 1
idle_poll_seconds = 0.02
[youtube]
mode = "live"
"""


def test_consent_upload_and_post_do_nothing_for_a_video_that_is_only_processed(
    project, write_config
):
    write_config(CONFIG)
    cfg = load_config(project / "config" / "reel.toml")
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    try:
        with db.transaction() as s:
            # a group that is in no configuration, and YouTube is live: still nothing happens
            recording = Recording(source_path="mine.mp4", size_bytes=1, group_name="Իմ խումբ",
                                  lesson_number=1, publish=False)  # fmt: skip
            s.add(recording)
            job_id = create_job(s, recording, stage=Stage.CONSENT).id
        registry = StageRegistry([ConsentStage(), UploadStage(), PostStage()])
        attention: list = []
        worker = Worker(db, registry, lambda: cfg, notifier=attention.append)
        worker.start(until_idle=True)
        assert worker.join(60)
        with db.transaction() as s:
            job = s.get(Job, job_id)
            assert (job.status, job.stage) == (JobStatus.DONE, Stage.POST)
            runs = s.scalars(select(StageRun).where(StageRun.job_id == job_id)).all()
            assert [(run.stage, run.metrics.get("skipped")) for run in runs] == [
                (Stage.CONSENT, "not published"),
                (Stage.UPLOAD, "not published"),
                (Stage.POST, "not published"),
            ]
            for table in (ConsentCheck, PublicationTarget):
                assert s.scalar(select(func.count()).select_from(table)) == 0
        assert attention == []
        assert NOT_PUBLISHED.startswith("only processed")
    finally:
        db.dispose()
