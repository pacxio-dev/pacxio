"""检查规则自身的反向测试：故意制造的错误必须被检出（否则 D12 的拒绝逻辑形同虚设）。"""

from __future__ import annotations

from pacxio.geometry import Path, Segment, Vec
from pacxio.model.dieline import Dieline, Edge, EdgeType
from pacxio.model.profile import load_profile, resolve_material
from pacxio.preflight import check_crease_sides, check_s01, check_s02
from pacxio.styles import get_style
from pacxio.styles.rte import RteParams


def good() -> Dieline:
    return get_style("rte").generate(
        RteParams(L=80, W=50, H=180), resolve_material("sbs_350"), load_profile("default")
    )


def with_cut(dl: Dieline, segments: list[Segment]) -> Dieline:
    edges = [e for e in dl.edges if e.type != EdgeType.CUT]
    edges.append(Edge(id="cut_outline", type=EdgeType.CUT, segments=segments))
    return dl.model_copy(update={"edges": edges})


def cut_segments(dl: Dieline) -> list[Segment]:
    """外轮廓的切线（插舌压线两端的钩形切口这类内部切口不算）。"""
    return list(dl.outline_segments())


def test_s01_detects_open_outline() -> None:
    dl = good()
    issues = check_s01(with_cut(dl, cut_segments(dl)[:-1]))
    assert [i.id for i in issues] == ["S01"] and "闭合" in issues[0].message


def test_s01_detects_self_intersection() -> None:
    bow = Path.from_points([Vec(0, 0), Vec(10, 10), Vec(10, 0), Vec(0, 10)], closed=True).segments
    issues = check_s01(with_cut(good(), bow))
    assert any("自交" in i.message for i in issues)


def test_s01_detects_duplicate_segments() -> None:
    dl = good()
    segs = cut_segments(dl)
    dup = with_cut(dl, [*segs[:5], segs[4], *segs[5:]])  # 同一线段重复一次
    assert any("重复" in i.message for i in check_s01(dup))


def test_s01_detects_crease_overlapping_cut() -> None:
    dl = good()
    first_cut = cut_segments(dl)[2]
    extra = Edge(id="bad_crease", type=EdgeType.CREASE, segments=[first_cut], panels=["a", "b"])
    assert any(
        "重复" in i.message for i in check_s01(dl.model_copy(update={"edges": [*dl.edges, extra]}))
    )


def test_crease_must_lie_on_both_panels() -> None:
    dl = good()
    moved = [
        e.model_copy(
            update={
                "segments": [Segment(s.start + Vec(3, 0), s.end + Vec(3, 0)) for s in e.segments]
            }
        )
        if e.id == "crease_back_side_a"
        else e
        for e in dl.edges
    ]
    issues = check_crease_sides(dl.model_copy(update={"edges": moved}))
    assert issues and "crease_back_side_a" in issues[0].message


def test_s02_detects_wrong_dimension_and_reports_both_values() -> None:
    dl = good()
    w, h = dl.panel("back").polygon.bbox()[2:]  # 仅为取得合理量级，下面故意给错
    issues = check_s02(dl, {"back": (80.0, 180.0)})  # 未含补偿的值，而几何已补偿
    assert len(issues) == 1 and issues[0].id == "S02" and issues[0].fix
    assert "80.480" in issues[0].message and "80.000" in issues[0].message
    assert w > 0 and h > 0
    assert check_s02(dl, {"back": (80.48, 180.48)}) == []
