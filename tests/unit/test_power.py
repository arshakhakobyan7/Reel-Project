"""The PC's sleep and Reel's work (D-056)."""

import sys
import time
from datetime import datetime, timedelta

import pytest

from reel import power
from reel.db import StageRun
from reel.metrics import _run_seconds

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_the_awake_clock_moves_with_the_monotonic_clock_while_the_pc_is_awake():
    awake, steady = power.awake_clock(), time.monotonic()
    time.sleep(0.2)
    awake_gone, steady_gone = power.awake_clock() - awake, time.monotonic() - steady
    assert awake_gone > 0.1
    assert abs(awake_gone - steady_gone) < 0.1  # 15.6 ms resolution of the interrupt time


@windows
def test_the_awake_clock_on_windows_leaves_out_sleep():
    assert power.awake_clock is not time.monotonic  # QueryUnbiasedInterruptTime


def test_keep_awake_sets_the_request_and_clears_it(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(power, "_set_execution_state", lambda flags: calls.append(flags) or True)
    with power.keep_awake() as held:
        assert held
        assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]
    assert calls[-1] == power.ES_CONTINUOUS


def test_keep_awake_clears_the_request_when_the_block_fails(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(power, "_set_execution_state", lambda flags: calls.append(flags) or True)
    with pytest.raises(RuntimeError), power.keep_awake():
        raise RuntimeError("stage failed")
    assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]


def test_keep_awake_can_be_turned_off(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(power, "_set_execution_state", lambda flags: calls.append(flags) or True)
    with power.keep_awake(enabled=False) as held:
        assert not held
    assert calls == []


def test_a_refused_request_is_not_cleared(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(power, "_set_execution_state", lambda flags: calls.append(flags) and False)
    with power.keep_awake() as held:
        assert not held
    assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]


@windows
def test_windows_accepts_the_request():
    with power.keep_awake() as held:
        assert held


def test_sleep_during_a_stage_run_is_not_processing_time():
    start = datetime(2026, 11, 20, 3, 23)
    run = StageRun(
        started_at=start,
        finished_at=start + timedelta(hours=10, minutes=40),
        metrics={"suspended_seconds": 10.5 * 3600},
    )
    assert _run_seconds(run) == pytest.approx(10 * 60)
    run.metrics = {}
    assert _run_seconds(run) == pytest.approx(640 * 60)
