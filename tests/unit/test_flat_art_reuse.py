"""Identical flat artwork may serve disjoint faces, without weakening duplicate guards."""

from pathlib import Path

import pytest
from PIL import Image

from pacxio import api, dielines
from pacxio.concept.service import check_flat, import_concept, read_meta
from pacxio.model.job import Job


def test_identical_flat_can_serve_other_faces_but_cannot_repeat_either(tmp_path: Path) -> None:
    job = tmp_path / "tea"
    assert api.init_job(job, "rte", {"L": 80, "W": 50, "H": 130}, "sbs_350").ok
    image = tmp_path / "solid.png"
    Image.new("RGB", (800, 500), "#b3c2a8").save(image)
    assert import_concept(job, image, name="top", view="flat", faces=["top_lid"]).ok
    reused = import_concept(job, image, name="bottom", view="flat", faces=["btm_lid"])
    assert reused.ok and reused.data["reused_flat_from"] == ["top"]
    for face in ("top_lid", "btm_lid"):
        repeat = import_concept(job, image, name="again-" + face, view="flat", faces=[face])
        assert not repeat.ok and repeat.errors[0].id == "DUPLICATE"
    # Geometry checking still rejects reusing the image on an incompatible face.
    mismatch = import_concept(job, image, name="front", view="flat", faces=["front"])
    assert mismatch.ok and mismatch.data["redo"]
    dl, errors = dielines.build_dieline_for(Job.load(job))
    assert dl is not None and not errors
    meta = read_meta(job, "front")
    assert meta is not None and check_flat(job, dl, meta).issue is not None


@pytest.mark.parametrize("view", ["main", "back"])
def test_flat_reuse_does_not_allow_duplicate_other_views(tmp_path: Path, view: str) -> None:
    job = tmp_path / "tea"
    assert api.init_job(job, "rte", {"L": 80, "W": 50, "H": 130}, "sbs_350").ok
    image = tmp_path / "solid.png"
    Image.new("RGB", (800, 500), "#b3c2a8").save(image)
    assert import_concept(job, image, name="top", view="flat", faces=["top_lid"]).ok
    duplicate = import_concept(job, image, name="other", view=view)
    assert not duplicate.ok and duplicate.errors[0].id == "DUPLICATE"


def test_codex_latest_still_rejects_old_image_for_other_flat_face(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = tmp_path / "tea"
    assert api.init_job(job, "rte", {"L": 80, "W": 50, "H": 130}, "sbs_350").ok
    home = tmp_path / "codex"
    generated = home / "generated_images"
    generated.mkdir(parents=True)
    image = generated / "solid.png"
    Image.new("RGB", (800, 500), "#b3c2a8").save(image)
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert import_concept(job, image, name="top", view="flat", faces=["top_lid"]).ok
    duplicate = import_concept(
        job, None, name="bottom", codex_latest=True, view="flat", faces=["btm_lid"]
    )
    assert not duplicate.ok and duplicate.errors[0].id == "DUPLICATE"
