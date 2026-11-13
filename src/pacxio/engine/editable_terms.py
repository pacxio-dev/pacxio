"""可编辑项的设计师说法（2026-11-07 用户：工作台里的术语太专业）。

只是给人看的名字：参数叫什么、属于盒子的哪个部位、算不算常用、每个选项怎么说。代码名（`body.corner_depth`）
照旧用来保存，页面上不显示。没收录的参数退回参数说明的第一句；说法不确定的宁可朴素一点。
"""

from __future__ import annotations

from typing import Any

PART_ORDER = ("顶部", "底部", "盒身", "侧翼", "锁扣", "糊口", "开窗和挂孔", "边角", "其他")

# 参数名 → (说法, 部位)；顶部 / 底部的封口参数部位按所在那一端
TERMS: dict[str, tuple[str, str]] = {
    # 封口
    "flap": ("摇盖长短", "顶部"), "overlap": ("两片盖重叠多少", "顶部"), "depth": ("小翼深", "顶部"),
    "wing": ("翼宽", "顶部"), "drop": ("内折板高", "顶部"), "thumb": ("拇指缺口开在", "顶部"),
    "lock": ("锁舌扣", "顶部"), "end_flaps": ("侧面带摇盖", "顶部"), "glue": ("画粘合区", "顶部"),
    "tuck_len": ("插舌长", "顶部"), "dust_depth": ("防尘翼深", "顶部"), "on": ("盖挂在哪面", "顶部"),
    "tear_strip": ("撕拉条", "顶部"), "autolock_form": ("锁底样式", "底部"), "hanger_layers": ("挂板层数", "顶部"),
    "lid_front": ("盖前沿", "顶部"), "dust": ("盖两侧防尘翼", "顶部"), "dust_lock": ("侧翼插扣（免胶）", "顶部"),
    "tear_window": ("撕开窗", "顶部"), "display_lid": ("展示盖", "顶部"), "dust_ears": ("防尘翼前耳", "顶部"),
    "lid_on": ("盖挂在", "顶部"), "lid_header": ("盖翻起当头卡", "顶部"),
    # 盒身 / 托盘
    "axis": ("盒子横放", "盒身"), "corners": ("四个角怎么连", "边角"), "corner_on": ("角翼挂在", "边角"),
    "corner_depth": ("角翼深", "边角"), "corner_diagonal": ("角翼对角压线", "边角"),
    "wall_diagonal": ("墙上斜压线", "盒身"), "rim": ("墙顶折边宽", "盒身"), "rim_on": ("折边在哪几面", "盒身"),
    "rim_ends": ("折边两头", "盒身"), "stack": ("叠放凸舌", "盒身"), "stack_on": ("凸舌在", "盒身"),
    "end_height": ("端墙高", "盒身"), "end_double": ("端墙双层", "盒身"), "front_double": ("前墙双层", "盒身"),
    "back_double": ("后墙双层", "盒身"), "double_walls": ("双层墙", "盒身"), "inner_ears": ("内墙耳片", "盒身"),
    "inner_wings": ("内墙翼", "盒身"), "lugs": ("插脚", "锁扣"), "lug_count": ("插脚数", "锁扣"),
    "front_low": ("前墙矮", "盒身"), "front_return": ("前墙回折", "盒身"), "corner_meet": ("角上连在一起", "边角"),
    "corner_hooks": ("角上钩扣", "锁扣"), "corner_posts": ("角柱", "边角"), "inside": ("角翼在里面", "边角"),
    "edge_slots": ("墙边插槽", "锁扣"), "end_slits": ("端墙开缝", "盒身"), "bevel": ("切角宽", "边角"),
    "bevel_join": ("切角怎么连", "边角"), "bevel_on": ("切角在", "边角"), "sides": ("边数", "盒身"),
    "crease": ("压线方向", "盒身"), "steps": ("台阶数", "盒身"), "edge": ("边宽", "盒身"),
    # 开窗、挂孔、提手
    "window": ("开窗在", "开窗和挂孔"), "window_shape": ("窗形", "开窗和挂孔"), "tear": ("撕开展示", "开窗和挂孔"),
    "end_window": ("端墙开窗", "开窗和挂孔"), "hand_holes": ("提手孔", "开窗和挂孔"), "base_holes": ("底板孔数", "开窗和挂孔"),
    "hang_hole": ("挂孔样式", "开窗和挂孔"), "hole_w": ("挂孔宽", "开窗和挂孔"), "hole_h": ("挂孔高", "开窗和挂孔"),
    "hole_top": ("挂孔离上边", "开窗和挂孔"), "hole_rise": ("挂孔顶上凸起", "开窗和挂孔"), "hole_d": ("孔径", "开窗和挂孔"),
    "hanger_h": ("挂板高", "顶部"), "hanger_radius": ("挂板圆角", "顶部"), "handle_h": ("手提板高", "顶部"),
    "roof_deg": ("屋顶角度", "顶部"), "lean_deg": ("端翼斜度", "顶部"),
    # 糊口、接头
    "width": ("糊口宽", "糊口"), "offset": ("接头离墙角", "糊口"), "partition": ("接头带隔板", "糊口"),
    "wall": ("接头在哪面墙", "糊口"), "joint_bevel_deg": ("糊口两头斜角", "糊口"), "joint_inset": ("糊口两头让位", "糊口"),
    "glue_w": ("糊口宽", "糊口"),
    # 锁扣、插舌细节（多在“更多”里）
    "friction_lock": ("插舌卡口", "锁扣"), "tuck_slit": ("插舌压线两端", "锁扣"), "tuck_inset": ("插舌两边让位", "锁扣"), "tuck_radius": ("插舌圆角", "锁扣"),
    "dust_offset": ("侧墙让位", "侧翼"), "major_depth": ("锁底主翼深", "底部"), "major_taper_deg": ("锁底主翼斜度", "底部"),
    "minor_depth": ("锁底侧翼深", "底部"), "notch_d": ("扣底缺口深", "底部"), "tongue_len": ("扣底锁舌长", "底部"),
    "tongue_w": ("扣底锁舌宽", "底部"), "lock_step": ("锁扣台阶", "锁扣"),
    "slot_w": ("摇盖之间的缝宽", "侧翼"), "flap_gap": ("两片盖之间的缝", "顶部"),
    # 飞机盒（0427 等）
    "roll_gap": ("卷边让位", "盒身"), "inner_inset": ("内板让位", "盒身"), "ear_offset": ("插耳位置", "锁扣"),
    "ear_depth": ("插耳深", "锁扣"), "ear_gap": ("插耳间隙", "锁扣"), "lug_w": ("插脚宽", "锁扣"),
    "lug_len": ("插脚长", "锁扣"), "slot_depth": ("插槽深", "锁扣"), "lid_flap_depth": ("盖侧翼深", "顶部"),
    "lid_flap_taper_deg": ("盖侧翼斜度", "顶部"), "tuck_ear_len": ("插耳长", "锁扣"), "thumb_notch": ("拇指缺口", "顶部"),
    "corner_radius": ("外角倒圆", "边角"),
    "H": ("高度", "盒身"),  # 多件套里套在外面那件自己的高（盖高……）
    "lid_h": ("盖高", "盒身"), "lid_clearance": ("盖的松量", "盒身"), "sleeve_clearance": ("外套松量", "盒身"),
    "sleeve_len": ("外套长", "盒身"), "notch_r": ("拉手缺口半径", "开窗和挂孔"), "pull": ("拉手", "开窗和挂孔"),
    "notch_on": ("缺口开在", "开窗和挂孔"),
}  # fmt: skip

