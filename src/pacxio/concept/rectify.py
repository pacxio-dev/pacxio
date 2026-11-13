"""展开平面图拉正（ 2.5、2.7）：AI 出的正视平面图 → 每个面按真实毫米尺寸的单独图。

做法（F7 手工量过的步骤，做成确定性的）：

1. 画面边界：图边一圈的颜色当背景（提示词要求纯白），和它差得多的像素占到一行 / 一列的一半以上，
   这一行 / 列就在画面里；整张图都是浅底时退回"有一点内容就算"。图边不是白的（米白底、插画铺到了
   图边）就是画面铺满了整张图（AI 常这样画），按整张图量——这时画面的比例就是画布的比例；
2. 折线：在按真实宽度比例算出的位置附近，找贯穿上下（≥ 60% 的高度）的细竖线——比左右两边都深
   （或都浅）；找不到就按真实宽度比例切；
3. 每个面单独拉伸到 面宽 × 面高（mm）——AI 常把相邻两面画得不等宽（F7：1.40 : 1），各面各自换算。

量出的宽高比和真实值的偏差（aspect_error）用来在同一个面有几张图时挑最准的那张。
平面图带 AI 写的字：拉正的结果和概念图一样存在 `concepts/` 下，只用来量位置，不能直接进印刷文件。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from pacxio.messages import t
from pacxio.model.dieline import Dieline, EdgeType, Panel

BORDER_PX = 4  # 取背景色的图边宽度
BG_TOL = 12  # 和背景差多少（0–255，取三通道最大差）算画面
FILL_SHARE = 0.5  # 一行 / 一列里画面像素占这么多才算在画面里
FILL_SHARE_LOW = 0.05  # 画面本身也是白底时的退路：有一点内容就算
MIN_AREA_SHARE = 0.2  # 量出的画面小于整张图的这么多，说明白底画面，改用退路
EDGE_BG_SHARE = 0.5  # 图的某一条边上背景色像素少于这么多：画面贴到了图边，量出的边界不可信
# 图边颜色比纯白暗这么多（取三通道里差得最多的）：不是提示词要的白边，是画面本身铺到了图边
SURROUND_TOL = 8  # AI 出的纯白一般是 254–255；米白 (254,251,244) 这种很浅的底也要认出来
LINE_DELTA = 6.0  # 细线比左右两边深（或浅）多少灰度
LINE_GAP = 3  # 和左右第几列比
LINE_SPAN = 0.6  # 细线要贯穿画面高度的这么多
SEARCH_SHARE = 0.15  # 在预计位置左右、画面宽度的这么多范围内找折线
MAX_LINE_PX = 12  # 比这宽的不是细线
RECTIFIED_DIR = "rectified"  # concepts/rectified/<id>.<面>.png
GRID_MM, GRID_MAJOR_MM = 10, 50
SIDES_CCW = ("right", "top", "left", "bottom")


class FlatError(ValueError):
    """平面图里量不出画面（全白、太小……）。"""


@dataclass(frozen=True)
class FaceCut:
    face: str
    box: tuple[int, int, int, int]  # 原图像素 (x0, y0, x1, y1)，右、下不含
    size_mm: tuple[float, float]  # 图里画出来的宽、高（躺着画的面宽高对调）
    turns: int = 0  # 图里的面要逆时针转几个 90° 才正立（抽屉外套一条里的侧面）

    @property
    def upright_mm(self) -> tuple[float, float]:
        """面正立时的宽、高 mm。"""
        w, h = self.size_mm
        return (h, w) if self.turns % 2 else (w, h)

    @property
    def aspect_error(self) -> float:
        """量出的宽高比 ÷ 真实宽高比 − 1（+0.14 = 画宽了 14%）。"""
        w, h = self.box[2] - self.box[0], self.box[3] - self.box[1]
        return (w / h) / (self.size_mm[0] / self.size_mm[1]) - 1


@dataclass(frozen=True)
class FlatCut:
    bounds: tuple[int, int, int, int]
    folds: tuple[
        tuple[int, int] | None, ...
    ]  # 每条折线占的列（竖叠时是行，含）；None = 没找到，按比例切
    faces: tuple[FaceCut, ...]
    full_bleed: bool = False  # 画面铺满整张图（没留白边），按整张图量的
    axis: str = "x"  # x：面从左到右并排；y：面从上到下叠（等宽的面上下叠，）

    @property
    def size_mm(self) -> tuple[float, float]:
        """整张画面的真实宽、高 mm（各面按 axis 拼起来）。"""
        if self.axis == "y":
            return max(c.size_mm[0] for c in self.faces), sum(c.size_mm[1] for c in self.faces)
        return sum(c.size_mm[0] for c in self.faces), max(c.size_mm[1] for c in self.faces)

    @property
    def folds_found(self) -> bool:
        return all(f is not None for f in self.folds)


# ---------------- 量 ----------------


def _background(a: np.ndarray) -> np.ndarray:
    b = BORDER_PX
    edge = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3),
                           a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3)])  # fmt: skip
    bg: np.ndarray = np.median(edge, axis=0)
    return bg


def _span(profile: np.ndarray, share: float) -> tuple[int, int] | None:
    idx = np.where(profile > share)[0]
    return (int(idx[0]), int(idx[-1]) + 1) if len(idx) else None


def _white_sides(a: np.ndarray) -> dict[str, bool]:
    """图的四条边各自是不是白的（一半以上的像素接近纯白）。"""
    b = BORDER_PX
    sides = {"top": a[:b], "bottom": a[-b:], "left": a[:, :b], "right": a[:, -b:]}
    return {k: bool(((255 - v.reshape(-1, 3)).max(axis=1) <= SURROUND_TOL).mean() >= EDGE_BG_SHARE)
            for k, v in sides.items()}  # fmt: skip


def locate_art(img: Image.Image) -> tuple[tuple[int, int, int, int], bool]:
    """画面在图里的范围 (x0, y0, x1, y1)，以及是不是铺满了整张图。

    图边是白的：和白边不同的部分就是画面。图边不是白的：提示词要的是白边，这说明画面本身铺到了图边，
    按整张图量（不能拿画面里的底色当背景去量——量到的只是中间的插画，比例差得离谱，Halvermoor 首轮 +77%）。
    只有一两条边是白的（：画面铺满、多出的比例做成一侧的白条）：白的那几边量到画面为止，其余到图边。"""
    a = np.asarray(img.convert("RGB"), dtype=np.int16)
    h, w, _ = a.shape
    white = _white_sides(a)
    if 0 < sum(white.values()) < 4:
        art = (255 - a).max(axis=2) > BG_TOL
        cx = _span(art.mean(axis=0), FILL_SHARE) or (0, w)
        cy = _span(art.mean(axis=1), FILL_SHARE) or (0, h)
        box = (cx[0] if white["left"] else 0, cy[0] if white["top"] else 0,
               cx[1] if white["right"] else w, cy[1] if white["bottom"] else h)  # fmt: skip
        if (box[2] - box[0]) * (box[3] - box[1]) < MIN_AREA_SHARE * w * h:
            raise FlatError(t("The artwork could not be measured. There is almost no artwork outside the white band."))
        return box, False
    bg, b = _background(a), BORDER_PX
    if float((255 - bg).max()) > SURROUND_TOL:
        return (0, 0, w, h), True
    for side in (a[:b], a[-b:], a[:, :b], a[:, -b:]):
        if (np.abs(side.reshape(-1, 3) - bg).max(axis=1) <= BG_TOL).mean() < EDGE_BG_SHARE:
            # 白边上一部分是画面：画面偏到了一边、被图边切掉了，量出的边界不可信
            raise FlatError(t("The artwork touches the image edge. An uninterrupted white border is needed to measure its bounds."))
    art = np.abs(a - bg).max(axis=2) > BG_TOL
    for share in (FILL_SHARE, FILL_SHARE_LOW):
        xs, ys = _span(art.mean(axis=0), share), _span(art.mean(axis=1), share)
        if xs and ys and (xs[1] - xs[0]) * (ys[1] - ys[0]) >= MIN_AREA_SHARE * w * h:
            return (xs[0], ys[0], xs[1], ys[1]), False
    raise FlatError(t("The artwork could not be measured because the image is nearly all white. Give white artwork a visible outline."))


def find_bounds(img: Image.Image) -> tuple[int, int, int, int]:
    """画面在图里的范围 (x0, y0, x1, y1)。"""
    return locate_art(img)[0]


def _line_score(gray: np.ndarray) -> np.ndarray:
    """每一列"是细竖线"的程度：比左右第 LINE_GAP 列都深（或都浅）的行占多少。"""
    k = LINE_GAP
    left, right = np.roll(gray, k, axis=1), np.roll(gray, -k, axis=1)
    darker = (np.minimum(left, right) - gray > LINE_DELTA).mean(axis=0)
    lighter = (gray - np.maximum(left, right) > LINE_DELTA).mean(axis=0)
    score: np.ndarray = np.maximum(darker, lighter)
    score[:k], score[-k:] = 0.0, 0.0
    return score


def find_fold(gray: np.ndarray, expected: float) -> tuple[int, int] | None:
    """在预计位置（列）附近找折线：返回细线占的列 (x_lo, x_hi)（含），找不到返回 None。"""
    w = gray.shape[1]
    score = _line_score(gray)
    lo = max(0, int(expected - SEARCH_SHARE * w))
    hi = min(w, int(expected + SEARCH_SHARE * w) + 1)
    if hi <= lo:
        return None
    best = lo + int(np.argmax(score[lo:hi]))
    if score[best] < LINE_SPAN:
        return None
    x0 = x1 = best
    while x0 > 0 and score[x0 - 1] >= LINE_SPAN:
        x0 -= 1
    while x1 < w - 1 and score[x1 + 1] >= LINE_SPAN:
        x1 += 1
    return (x0, x1) if x1 - x0 + 1 <= MAX_LINE_PX else None


def measure_flat(
    img: Image.Image, faces: list[str], sizes_mm: list[tuple[float, float]],
    turns: list[int] | None = None, axis: str = "x",
) -> FlatCut:  # fmt: skip
    """量平面图：画面边界、面与面之间的折线、每个面在原图里的像素框。faces 按图里从左到右（axis="y"
    时从上到下）；sizes_mm 是图里画出来的宽、高，turns 是每个面要逆时针转几个 90° 才正立（不给就都是正立画的）。"""
    if not faces or len(faces) != len(sizes_mm):
        raise FlatError(t("Supply the panels in the image and the dimensions of each panel"))
    turns = turns or [0] * len(faces)
    (x0, y0, x1, y1), full_bleed = locate_art(img)
    gray = np.asarray(img.convert("L"), dtype=np.float32)[y0:y1, x0:x1]
    if axis == "y":  # 上下叠：转置后照左右并排的办法找折线、切
        gray = gray.T
        sizes_mm = [(h, w) for w, h in sizes_mm]
    total = sum(w for w, _ in sizes_mm)
    width = gray.shape[1]
    cuts: list[tuple[int, int]] = []  # 每个面的 [左, 右) 列（相对画面）
    folds: list[tuple[int, int] | None] = []
    left, acc = 0, 0.0
    for w_mm, _ in sizes_mm[:-1]:
        acc += w_mm
        fold = find_fold(gray, width * acc / total)
        folds.append(fold)
        if fold is None:
            at = round(width * acc / total)
            cuts.append((left, at))
            left = at
        else:
            cuts.append((left, fold[0]))
            left = fold[1] + 1
    cuts.append((left, width))
    if axis == "y":
        face_cuts = tuple(
            FaceCut(face, (x0, y0 + a, x1, y0 + b), (h, w), turn)
            for face, (w, h), turn, (a, b) in zip(faces, sizes_mm, turns, cuts, strict=True)
        )
    else:
        face_cuts = tuple(
            FaceCut(face, (x0 + a, y0, x0 + b, y1), size, turn)
            for face, size, turn, (a, b) in zip(faces, sizes_mm, turns, cuts, strict=True)
        )
    if any(min(c.box[2] - c.box[0], c.box[3] - c.box[1]) < 2 for c in face_cuts):
        raise FlatError(t("A measured panel is too narrow. Check that the listed panels match the image."))
    return FlatCut((x0, y0, x1, y1), tuple(folds), face_cuts, full_bleed, axis)


def swapped_faces(img: Image.Image, cut: FlatCut) -> bool:
    """两面一张、预计位置没找到折线，镜像的位置（两面换个左右）却有一条：AI 多半把两面左右画反了
    （开槽箱测试 R-06：背面视角里左侧面画错了边，平面图跟着画反，以前按比例切开当合格）。"""
    if len(cut.faces) != 2 or cut.folds[0] is not None:
        return False
    x0, y0, x1, y1 = cut.bounds
    gray = np.asarray(img.convert("L"), dtype=np.float32)[y0:y1, x0:x1]
    if cut.axis == "y":
        gray = gray.T
    sizes = [c.size_mm[1] if cut.axis == "y" else c.size_mm[0] for c in cut.faces]
    width = gray.shape[1]
    here, mirror = width * sizes[0] / sum(sizes), width * sizes[1] / sum(sizes)
    if abs(here - mirror) <= SEARCH_SHARE * width:  # 两面差不多宽：换了也看不出
        return False
    return find_fold(gray, mirror) is not None


def rectify_face(img: Image.Image, cut: FaceCut, px_per_mm: float) -> Image.Image:
    """把一个面按真实尺寸拉正：宽 = 面宽 mm × px_per_mm，高同理；躺着画的面转正。"""
    w, h = cut.size_mm
    size = (max(1, round(w * px_per_mm)), max(1, round(h * px_per_mm)))
    return _turn(img.convert("RGB").crop(cut.box).resize(size, Image.Resampling.LANCZOS), cut.turns)


def face_crop(img: Image.Image, cut: FaceCut) -> Image.Image:
    """图里这个面的原样（不缩放），躺着画的转正。"""
    return _turn(img.convert("RGB").crop(cut.box), cut.turns)


def straighten(
    img: Image.Image, corners: list[tuple[float, float]], size: tuple[int, int]
) -> Image.Image:
    """透视拉正：corners 是图里这个面的四个角（面正立时的左上、右上、右下、左下，像素），输出 size。
    给出图参考用（概念图是透视的，拉正后只当"照这个构图画"的参考图，不描、不进印刷文件）。"""
    w, h = size
    dst = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (x, y), (u, v) in zip(corners, dst, strict=True):  # 输出 (u, v) → 输入 (x, y)
        rows += [[u, v, 1, 0, 0, 0, -u * x, -v * x], [0, 0, 0, u, v, 1, -u * y, -v * y]]
        rhs += [x, y]
    coef = np.linalg.solve(np.array(rows, dtype=np.float64), np.array(rhs, dtype=np.float64))
    return img.convert("RGB").transform(
        (w, h), Image.Transform.PERSPECTIVE, tuple(float(c) for c in coef), Image.Resampling.BICUBIC
    )


def draw_grid(img: Image.Image, px_per_mm: float) -> Image.Image:
    """叠一层毫米网格（每 10 mm 细线、每 50 mm 粗线，原点在左下角、y 向上），方便量位置。"""
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out, "RGBA")
    w, h = out.size
    for mm in range(0, int(max(w, h) / px_per_mm) + 1, GRID_MM):
        major = mm % GRID_MAJOR_MM == 0
        ink = (220, 40, 40, 150) if major else (220, 40, 40, 60)
        x, y = round(mm * px_per_mm), h - 1 - round(mm * px_per_mm)
        if x < w:
            d.line([(x, 0), (x, h)], fill=ink, width=1)
            if major and mm:
                d.text((x + 3, h - 14), str(mm), fill=(200, 30, 30, 255))
        if y >= 0:
            d.line([(0, y), (w, y)], fill=ink, width=1)
            if major and mm:
                d.text((3, y - 13), str(mm), fill=(200, 30, 30, 255))
    return out


# ---------------- 刀版那一侧：面的尺寸与正立方向 ----------------


def face_rects(dl: Dieline) -> dict[str, tuple[float, float, float, float]]:
    """每个看得到的面在刀版上取的矩形（与效果图纹理相同：侧面取整高）。键是刀版面 id。
    多部件盒型的目录刀版：合上后看得到的面，键是"部件.面"。"""
    from pacxio.mockup.fold import build_box

    rects = dl.style_meta.get("set_rects")
    if isinstance(rects, dict):
        return {k: (float(v[0]), float(v[1]), float(v[2]), float(v[3])) for k, v in rects.items()}
    return {f.panel: f.tex_rect for f in build_box(dl).faces}


def face_size(rect: tuple[float, float, float, float], panel: Panel) -> tuple[float, float]:
    """面在正立方向下的 宽 × 高（mm）。"""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    return (h, w) if upright_turns(panel) % 2 else (w, h)


def upright_turns(panel: Panel) -> int:
    """刀版视图要逆时针转几个 90° 才能让面局部的 +y（正立方向）朝上。"""
    angle = math.degrees(math.atan2(panel.frame.d, panel.frame.c))
    return round((90.0 - angle) / 90.0) % 4


def _turn(img: Image.Image, turns: int) -> Image.Image:
    """逆时针转 turns 个 90°。"""
    ops = {1: Image.Transpose.ROTATE_90, 2: Image.Transpose.ROTATE_180,
           3: Image.Transpose.ROTATE_270}  # fmt: skip
    return img.transpose(ops[turns % 4]) if turns % 4 else img


def upright(img: Image.Image, panel: Panel) -> Image.Image:
    return _turn(img, upright_turns(panel))


def to_blank(img: Image.Image, panel: Panel) -> Image.Image:
    """正立的面图转回它在刀版上的方向（upright 的反向）。"""
    return _turn(img, -upright_turns(panel))


def blank_strip(dl: Dieline, faces: list[str]) -> bool:
    """多部件盒型里在刀版上从左到右相接、一样高的几个面（抽屉外套的 左侧 | 顶 | 右侧）：平面图照刀版的
    样子画成一条，侧面躺着（抽屉测试 F-11）。天地盖盖侧壁那样合上后相邻、刀版上不相接的一对不算。"""
    if len(faces) < 2 or not dl.style_meta.get("parts"):
        return False
    rects = face_rects(dl)
    if any(f not in rects for f in faces):
        return False
    rs = [rects[f] for f in faces]
    return all(abs(a[2] - b[0]) < 1.0 and abs(a[1] - b[1]) < 1.0 and abs(a[3] - b[3]) < 1.0
               for a, b in zip(rs, rs[1:], strict=False))  # fmt: skip


# 多部件盒型里合上后在转角相邻的两面（看正面时右侧在它右边；看背面时左侧在它右边）
CORNER_PAIRS = (("front", "side_b"), ("back", "side_a"))


def corner_pair(dl: Dieline, faces: list[str]) -> bool:
    """多部件盒型同一部件里合上后在转角相邻的两面（正面 + 右侧、背面 + 左侧）：高的盖子按转角左右并排画，
    花纹才能绕过转角（2026-11-11 用户定，customer 香薰盒；浅盖子仍按 上下叠）。"""
    if not dl.style_meta.get("parts") or len(faces) != 2:
        return False
    a, b = (f.split(".", 1) for f in faces)
    return len(a) == len(b) == 2 and a[0] == b[0] and (a[1], b[1]) in CORNER_PAIRS


def stack_axis(dl: Dieline, faces: list[str]) -> str:
    """平面图里几个面怎么排：多部件盒型里刀版上不相接的几个面（天地盖的前壁 / 后壁、左 / 右侧壁，抽屉内托
    前脸 / 后壁）都正着画、等宽上下叠（"y"，：分开放、四周留白缝会被画扁 15–30%，叠在一起铺满一次合格）；
    合上后在转角相邻的两面（高盖子，`corner_pair`）和其余的左右并排（"x"）。"""
    if any(list(s.get("faces", [])) == faces for s in dl.style_meta.get("flat_stacks") or []):
        return "y"  # 盒型声明的上下叠（飞机盒：盖 + 插舌、背板 + 底、两个侧面）
    if corner_pair(dl, faces):
        return "x"
    multipart = bool(dl.style_meta.get("parts"))
    return "y" if multipart and len(faces) > 1 and not blank_strip(dl, faces) else "x"


def joined_stack(dl: Dieline, faces: list[str]) -> bool:
    """上下叠的几个面在合上的盒子上是连着的（上一面的下边就是下一面的上边，飞机盒的盖和插舌）：画面跨折线
    连着画。天地盖侧壁、抽屉内托前后脸这类各自成画的不算。"""
    return any(
        list(s.get("faces", [])) == faces and bool(s.get("joined"))
        for s in dl.style_meta.get("flat_stacks") or []
    )


def drawn_layout(dl: Dieline, faces: list[str]) -> list[tuple[tuple[float, float], int]]:
    """平面图里各面（从左到右）画出来的 (宽, 高) mm 和要逆时针转几个 90° 才正立：照刀版画成一条的
    （blank_strip）按刀版上的方向，其余每个面正立画。"""
    rects = face_rects(dl)
    panels = {p.id: p for p in dl.panels}
    if blank_strip(dl, faces):
        return [((rects[f][2] - rects[f][0], rects[f][3] - rects[f][1]), upright_turns(panels[f]))
                for f in faces]  # fmt: skip
    return [(face_size(rects[f], panels[f]), 0) for f in faces]


def hinge_side(dl: Dieline, panel_id: str) -> tuple[str, str] | None:
    """面（正立摆放时）和它铰链所在的父面相接的那条边：(哪一边, 父面 id)；没有铰链返回 None。"""
    panel = dl.panel(panel_id)
    if panel.hinge is None:
        return None
    parent = panel.hinge.parent_panel
    # 压线可能被插孔分成几段（托盘的前后壁）：几段一起算，只看第一段会偏到一头（抽屉测试 F-13）
    segments = [s for e in dl.edges_of(EdgeType.CREASE) if set(e.panels) == {panel_id, parent}
                for s in e.segments]  # fmt: skip
    if not segments:
        return None
    xs = [v for s in segments for v in (s.bbox()[0], s.bbox()[2])]
    ys = [v for s in segments for v in (s.bbox()[1], s.bbox()[3])]
    px0, py0, px1, py1 = panel.polygon.bbox()
    dx = (min(xs) + max(xs)) / 2 - (px0 + px1) / 2
    dy = (min(ys) + max(ys)) / 2 - (py0 + py1) / 2
    side = (0 if dx > 0 else 2) if abs(dx) > abs(dy) else (1 if dy > 0 else 3)
    return SIDES_CCW[(side + upright_turns(panel)) % 4], parent


__all__ = [
    "FaceCut",
    "FlatCut",
    "FlatError",
    "blank_strip",
    "draw_grid",
    "stack_axis",
    "drawn_layout",
    "face_crop",
    "face_rects",
    "face_size",
    "find_bounds",
    "find_fold",
    "locate_art",
    "hinge_side",
    "measure_flat",
    "rectify_face",
    "straighten",
    "to_blank",
    "upright",
    "upright_turns",
]
