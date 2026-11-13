"""Local vector-PDF calibration; reference sampling never changes production geometry."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pikepdf
from PIL import Image, ImageDraw
from shapely import distance, points
from shapely.geometry import LineString, MultiLineString

from pacxio.geometry import Affine, Vec
from pacxio.geometry import Path as GPath
from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.model.dieline import Dieline, EdgeType
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style

PT_MM = 25.4 / 72
SAMPLE_MM = 0.05  # 【初值】最大采样间隔，最大距离的采样不确定度 ≤ 0.025 mm
CURVE_MM = 0.005  # 【初值】参考 PDF 贝塞尔自适应采样误差；不转成生产刀线


@dataclass
class State:
    matrix: Affine
    color: tuple[float, float, float] = (0, 0, 0)
    clipped: bool = False


def _bezier(a: Vec, b: Vec, c: Vec, d: Vec, depth: int = 0) -> list[Vec]:
    line = LineString([a.as_tuple(), d.as_tuple()])
    error = max(line.distance(points([b.as_tuple(), c.as_tuple()])))
    if error <= CURVE_MM or depth >= 16:
        return [d]
    ab, bc, cd = (a + b) * 0.5, (b + c) * 0.5, (c + d) * 0.5
    abc, bcd = (ab + bc) * 0.5, (bc + cd) * 0.5
    mid = (abc + bcd) * 0.5
    return _bezier(a, ab, abc, mid, depth + 1) + _bezier(mid, bcd, cd, d, depth + 1)


def read_reference(
    path: Path, cut_color: tuple[float, float, float], crease_color: tuple[float, float, float]
) -> dict[str, list[list[Vec]]]:
    result: dict[str, list[list[Vec]]] = {"cut": [], "crease": []}

    def parse(obj: Any, resources: Any, state: State, level: int = 0) -> None:
        if level > 16:
            raise ValueError(t("PDF forms are nested too deeply"))
        stack = []
        paths: list[list[Vec]] = []
        active: list[Vec] = []

        def point(x: Any, y: Any) -> Vec:
            value = state.matrix.apply(Vec(float(x), float(y)))
            if not all(math.isfinite(v) for v in value.as_tuple()):
                raise ValueError(t("The reference PDF contains non-finite coordinates"))
            return value

        for ins in pikepdf.parse_content_stream(obj):
            vals, op = ins.operands, str(ins.operator)
            if op == "q":
                stack.append(replace(state))
            elif op == "Q":
                if not stack:
                    raise ValueError(t("The PDF graphics state stack is unbalanced"))
                state = stack.pop()
            elif op == "cm":
                state = replace(state, matrix=state.matrix @ Affine(*map(float, vals)))
            elif op == "RG":
                state = replace(state, color=(float(vals[0]), float(vals[1]), float(vals[2])))
            elif op == "G":
                state = replace(state, color=(float(vals[0]),) * 3)
            elif op == "K":
                cyan, magenta, yellow, black = map(float, vals)
                state = replace(
                    state,
                    color=(
                        (1 - cyan) * (1 - black),
                        (1 - magenta) * (1 - black),
                        (1 - yellow) * (1 - black),
                    ),
                )
            elif op == "m":
                active = [point(*vals)]
                paths.append(active)
            elif op == "l":
                if not active:
                    raise ValueError(t("A PDF line operator has no starting point"))
                active.append(point(*vals))
            elif op in {"c", "v", "y"}:
                if not active:
                    raise ValueError(t("A PDF curve has no starting point"))
                a = active[-1]
                if op == "c":
                    b, c, d = [point(*vals[i : i + 2]) for i in (0, 2, 4)]
                elif op == "v":
                    b, c, d = a, point(*vals[:2]), point(*vals[2:])
                else:
                    b, c, d = point(*vals[:2]), point(*vals[2:]), point(*vals[2:])
                active.extend(_bezier(a, b, c, d))
            elif op == "re":
                x, y, w, h = map(float, vals)
                active = [
                    point(x, y),
                    point(x + w, y),
                    point(x + w, y + h),
                    point(x, y + h),
                    point(x, y),
                ]
                paths.append(active)
            elif op == "h" and active:
                active.append(active[0])
            elif op in {"W", "W*"}:
                state = replace(state, clipped=True)
            elif op in {"S", "s", "B", "B*", "b", "b*"}:
                if op in {"s", "b", "b*"} and active and active[-1] != active[0]:
                    active.append(active[0])
                matches = [
                    name
                    for name, color in (("cut", cut_color), ("crease", crease_color))
                    if math.dist(state.color, color) <= 0.25
                ]
                if matches and state.clipped:
                    raise ValueError(
                        t(
                            "A coloured dieline uses a clipping path. Flatten the PDF clipping before calibrating"
                        )
                    )
                if len(matches) > 1:
                    raise ValueError(t("The cut and crease colours cannot be distinguished"))
                if matches:
                    result[matches[0]].extend(p for p in paths if len(p) > 1)
                paths = []
                active = []
            elif op in {"n", "f", "F", "f*"}:
                paths = []
                active = []
            elif op == "Do":
                form = resources.get("/XObject", {}).get(vals[0])
                if form is not None and str(form.get("/Subtype")) == "/Form":
                    fm = Affine(*map(float, form.get("/Matrix", [1, 0, 0, 1, 0, 0])))
                    parse(
                        form,
                        form.get("/Resources", resources),
                        replace(state, matrix=state.matrix @ fm),
                        level + 1,
                    )
                elif form is not None and str(form.get("/Subtype")) == "/Image":
                    # Bitmap artwork is ignored, but cannot supply reference knife paths.
                    continue

    with pikepdf.open(path) as pdf:
        if len(pdf.pages) != 1:
            raise ValueError(t("The reference PDF must contain one page and one dieline"))
        page = pdf.pages[0]

        def inherited(key: str, default: Any) -> Any:
            node: Any = page.obj
            for _ in range(32):
                if key in node:
                    return node[key]
                node = node.get("/Parent")
                if node is None:
                    return default
            raise ValueError(t("The PDF page inheritance tree is too deep"))

        if int(inherited("/Rotate", 0)) % 360:
            raise ValueError(
                t(
                    "Apply the page rotation to the PDF content first. Calibration does not rotate the reference"
                )
            )
        unit = float(page.obj.get("/UserUnit", 1)) * PT_MM
        parse(page, inherited("/Resources", {}), State(Affine.scale(unit)))
    if not result["cut"]:
        raise ValueError(
            t(
                "No vector cut lines matched the cut colour in the reference. Bitmap or black-and-white references cannot be detected automatically"
            )
        )
    return result


def _generated(dl: Dieline) -> dict[str, list[list[Vec]]]:
    result: dict[str, list[list[Vec]]] = {"cut": [], "crease": []}
    for edge in dl.edges:
        key = (
            "cut"
            if edge.type == EdgeType.CUT
            else "crease"
            if edge.type in (EdgeType.CREASE, EdgeType.REVERSE_CREASE)
            else None
        )
        if key:
            for segment in edge.segments:
                result[key].append(GPath([segment]).to_points(CURVE_MM))
    return result


def _geometry(paths: list[list[Vec]]) -> MultiLineString:
    return MultiLineString([[v.as_tuple() for v in path] for path in paths])


def _metrics(a: MultiLineString, b: MultiLineString) -> dict[str, float]:
    def directed(source: MultiLineString, target: MultiLineString) -> tuple[float, float]:
        values = []
        for line in source.geoms:
            count = max(2, math.ceil(line.length / SAMPLE_MM) + 1)
            locations = [line.interpolate(t).coords[0] for t in np.linspace(0, line.length, count)]
            values.extend(distance(points(locations), target).tolist())
        return max(values), sum(values) / len(values)

    ab, ba = directed(a, b), directed(b, a)
    return {
        "max_mm": max(ab[0], ba[0]),
        "mean_mm": (ab[1] + ba[1]) / 2,
        "generated_to_ref_max_mm": ab[0],
        "ref_to_generated_max_mm": ba[0],
    }


def calibrate(
    style_id: str,
    reference: Path,
    size: str,
    material_id: str,
    profile_id: str,
    out: Path,
    tolerance: float = 0.1,
    part: str | None = None,
    options: dict[str, Any] | None = None,
    cut_color: tuple[float, float, float] = (1, 0, 0),
    crease_color: tuple[float, float, float] = (0, 1, 0),
) -> Result:
    r = Result(command="styles calibrate")
    try:
        if not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError(t("Tolerance must be a finite positive number"))
        style = get_style(style_id)
        material = resolve_material(material_id)
        profile = load_profile(profile_id)
        dims = [float(v) for v in size.lower().replace("×", "x").split("x")]
        if len(dims) != 3 or not all(math.isfinite(v) and v > 0 for v in dims):
            raise ValueError(t("--size requires positive dimensions in LxWxH format"))
        values = dict(zip(("L", "W", "H"), dims, strict=True)) | (options or {})
        fields = style.param_schema().model_fields
        if set(values) - set(fields):
            raise ValueError(t("An unrecognised box style parameter was supplied"))
        if "apply_compensation" in fields:
            values["apply_compensation"] = False
        params = style.param_schema()(**values)
        issues = style.validate(params, material, profile)
        if issues:
            r.errors.extend(issues)
            r.ok = False
            r.exit_code = 1
            return r
        multi = getattr(style, "generate_parts", None)
        if multi:
            parts = multi(params, material, profile)
            if part not in parts:
                raise ValueError(
                    t(
                        "For a multi-part style, use --part to select one of {parts}",
                        parts=list(parts),
                    )
                )
            dl = parts[part]
        else:
            if part is not None:
                raise ValueError(t("--part is not available for single-piece styles"))
            dl = style.generate(params, material, profile)
        generated = _generated(dl)
        ref = read_reference(reference, cut_color, crease_color)
        gx0, gy0, _, _ = _geometry(generated["cut"]).bounds
        rx0, ry0, _, _ = _geometry(ref["cut"]).bounds
        shift = Vec(gx0 - rx0, gy0 - ry0)
        ref = {key: [[v + shift for v in path] for path in paths] for key, paths in ref.items()}
        metrics = {}
        for key in generated:
            if not generated[key] and not ref[key]:
                continue
            if not generated[key] or not ref[key]:
                raise ValueError(
                    t("The {key} line type is missing; calibration cannot pass", key=key)
                )
            metrics[key] = _metrics(_geometry(generated[key]), _geometry(ref[key]))
        passed = all(m["max_mm"] <= tolerance for m in metrics.values())
        report = {
            "style": style_id,
            "part": part,
            "params": params.model_dump(),
            "reference": str(reference.resolve()),
            "tolerance_mm": tolerance,
            "passed": passed,
            "metrics": metrics,
            "translation_mm": shift.as_tuple(),
            "reference_sampling_mm": CURVE_MM,
            "distance_sampling_mm": SAMPLE_MM,
            "accuracy_level_updated": False,
            "note": t(
                "Alignment uses translation only. Sampled reference curves do not become production cut lines. A synthetic reference does not establish L1 or change the physical verification status"
            ),
        }
        out.mkdir(parents=True, exist_ok=True)
        report_file = out / "calibration.json"
        overlay = out / "overlay.png"
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        allpaths = [p for dataset in (generated, ref) for paths in dataset.values() for p in paths]
        coords = [v for path in allpaths for v in path]
        x0, y0 = min(v.x for v in coords), min(v.y for v in coords)
        x1, y1 = max(v.x for v in coords), max(v.y for v in coords)
        scale = min(1500 / max(x1 - x0, 1), 1100 / max(y1 - y0, 1))
        img = Image.new("RGB", (1600, 1200), "white")
        draw = ImageDraw.Draw(img)
        for dataset, color in ((generated, "#141b2a"), (ref, "#e44242")):
            for paths in dataset.values():
                for path in paths:
                    draw.line(
                        [(50 + (v.x - x0) * scale, 1150 - (v.y - y0) * scale) for v in path],
                        fill=color,
                        width=1,
                    )
        draw.text(
            (30, 20),
            f"{style_id} | generated black / reference red | tolerance {tolerance:g} mm | {'PASS' if passed else 'DIFFERENT'}",
            fill="black",
        )
        img.save(overlay)
        r.artifacts = [str(report_file), str(overlay)]
        r.data = report
        if not passed:
            r.fail(
                Issue(
                    id="CALIBRATION",
                    message=t(
                        "The reference differs from the generated dieline by more than the tolerance"
                    ),
                    fix=t(
                        "Inspect the overlay and the distances in both directions for each line type. Check the dimensions, board, parameters and reference orientation first"
                    ),
                )
            )
        return r
    except (ValueError, KeyError, OSError, pikepdf.PdfError) as error:
        return r.fail(
            Issue(
                id="CALIBRATION",
                message=str(error),
                fix=t(
                    "Check the local vector reference, dimensions, board, line colours and part. Use a vector dieline at a known scale"
                ),
            ),
            exit_code=2,
        )