# 常用的（编辑页默认露出来），其余收在“更多”
COMMON = {
    "H", "lid_h", "lid_clearance", "sleeve_clearance", "sleeve_len", "pull",
    "flap", "overlap", "depth", "wing", "tuck_len", "dust_depth", "lid_front", "dust_lock", "tear_window",
    "display_lid", "tear_strip", "hanger_layers", "autolock_form", "corners", "corner_depth", "window",
    "window_shape", "tear", "hang_hole", "handle_h", "corner_radius", "width", "thumb_notch", "lid_flap_depth",
    "tuck_ear_len", "glue_w",
}  # fmt: skip

# 改结构页“常用调整”里每项下面的一句话：这项改的是盒子上哪儿、改大改小有什么用（设计师看得懂的说法）
EXPLAIN = {
    "tuck_len": "盖子插进盒里的那片舌头有多长，长一点盖得更牢",
    "dust_depth": "盖子两边的小翼伸进盒里多深，挡灰、托住盖子",
    "glue_w": "侧边粘起来的那条边有多宽",
    "lid_flap_depth": "盖子两侧往下折的小翼有多深",
    "tuck_ear_len": "插舌两边的小耳朵有多长，卡住盒子、盖子不弹开",
    "lid_h": "天盖有多高",
    "lid_clearance": "盖子比盒身大多少，大一点好盖、好打开",
    "sleeve_clearance": "外套比里面的盒子大多少，大一点好推拉",
    "sleeve_len": "外套有多长",
    "depth": "这片翼伸进去多深",
    "wing": "侧翼有多宽",
    "corner_depth": "四角折进去的角翼有多深",
    "handle_h": "提手有多高",
    "corner_radius": "盒子外面的角倒多大的圆",
    "width": "这条边有多宽",
    "overlap": "两片摇盖叠在一起多少",
    "H": "这一件有多高",
}

