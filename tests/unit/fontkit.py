"""测试用的合成字体（不是测试文件）：授权位、许可证信息、字形覆盖、字宽都可控。"""

from __future__ import annotations

from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

from pacxio.fonts import CJK_SAMPLE

LATIN = " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
UNITS = 1000


def _glyph(width: int):  # type: ignore[no-untyped-def]
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((width - 50, 700))
    pen.lineTo((width - 50, 0))
    pen.closePath()
    return pen.glyph()


def make_font(
    path: Path,
    family: str = "BrandSans",
    *,
    latin_advance: int = 600,
    cjk_advance: int = 1000,
    cjk: bool = False,
    fs_type: int = 0,
    license_description: str | None = "Test license: free for print use",
    license_url: str | None = "https://example.com/license",
    copyright: str | None = "Copyright (c) Test Brand",
    variable: bool = False,
    extra: str = "",
) -> Path:
    chars = LATIN + extra + (CJK_SAMPLE + "山茶花手作点" if cjk else "")
    names = {c: ("space" if c == " " else f"uni{ord(c):04X}") for c in chars}
    order = [".notdef", *dict.fromkeys(names.values())]
    fb = FontBuilder(UNITS, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): n for c, n in names.items()})
    adv = {".notdef": latin_advance}
    glyphs = {".notdef": _glyph(latin_advance)}
    for c, n in names.items():
        w = cjk_advance if ord(c) > 0x2E80 else latin_advance
        adv[n] = w
        glyphs[n] = _glyph(w) if c != " " else TTGlyphPen(None).glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (adv[n], 50) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    strings = {"familyName": family, "styleName": "Regular"}
    if license_description:
        strings["licenseDescription"] = license_description
    if license_url:
        strings["licenseInfoURL"] = license_url
    if copyright:
        strings["copyright"] = copyright
    fb.setupNameTable(strings)
    fb.setupOS2(
        fsType=fs_type, sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200
    )
    fb.setupPost()
    if variable:
        from fontTools.ttLib.tables._f_v_a_r import Axis

        fb.font["name"].setName("Weight", 256, 3, 1, 0x409)
        fvar = newTable("fvar")
        axis = Axis()
        axis.axisTag, axis.minValue, axis.defaultValue, axis.maxValue, axis.axisNameID = (
            "wght",
            100,
            400,
            900,
            256,
        )
        fvar.axes, fvar.instances = [axis], []
        fb.font["fvar"] = fvar
    path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(path))
    return path


def to_woff(src: Path, dest: Path) -> Path:
    f = TTFont(str(src))
    f.flavor = "woff"
    f.save(str(dest))
    return dest


def to_ttc(fonts: list[Path], dest: Path) -> Path:
    from fontTools.ttLib.ttCollection import TTCollection

    c = TTCollection()
    c.fonts = [TTFont(str(p)) for p in fonts]
    c.save(str(dest))
    return dest
