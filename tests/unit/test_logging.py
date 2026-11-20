"""Structured logging: JSON fields, job context, per-job files, redaction, rotation (T-004)."""

import io
import json
import logging
import logging.handlers
import re
import threading
from pathlib import Path

from reel.cli import main
from reel.config import load_config
from reel.config.schema import LoggingSettings
from reel.logging_setup import (
    MASK,
    configure_from_config,
    configure_logging,
    current_context,
    log_context,
    register_secret,
)

log = logging.getLogger("reel.test")


def json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- JSON lines --------------------------------------------------------------------------


def test_json_line_has_the_documented_fields(tmp_path):
    configure_logging(logs_dir=tmp_path, role="test", console=False)
    with log_context(job_id=12, stage="transcribe"):
        log.info("Transcribed %d segments", 42, extra={"audio_seconds": 1.5})
    record = json_lines(tmp_path / "reel-test.jsonl")[-1]
    assert list(record) == [
        "time",
        "level",
        "component",
        "job_id",
        "stage",
        "message",
        "audio_seconds",
    ]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", record["time"])
    assert record["level"] == "INFO"
    assert record["component"] == "reel.test"
    assert record["job_id"] == 12
    assert record["stage"] == "transcribe"
    assert record["message"] == "Transcribed 42 segments"
    assert record["audio_seconds"] == 1.5


def test_exception_is_recorded_with_its_traceback(tmp_path):
    configure_logging(logs_dir=tmp_path, role="test", console=False)
    try:
        raise ValueError("bad input")
    except ValueError:
        log.exception("Stage failed")
    record = json_lines(tmp_path / "reel-test.jsonl")[-1]
    assert record["level"] == "ERROR"
    assert "Traceback" in record["exception"]
    assert "ValueError: bad input" in record["exception"]


def test_armenian_text_is_written_as_is(tmp_path):
    configure_logging(logs_dir=tmp_path, role="test", console=False)
    log.info("Դաս 14 · Բառարաններ")
    assert "Դաս 14 · Բառարաններ" in (tmp_path / "reel-test.jsonl").read_text(encoding="utf-8")


def test_extra_named_like_a_core_field_does_not_overwrite_it(tmp_path):
    configure_logging(logs_dir=tmp_path, role="test", console=False)
    log.info("real message", extra={"time": "not a timestamp"})
    record = json_lines(tmp_path / "reel-test.jsonl")[-1]
    assert record["message"] == "real message"
    assert record["extra_time"] == "not a timestamp"
    assert record["time"].endswith("Z")


# --- job context ---------------------------------------------------------------------------


def test_context_nests_and_is_restored():
    assert current_context() == {}
    with log_context(job_id=1, stage="import"):
        with log_context(stage="transcribe", attempt=2):
            assert current_context() == {"job_id": 1, "stage": "transcribe", "attempt": 2}
        assert current_context() == {"job_id": 1, "stage": "import"}
    assert current_context() == {}


def test_context_does_not_leak_into_other_threads(tmp_path):
    configure_logging(logs_dir=tmp_path, role="test", console=False)
    with log_context(job_id=5):
        thread = threading.Thread(target=lambda: log.info("from another thread"))
        thread.start()
        thread.join()
        log.info("from the job thread")
    records = {r["message"]: r for r in json_lines(tmp_path / "reel-test.jsonl")}
    assert "job_id" not in records["from another thread"]
    assert records["from the job thread"]["job_id"] == 5


def test_explicit_extra_wins_over_the_context(tmp_path):
    configure_logging(logs_dir=tmp_path, role="test", console=False)
    with log_context(stage="clean"):
        log.info("override", extra={"stage": "render"})
    assert json_lines(tmp_path / "reel-test.jsonl")[-1]["stage"] == "render"


# --- console and per-job files ---------------------------------------------------------------


def test_console_lines_are_short_and_readable():
    stream = io.StringIO()
    configure_logging(stream=stream)
    with log_context(job_id=3, stage="clean"):
        log.warning("Silence removed", extra={"seconds": 12.5})
    log.debug("not shown at INFO")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert re.fullmatch(
        r"\d\d:\d\d:\d\d WARNING \[job 3 \| clean\] reel\.test: Silence removed \(seconds=12\.5\)",
        lines[0],
    )


def test_job_log_files_contain_only_their_own_job(tmp_path):
    jobs = tmp_path / "jobs"
    configure_logging(jobs_dir=jobs, console=False)
    with log_context(job_id=7, stage="import"):
        log.info("seven")
    log.info("no job")
    with log_context(job_id=8):
        log.info("eight")
    seven = (jobs / "7" / "job.log").read_text(encoding="utf-8")
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO    \[import\] reel\.test: seven\n", seven
    )
    assert "eight" in (jobs / "8" / "job.log").read_text(encoding="utf-8")
    assert sorted(p.name for p in jobs.iterdir()) == ["7", "8"]