WALLS = {None: "默认", 0: "背面", 1: "左侧面", 2: "正面", 3: "右侧面"}
OPTIONS: dict[str, dict[Any, str]] = {
    "flap": {"half": "两片对口", "overlap": "部分重叠", "full": "四片都全宽", "full_long": "长边全宽、侧边一半",
             "center": "侧边合到中间", "center_overlap": "四片都合到中间", "center_full": "长边全宽、侧边到中间",
             "rim": "窄边框", "rim_long": "长边窄边 + 内板", "rim_end": "侧边窄边 + 内板", "gusset": "三角折页",
             "divider": "带隔板", "cells": "分格", "slot_lock": "插缝自锁", "tongue_slot": "舌插缝", "tongue_notch": "舌嵌缺口"},
    "display_lid": {"none": "不要", "rect": "平边", "tab": "两角圆 + 半圆拉手"},
    "tuck_slit": {"cut": "两端钩形切口", "crease": "上方两段短压线"},
    "lid_front": {"tuck": "插舌插进前墙", "lip": "前翼盖在外面", "return": "前翼 + 回折内衬", "lock": "锁舌插槽",
                  "none": "平边（靠侧扣或展示盖）"},
    "autolock_form": {"hook": "钩齿 + 糊胶三角", "tab": "插舌锁底", "web": "四翼糊胶"},
    "corners": {"flap": "角翼", "glue": "糊角", "gusset": "三角折角", "open": "不连", "ear": "耳片", "bevel": "切角",
                "strip": "长条包裹", "wrap": "包裹", "lock": "扣舌"},
    "corner_on": {"long": "长边", "end": "端边"}, "lid_on": {"back": "后墙", "end": "端墙"},
    "stack": {"none": "不要", "center": "中间一个", "pair": "两个", "corners": "四角"},
    "window": {None: "不开", "front": "正面", "back": "背面"},
    "window_shape": {"octagon": "八角窗", "tap": "出液口", "low": "下部取货口"},
    "hang_hole": {"euro": "欧式孔", "round": "圆孔", "none": "不开"},
    "axis": {"H": "竖放（常规）", "L": "沿长横放", "W": "沿宽横放"},
    "hanger_layers": {None: "默认", 1: "单层", 2: "双层对折"},
    "seam_wall": {"long": "长边墙", "short": "端墙"},
    "rim_on": {"all": "四面", "long": "长边", "end": "端边", "front": "前墙"},
    "rim_ends": {"none": "不处理", "drop": "折下去", "wrap": "包过去"},
    "bevel_join": {"open": "不连", "flap": "翼连", "wrap": "包过去"}, "bevel_on": {"base": "底板", "long": "长边"},
    "double_walls": {"pair": "两面", "all": "四面"}, "stack_on": {"long": "长边", "end": "端边"},
    "crease": {"along_L": "沿长", "along_W": "沿宽", "along_H": "沿高"},
}  # fmt: skip
BOOL = {True: "有", False: "没有", None: "默认"}


def term(path: str, section_kind: str | None = None) -> dict[str, Any]:
    """路径 → {name, part, common}。section_kind：顶 / 底那一端的封口种类（暂未用，留给以后按封口细分说法）。"""
    segs = path.split(".")
    name, section = segs[-1], (segs[-2] if len(segs) > 1 else "")
    plain, part = TERMS.get(name, ("", "其他"))
    if section in ("top", "bottom"):
        part = "顶部" if section == "top" else "底部"
    elif section == "seam":
        part = "糊口"
    out: dict[str, Any] = {"name": plain, "part": part, "common": name in COMMON}
    if name in EXPLAIN:
        out["explain"] = EXPLAIN[name]
    return out


def option_names(path: str, options: list[Any]) -> dict[str, str]:
    """选项值 → 说法（键是 JSON 文本，和页面里一样）。没收录的照原样显示。"""
    import json

    name = path.rsplit(".", 1)[-1]
    table = OPTIONS.get("seam_wall" if path == "seam.wall" else name, {})
    out = {}
    for o in options:
        if name in ("on", "thumb"):
            text = WALLS.get(o, str(o))
        elif isinstance(o, bool) or o is None and not table:
            text = BOOL.get(o, str(o))
        else:
            text = table.get(o, str(o))
        out[json.dumps(o)] = text
    return out


__all__ = ["COMMON", "PART_ORDER", "TERMS", "option_names", "term"]
