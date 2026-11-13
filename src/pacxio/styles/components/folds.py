"""Component fold declarations consumed by the existing hinge-tree renderer."""

from dataclasses import dataclass

from pacxio.messages import t


@dataclass(frozen=True)
class FoldAction:
    keys: tuple[tuple[int, float], ...]
    layer: float = 0


@dataclass(frozen=True)
class FoldRecipe:
    root: str
    moves: dict[str, FoldAction]
    steps: int
    open_step: int
    pose_step: int = 2
    ground_step: int = 2


def action(step: int, angle: float = 90, layer: float = 0) -> FoldAction:
    return FoldAction(((step, angle),), layer)


def ring() -> dict[str, FoldAction]:
    return {
        "glue": action(1, layer=1),
        "side_a": action(2),
        "front": action(2),
        "side_b": action(2),
    }


def tuck_end(prefix: str, first: int) -> dict[str, FoldAction]:
    bottom = prefix == "btm"
    a, b = ("dust_ba", "dust_bb") if bottom else ("dust_ta", "dust_tb")
    return {
        a: action(first),
        b: action(first),
        f"{prefix}_lid": action(first + 1),
        f"{prefix}_tuck": FoldAction(((first, 45.0), (first + 1, 90.0)), 1),
    }


def carton_recipe(kind: str, hanger: bool = False) -> FoldRecipe:
    moves = ring()
    if kind == "tuck":
        moves |= tuck_end("btm", 3)
        first = 5
    elif kind == "snaplock":
        moves |= {
            "btm_back": action(3),
            "btm_sa": action(4, layer=2),
            "btm_sb": action(4, layer=2),
            "btm_front": action(5, layer=1),
        }
        first = 6
    elif kind == "autolock":
        moves |= {
            "btm_back": action(3),
            "btm_sa": action(3, layer=2),
            "btm_sb": action(3, layer=2),
            "btm_front": action(3, layer=1),
        }
        first = 4
    else:
        raise ValueError(t("Unknown bottom fold type {kind}", kind=kind))
    moves |= tuck_end("top", first)
    if hanger:
        moves["hanger"] = FoldAction(())
    return FoldRecipe("front", moves, first + 1, first - 1)


def glued_ends(has_top: bool = True) -> FoldRecipe:
    moves = ring()
    for prefix, first in (("btm", 3), ("top", 6)):
        if prefix == "top" and not has_top:
            continue
        moves |= {
            f"{prefix}_side_a": action(first, layer=2),
            f"{prefix}_side_b": action(first, layer=3),
            f"{prefix}_back": action(first + 1, layer=1),
            f"{prefix}_front": action(first + 2),
        }
    return FoldRecipe("front", moves, 8 if has_top else 5, 5)
