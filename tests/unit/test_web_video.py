"""The cleaned video on the recording page (D-057): a player, Range requests, a download."""

from types import SimpleNamespace

from test_web_dashboard import add_job
from test_web_detail import site  # noqa: F401  (the signed-in dashboard fixture)

from reel.db import JobStatus, PublicationTarget, Stage, VideoMetadata
from reel.db.enums import PublicationStatus, TargetKind
from reel.web.detail import CLEANED_VIDEO, video_file_name

DATA = bytes(range(256)) * 40
ONLY_HERE = "միայն այս համակարգչում է"


def with_video(where, job_id: int) -> None:
    folder = where.cfg.paths.data_dir / "jobs" / str(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CLEANED_VIDEO).write_bytes(DATA)


def test_the_cleaned_video_plays_on_the_recording_page(site):  # noqa: F811
    job_id = add_job(site.db, group="Python 12", lesson=4, status=JobStatus.DONE, stage=Stage.POST)
    assert "<video" not in site.client.get(f"/recordings/{job_id}").text
    assert site.client.get(f"/recordings/{job_id}/video").status_code == 404

    with_video(site, job_id)
    page = site.client.get(f"/recordings/{job_id}").text
    assert (
        f'<video class="player" controls preload="metadata" src="/recordings/{job_id}/video"'
        in page
    )
    assert f'href="/recordings/{job_id}/video?download=1" download>' in page
    assert ONLY_HERE in page  # no live YouTube upload

    whole = site.client.get(f"/recordings/{job_id}/video")
    assert whole.status_code == 200 and whole.content == DATA
    assert whole.headers["content-type"] == "video/mp4"
    assert whole.headers["accept-ranges"] == "bytes"
    assert "content-disposition" not in whole.headers  # plays in the page
    part = site.client.get(f"/recordings/{job_id}/video", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == DATA[100:200]  # seeking in the player
    assert part.headers["content-range"] == f"bytes 100-199/{len(DATA)}"


def test_the_download_is_named_after_the_title(site):  # noqa: F811
    job_id = add_job(site.db, group="Python 12", lesson=4, status=JobStatus.DONE, stage=Stage.POST)
    with site.db.transaction() as s:
        s.add(VideoMetadata(job_id=job_id, title="Python 12 · Lecture 4"))
    with_video(site, job_id)
    down = site.client.get(f"/recordings/{job_id}/video?download=1")
    assert down.status_code == 200 and down.content == DATA
    disposition = down.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "Lecture%204.mp4" in disposition


def test_a_video_on_youtube_has_no_only_here_note(site):  # noqa: F811
    job_id = add_job(site.db, group="Python 12", lesson=5, status=JobStatus.DONE, stage=Stage.POST)
    with site.db.transaction() as s:
        s.add(PublicationTarget(
            job_id=job_id, target=TargetKind.YOUTUBE, status=PublicationStatus.PUBLISHED,
            dry_run=False, attempts=1, details={}, external_id="abc123",
            url="https://www.youtube.com/watch?v=abc123",
        ))  # fmt: skip
    with_video(site, job_id)
    page = site.client.get(f"/recordings/{job_id}").text
    assert "<video" in page and ONLY_HERE not in page


def test_the_video_needs_login(site):  # noqa: F811
    job_id = add_job(site.db, group="Python 12", lesson=6, status=JobStatus.DONE, stage=Stage.POST)
    with_video(site, job_id)
    site.client.cookies.clear()
    assert site.client.get(f"/recordings/{job_id}/video").status_code == 303
    assert site.client.get("/recordings/999/video").status_code == 303


def test_file_names_are_safe_on_windows():
    def job(title):
        return SimpleNamespace(id=7, video_metadata=SimpleNamespace(title=title) if title else None)

    assert video_file_name(job("Python 12 · Lecture 4")) == "Python 12 · Lecture 4.mp4"
    assert video_file_name(job('a/b: "c"?')) == "a b c.mp4"
    assert video_file_name(job("...")) == "reel-7.mp4"
    assert video_file_name(job(None)) == "reel-7.mp4"
