"""Pipeline stage implementations. They are added task by task: import (T-204),
transcribe (T-302), clean (T-406), chapters (T-504), consent (T-602), upload and post
(T-706)."""

from reel.queue.stage import StageRegistry
from reel.stages.chapters import ChaptersStage
from reel.stages.cleaning import CleanStage
from reel.stages.consent import ConsentStage
from reel.stages.importing import ImportStage
from reel.stages.publishing import PostStage, UploadStage
from reel.stages.transcribing import TranscribeStage


def default_registry() -> StageRegistry:
    """All pipeline stages."""
    registry = StageRegistry()
    registry.register(ImportStage())
    registry.register(TranscribeStage())
    registry.register(CleanStage())
    registry.register(ChaptersStage())
    registry.register(ConsentStage())
    registry.register(UploadStage())
    registry.register(PostStage())
    return registry
