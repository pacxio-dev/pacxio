"""C1 ports and pieces preserve circular geometry, identity, order and isolation."""

import pytest
from shapely.geometry import Point

from pacxio.geometry import Path, Segment, Vec, path_to_polygon
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles.components import Piece, Port, Tube4
from pacxio.styles.rte import RteParams, resolve


@pytest.mark.parametrize(
    "start,end,outward",
    [
        (Vec(10, 5), Vec(0, 5), Vec(0, 1)),
        (Vec(0, 5), Vec(10, 5), Vec(0, -1)),
        (Vec(4, 10), Vec(4, 0), Vec(-1, 0)),
        (Vec(0, 0), Vec(6, 8), Vec(-8, 6)),
    ],
)
def test_port_frame_places_the_attachment_edge_and_outward_direction(
    start: Vec, end: Vec, outward: Vec
) -> None:
    port = Port("lid", "back", start, end, outward)
    assert port.frame.apply(Vec(0, 0)).is_close(start)
    assert port.frame.apply(Vec(port.width, 0)).is_close(end)
    assert (port.frame.apply(Vec(0, 1)) - start).is_close(outward.normalized())


def test_port_rejects_a_skewed_basis_instead_of_distorting_parts() -> None:
    port = Port("lid", "back", Vec(0, 0), Vec(10, 0), Vec(1, 1))
    with pytest.raises(ValueError, match="垂直"):
        _ = port.frame


def test_piece_reflection_retains_arcs_ids_order_and_original() -> None:
    arc = Segment(Vec(0, 0), Vec(10, 0), 0.5)
    crease = Edge(
        id="hinge",
        type=EdgeType.CREASE,
        segments=[Segment(Vec(0, 0), Vec(10, 0))],
        panels=["back", "lid"],
    )
    slit = Edge(
        id="slit", type=EdgeType.CUT, segments=[Segment(Vec(2, 1), Vec(3, 1))], panels=["lid"]
    )
    piece = Piece(
        outline=[arc],
        panels={
            "lid": (PanelRole.LID, Path.rect(0, 0, 10, 4)),
            "tab": (PanelRole.TUCK, Path.rect(2, 4, 6, 3)),
        },
        creases=[crease],
        inner_edges=[slit],
        glue_areas=[Path.rect(1, 1, 2, 2)],
    )
    port = Port("top", "back", Vec(20, 30), Vec(10, 30), Vec(0, 1))
    moved = piece.transformed(port.frame)
    assert moved.outline[0].bulge == -0.5
    assert moved.outline[0].length() == pytest.approx(arc.length())
    assert moved.outline[0].start == Vec(20, 30) and moved.outline[0].end == Vec(10, 30)
    assert list(moved.panels) == ["lid", "tab"]
    assert moved.creases[0].id == "hinge" and moved.creases[0].panels == ["back", "lid"]
    assert moved.inner_edges[0].id == "slit"
    assert moved.glue_areas[0].bbox() == pytest.approx((17, 31, 19, 33))
    assert piece.outline == [arc] and piece.creases[0].segments[0].start == Vec(0, 0)
    moved.creases[0].panels.append("other")
    moved.inner_edges[0].panels.clear()
    assert piece.creases[0].panels == ["back", "lid"]
    assert piece.inner_edges[0].panels == ["lid"]


def test_body_ports_lie_on_the_parent_wall_and_point_outside() -> None:
    d = resolve(RteParams(L=80, W=50, H=180), resolve_material("sbs_350"), load_profile("default"))
    body = Tube4().build(d)
    assert list(body.piece.panels) == ["back", "side_a", "front", "side_b"]
    assert len(body.ports) == 9
    assert [e.id for e in body.piece.creases] == [
        "crease_back_side_a",
        "crease_side_a_front",
        "crease_front_side_b",
    ]
    for port in body.ports.values():
        poly = path_to_polygon(body.piece.panels[port.parent][1])
        mid = (port.start + port.end) * 0.5
        assert poly.boundary.distance(Point(*mid.as_tuple())) < 1e-9
        delta = port.outward.normalized() * 0.01
        assert poly.contains(Point(*(mid - delta).as_tuple()))
        assert not poly.contains(Point(*(mid + delta).as_tuple()))


def test_component_builds_do_not_share_mutable_panels_or_ports() -> None:
    d = resolve(RteParams(L=80, W=50, H=180), resolve_material("sbs_350"), load_profile("default"))
    a, b = Tube4().build(d), Tube4().build(d)
    a.piece.panels.pop("back")
    a.ports.clear()
    a.piece.creases.clear()
    assert len(b.piece.panels) == 4 and len(b.ports) == 9 and len(b.piece.creases) == 3
