"""Stage ``consent`` (T-602; FR-20 … FR-22, UJ-3, C-3, D-019, D-046): nothing is published
for a lesson unless every student of the group consented.

- Every check is stored as a ``consent_checks`` row (time, provider, group, the students' ids
  and statuses only, result, message) — the audit trail.
- Passed → the job continues to upload.
- A student opted out, or a status is unknown (counts as opted out, D-019) → On hold
  (``consent_opted_out`` / ``consent_unknown``) and a staff notification. Staff decide:
  release "accept" (``advance=True``: continues to upload; their name, time and note are written
  on the check row), release "check again" (the stage runs again), or cancel (nothing is
  published).
- The consent source cannot be reached → retried like any other failure, and on the last
  attempt On hold (``consent_unreachable``) instead of failing, so staff see it.
"""

from collections.abc import Callable

from reel.config.groups import missing_group_message
from reel.config.loader import LoadedConfig
from reel.consent import ConsentProvider, make_consent_provider
from reel.db import ConsentCheck, Job, Stage
from reel.db.enums import ConsentResult
from reel.queue.stage import (
    Hold,
    StageContext,
    StageFailed,
    StageHandler,
    StageResult,
    only_processed,
    skipped_unpublished,
)

HOLD_PREFIX = "consent_"
MESSAGES = {
    ConsentResult.OPTED_OUT: "A student of this group did not consent to publishing. "
    "Check the video: release it to publish anyway, or cancel the job.",
    ConsentResult.UNKNOWN: "The consent of some students is unknown (treated as opted out). "
    "Check the video: release it to publish anyway, or cancel the job.",
    ConsentResult.UNREACHABLE: "The consent source could not be reached. "
    "Release the job to check again when it works.",
}


def hold_reason(result: ConsentResult) -> str:
    return f"{HOLD_PREFIX}{result.value}"


class ConsentStage(StageHandler):
    name = Stage.CONSENT

    def __init__(
        self,
        provider_factory: Callable[[LoadedConfig], ConsentProvider] = make_consent_provider,
    ) -> None:
        self.provider_factory = provider_factory

    def run(self, ctx: StageContext) -> StageResult:
        cfg = ctx.config
        if only_processed(ctx):  # nothing is published, so there is nothing to consent to
            return skipped_unpublished()
        with ctx.db.transaction() as session:
            recording = session.get(Job, ctx.job_id).recording
            group_name, lesson_date = recording.group_name, recording.recording_date
        group = cfg.groups.groups.get(group_name) if group_name else None
        if group is None:  # the chapters stage holds such jobs; this is a safety net
            raise Hold("identification", missing_group_message(group_name))
        provider = self.provider_factory(cfg)
        reference = group.core_engine_group_id or group_name
        decision = provider.check(reference, lesson_date)
        ctx.check()
        students = decision.students_json()
        with ctx.db.transaction(immediate=True) as session:
            session.add(
                ConsentCheck(
                    job_id=ctx.job_id,
                    provider=provider.name,
                    group_ref=decision.group_ref or reference,
                    students=students,
                    result=decision.result,
                    message=decision.message,
                )
            )
        counts = {
            status: sum(1 for s in students if s["status"] == status)
            for status in ("granted", "denied", "unknown")
        }
        metrics = {"result": decision.result.value, "students": len(students), **counts}
        if decision.result is ConsentResult.PASSED:
            return StageResult(metrics=metrics, message=f"all {len(students)} students consented")
        detail = f"{MESSAGES[decision.result]} ({decision.message})".strip()
        if decision.result is ConsentResult.UNREACHABLE:
            last = ctx.attempt >= cfg.settings.workers.max_attempts
            if not last:
                raise StageFailed(f"consent source unreachable: {decision.message}")
        raise Hold(hold_reason(decision.result), detail)