def test_unsafe_job_id_cannot_escape_the_jobs_folder(tmp_path):
    jobs = tmp_path / "jobs"
    configure_logging(jobs_dir=jobs, console=False)
    with log_context(job_id="../outside"):
        log.info("ignored by the job log")
    assert not (tmp_path / "outside").exists()
    assert not jobs.exists()


def test_job_logs_can_be_switched_off(tmp_path):
    configure_logging(LoggingSettings(job_logs=False), jobs_dir=tmp_path / "jobs", console=False)
    with log_context(job_id=1):
        log.info("x")
    assert not (tmp_path / "jobs").exists()


# --- redaction -----------------------------------------------------------------------------

SECRET = "super-secret-value-123"
LEAKS = (
    SECRET,
    "abcd1234efgh",
    "hunter2!",
    "zzzzzzzzzz",
    "xoxb-1234567890-abcdefghij",
    "abc.def.ghi12345",
    "late-secret-999",
)


def test_secrets_are_masked_in_every_output(tmp_path):
    stream = io.StringIO()
    configure_logging(
        logs_dir=tmp_path,
        jobs_dir=tmp_path / "jobs",
        role="test",
        stream=stream,
        secret_values=[SECRET],
    )
    register_secret("late-secret-999")
    with log_context(job_id=1, stage="upload"):
        log.error(
            "Upload failed with key %s",
            SECRET,
            extra={
                "url": "https://api.example/x?access_token=abcd1234efgh",
                "password": "hunter2!",
            },
        )
        try:
            raise RuntimeError("request failed: token=zzzzzzzzzz")
        except RuntimeError:
            log.exception("Slack post failed")
        log.info("Slack xoxb-1234567890-abcdefghij, Bearer abc.def.ghi12345, late-secret-999")
    outputs = {
        "console": stream.getvalue(),
        "json": (tmp_path / "reel-test.jsonl").read_text(encoding="utf-8"),
        "job": (tmp_path / "jobs" / "1" / "job.log").read_text(encoding="utf-8"),
    }
    for name, text in outputs.items():
        for leak in LEAKS:
            assert leak not in text, f"{leak!r} leaked into the {name} output"
        assert MASK in text


def test_short_values_and_token_counts_are_not_masked(tmp_path):
    configure_logging(logs_dir=tmp_path, role="test", console=False, secret_values=["abc"])
    log.info("abc done", extra={"prompt_tokens": 1234, "max_tokens": 8192})
    record = json_lines(tmp_path / "reel-test.jsonl")[-1]
    assert record["message"] == "abc done"
    assert (record["prompt_tokens"], record["max_tokens"]) == (1234, 8192)


# --- rotation and setup --------------------------------------------------------------------


def test_json_log_rotates_at_the_configured_size(tmp_path):
    settings = LoggingSettings(file_max_mb=0.1, file_backups=2)
    setup = configure_logging(settings, logs_dir=tmp_path, role="test", console=False)
    handler = setup.json_handler
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == int(0.1 * 1024 * 1024)
    assert handler.backupCount == 2
    for i in range(3000):
        log.info("line %d %s", i, "x" * 100)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["reel-test.jsonl", "reel-test.jsonl.1", "reel-test.jsonl.2"]
    assert (tmp_path / "reel-test.jsonl").stat().st_size <= handler.maxBytes


def test_configuring_twice_does_not_duplicate_output():
    stream = io.StringIO()
    configure_logging(stream=stream)
    configure_logging(stream=stream)
    log.warning("once")
    assert stream.getvalue().count("once") == 1


def test_noisy_libraries_are_quieted():
    configure_logging(console=False)
    assert logging.getLogger("urllib3").level == logging.WARNING


def test_level_names_are_case_insensitive():
    assert LoggingSettings(level="debug", console_level="Warning").level == "DEBUG"


def test_configure_from_config_uses_project_folders_and_masks_env_secrets(project):
    (project / ".env").write_text(
        "SLACK_BOT_TOKEN=xoxb-from-dotenv-0001\nREEL_API_TOKENS=firsttoken111,secondtoken222\n",
        encoding="utf-8",
    )
    cfg = load_config(project / "config" / "reel.toml", environ={})
    setup = configure_from_config(cfg, role="test", console=False)
    assert setup.json_file == cfg.paths.logs_dir / "reel-test.jsonl"
    with log_context(job_id=4):
        log.info("slack xoxb-from-dotenv-0001, api firsttoken111 and secondtoken222")
    for path in (setup.json_file, cfg.paths.data_dir / "jobs" / "4" / "job.log"):
        text = path.read_text(encoding="utf-8")
        for leak in ("xoxb-from-dotenv-0001", "firsttoken111", "secondtoken222"):
            assert leak not in text


def test_cli_verbose_flag_enables_debug_messages(capsys):
    assert main(["--verbose"]) == 0
    logging.getLogger("reel.test").debug("visible with -v")
    assert "visible with -v" in capsys.readouterr().err


def test_cli_hides_debug_messages_by_default(capsys):
    assert main([]) == 0
    logging.getLogger("reel.test").info("hidden without -v")
    assert "hidden without -v" not in capsys.readouterr().err
