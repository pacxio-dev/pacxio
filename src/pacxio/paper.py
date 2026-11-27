"""特种纸 / 特殊基材面色与白墨打底判定（开源公共模块，）。

同时供开源模块（API、刀版、3D 效果图、概念图提示词）和闭源模块（自动白墨打底、预览软打样、
规格书、PP01 印前检查）复用，避免开源代码反向依赖闭源包。
"""

from __future__ import annotations

from pacxio.model.profile import Material, PaperFace

# 牛皮纸 / 牛皮面纸的屏幕示意色（与瓦楞牛皮面纸 LINER_RGB 一致）
KRAFT_RGB: tuple[int, int, int] = (186, 146, 104)

KRAFT_FACE = PaperFace(
    rgb=KRAFT_RGB,
    finish="paper",
    needs_white=False,
    back_rgb=KRAFT_RGB,
    name="Kraft paper",
)

# 既有镀铝标签 / 镀铝复合膜（label_metallized / pouch_pet_vmpet_pe 等）的 3D 与软打样基材外观
METALLIZED_FILM_FACE = PaperFace(
    rgb=(224, 228, 234),
    finish="metallic",
    needs_white=True,
    back_rgb=(224, 228, 234),
    name="Silver metallized film",
)

TEXTURED_LABEL_FACE = PaperFace(
    rgb=(245, 241, 233),
    finish="textured",
    needs_white=False,
    back_rgb=(245, 241, 233),
    name="Textured paper",
)


def paper_face(material: Material) -> PaperFace | None:
    """纸张 / 纸板的纸面属性：不填 face 时常规白卡返回 None，牛皮面纸 / 牛皮卡返回 KRAFT_FACE。

    注意：标签膜材与软包装复合膜（film_laminate / label_stock）有独立的 LB06 / PO04 手动白墨流程，
    除非显式设置了 material.face，否则这里返回 None，不触发彩盒自动白墨打底。
    """
    if material.face is not None:
        return material.face
    if material.liner == "kraft" or material.preset == "kraft_350":
        return KRAFT_FACE
    return None


def display_paper_face(material: Material) -> PaperFace | None:
    """用于 3D 效果图、预览软打样和概念图提示词的基材外观（含既有镀铝 / 牛皮 / 压纹标签与软袋）。"""
    face = paper_face(material)
    if face is not None:
        return face
    if material.film == "metallized":
        return METALLIZED_FILM_FACE
    if material.film == "kraft" or material.preset == "label_kraft":
        return KRAFT_FACE
    if material.preset == "label_textured":
        return TEXTURED_LABEL_FACE
    return None


def paper_rgb(material: Material) -> tuple[int, int, int] | None:
    """屏幕预览 / 3D / 去字补底用的基材本色；白卡 / 透明膜 / 白膜返回 None。"""
    face = display_paper_face(material)
    return face.rgb if face is not None else None


def needs_white(material: Material) -> bool:
    """是否需要自动白墨打底（仅面向彩盒特种纸，避免影响既有标签 LB06 / 软袋 PO04 流程）。"""
    face = paper_face(material)
    return face is not None and face.white_needed()
