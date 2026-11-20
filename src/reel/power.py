"""The PC's sleep and Reel's work (T-1002 finding, D-056).

A laptop that sleeps in the middle of a stage stops all work until it wakes. Two things
follow from that:

- :func:`awake_clock` counts only the time the PC was awake. The worker measures stage
  timeouts and processing time with it, so ten hours of sleep are not taken for a stage
  that hangs, and the time budget is not exceeded by the night.
- :func:`keep_awake` asks Windows not to put the PC to sleep for being idle while a stage
  runs. Windows still sleeps when the battery is low, the lid is closed or the power
  button is pressed: that cannot and should not be prevented, so the PC must stay plugged
  in.

On other systems ``time.monotonic`` already stops during sleep, and ``keep_awake`` does
nothing.
"""

import ctypes
import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _windows_awake_clock() -> Callable[[], float] | None:
    try:
        query = ctypes.WinDLL("kernel32").QueryUnbiasedInterruptTime
    except AttributeError, OSError:
        return None
    query.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    query.restype = ctypes.c_int

    def clock() -> float:
        value = ctypes.c_ulonglong(0)
        if not query(ctypes.byref(value)):
            return time.monotonic()
        return value.value / 10_000_000  # 100-nanosecond units, without sleep and hibernation

    return clock


awake_clock: Callable[[], float] = (
    (_windows_awake_clock() or time.monotonic) if sys.platform == "win32" else time.monotonic
)
"""Seconds of awake time from an arbitrary start. Only differences are meaningful."""


def _set_execution_state(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        function = ctypes.WinDLL("kernel32").SetThreadExecutionState
    except AttributeError, OSError:
        return False
    function.argtypes = [ctypes.c_uint32]
    function.restype = ctypes.c_uint32
    return function(flags) != 0


@contextmanager
def keep_awake(enabled: bool = True) -> Iterator[bool]:
    """Keep the PC from sleeping for being idle while the block runs, in this thread.

    Yields whether Windows accepted the request. The request belongs to the calling thread
    and ends when the block ends (or the thread ends).
    """
    held = enabled and _set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    if enabled and not held and sys.platform == "win32":
        log.warning("Windows did not accept the request to stay awake during the work")
    try:
        yield held
    finally:
        if held:
            _set_execution_state(ES_CONTINUOUS)
