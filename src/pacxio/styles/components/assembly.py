"""Join replacement boundaries without boolean operations or arc approximation."""

from collections.abc import Iterable

from pacxio.geometry import Path, Segment
from pacxio.messages import t


def stitch(*runs: Iterable[Segment]) -> Path:
    segments = [segment for run in runs for segment in run]
    if not segments:
        raise ValueError(t("The outline cannot be empty"))
    for before, after in zip(segments, [*segments[1:], segments[0]], strict=True):
        if not before.end.is_close(after.start):
            raise ValueError(
                t(
                    "The component outline is disconnected: {end} → {start}",
                    end=before.end,
                    start=after.start,
                )
            )
    return Path(segments, closed=True)
