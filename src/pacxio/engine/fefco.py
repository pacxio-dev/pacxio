"""FEFCO 编号 ↔ 结构配方（几何引擎第 1 步）。

FEFCO 目录（第 12 版，2022）版权属 FEFCO，不进仓库；这里只记编号和我们自己的结构描述。每款写成配方：
盒身（长方筒身）+ 两端封口积木（engine/slotted.py）。面号同 ECMA：on = 0 是紧挨接头的那面（背面）。
"""

from __future__ import annotations

import math
from typing import Any

# 02 开槽箱（36 款）
SERIES_02: tuple[str, ...] = (
    "0200", "0201", "0202", "0203", "0204", "0205", "0206", "0207", "0208", "0209", "0210", "0211",
    "0212", "0214", "0215", "0216", "0217", "0218", "0219", "0220", "0221", "0222", "0225", "0226",
    "0227", "0228", "0229", "0229.1", "0230", "0231", "0232", "0233", "0233.1", "0240", "0241", "0242",
)  # fmt: skip


def _flaps(rule: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "flaps", "flap": rule, **extra}


OPEN: dict[str, Any] = {"kind": "open"}
SNAP: dict[str, Any] = {"kind": "snaplock", "on": 2}  # 锁舌翼在正面（0215 的样子）
LID: dict[str, Any] = {"kind": "lid_tuck", "on": 0}  # 插舌盖挂背面（0210 的样子）
AUTO: dict[str, Any] = {"kind": "autolock"}  # 自动锁底（0711 的钩齿样子）
AUTO_TAB: dict[str, Any] = {"kind": "autolock", "autolock_form": "tab"}
AUTO_WEB: dict[str, Any] = {"kind": "autolock", "autolock_form": "web"}

# 编号 → (顶, 底, 一句话结构说明)
STYLES: dict[str, tuple[dict[str, Any], dict[str, Any], str]] = {
    "0200": (OPEN, _flaps("half"), "开顶，底部四片 ½W 摇盖"),
    "0201": (_flaps("half"), _flaps("half"), "普通开槽箱：四片摇盖都是 ½W，外摇盖合在中间"),
    "0202": (_flaps("overlap"), _flaps("overlap"), "外摇盖部分重叠"),
    "0203": (_flaps("full"), _flaps("full"), "摇盖全重叠（四片都是 W）"),
    "0204": (_flaps("center"), _flaps("center"), "长边 ½W、端边 ½L，四片都合到中心"),
    "0205": (_flaps("center_overlap"), _flaps("center_overlap"), "四片都是 ½L：端边对口、长边重叠"),
    "0206": (_flaps("center_full"), _flaps("center_full"), "长边全叠（W）、端边 ½L 对口"),
    "0207": (
        _flaps("half"),
        _flaps("cells"),
        "顶部 ½W；分格底：端摇盖深 L/3、头上横隔板，长边摇盖 ½W、头上两段折成纵隔板，分成 2 × 3 格",
    ),
    "0208": (
        _flaps("divider"),
        _flaps("divider"),
        "四片 ½W 摇盖头上各接一段隔板（最多 ½H）折进箱子：长边两片成纵隔板，端摇盖对开两半成横隔板",
    ),
    "0209": (_flaps("rim"), _flaps("half"), "顶部四片窄边平铺成框（开窗顶），底部 ½W"),
    "0210": (LID, LID, "上下两个插舌盖挂在同一面，端边带防尘翼，对面长边不带翼"),
    "0211": (LID, {"kind": "lid_tuck", "on": 2}, "上下两个插舌盖挂在相对的两面"),
    "0212": (LID, _flaps("half"), "顶部插舌盖，底部 ½W 摇盖"),
    "0214": (
        _flaps("rim", fold="down"),
        _flaps("half"),
        "顶部四片窄边折下去贴墙（加强口沿），底部 ½W",
    ),
    "0215": (LID, SNAP, "顶部插舌盖，扣底"),
    "0216": (_flaps("half"), SNAP, "顶部 ½W 摇盖，扣底"),
    "0217": ({"kind": "gable"}, SNAP, "提手山形顶（长边屋顶 + 手提板，端边带缝三角端翼），扣底"),
    "0219": (
        {"kind": "display", "on": 2},
        SNAP,
        "背面摇盖整片立起当展示板（挂槽、顶上小舌往后折）；正面一整片盖合上，远边窄条立起贴展示板；扣底",
    ),
    "0220": ({"kind": "snaplock", "on": 2}, SNAP, "上下都是扣底式"),
    "0232": (
        _flaps("half"),
        _flaps("slot_lock"),
        "顶部 ½W；斜缝自锁底：长边两片 ½W 各开两道斜缝，端摇盖 ¼W 处压线、外段的角顶进斜缝",
    ),
    "0233": (
        _flaps("half"),
        _flaps("tongue_slot"),
        "顶部 ½W；舌头穿缝自锁底：背面那片伸出舌头（两角斜压线），正面那片中间 U 形缝，舌头从缝下穿过",
    ),
    "0233.1": (
        _flaps("half"),
        _flaps("tongue_notch"),
        "顶部 ½W；舌头嵌缺口自锁底：背面那片伸出舌头，正面那片远边中间缺口，舌头平嵌进去",
    ),
    "0240": (
        {"kind": "hanger", "on": 0},
        SNAP,
        "背面加高双层挂板（带挂孔）+ 正面插舌盖 + 防尘翼，扣底",
    ),
    "0221": (OPEN, SNAP, "开顶，扣底"),
    "0218": (
        _flaps("full", lock=True),
        _flaps("full"),
        "全重叠摇盖；顶部外摇盖收成带耳的锁舌，里面那片开两道斜缝，锁舌两角插进缝里",
    ),
    "0241": (
        {"kind": "hang_tab", "on": 2},
        SNAP,
        "正面一整片盖（深 W、长度中间一道缝），背面不带摇盖；两端摇盖合到长度中间，"
        "头上挂板（挂孔）立起来背靠背从缝里穿出；扣底",
    ),
    "0242": (
        {"kind": "hang_mid", "on": 2},
        SNAP,
        "背面摇盖合到中间、头上两块挂板对折立起（挂孔对齐）再接一块平铺盖住前半边；"
        "正面一整片盖从缝里套过挂板、头上插舌插进背面摇盖根部的缝；端摇盖梯形带缺口；扣底",
    ),
    "0225": (
        _flaps("half"),
        _flaps("full", end_flaps=False, wing="half", thumb="opposite"),
        "顶部 ½W；底部只有两片 W 长摇盖：背面那片先合（远边拇指缺口），"
        "正面那片两侧带 ½W 宽的翼，合上后翼立起来贴端墙里面",
    ),
    "0227": (
        _flaps("full", wing="narrow", thumb="on"),
        _flaps("full", wing="narrow", thumb="on"),
        "全重叠摇盖：正面那片两侧带窄翼（两头 45° 斜角）、远边拇指缺口，先合，翼贴端墙里面；"
        "端摇盖让出一个翼宽；背面那片最外",
    ),
    "0230": (
        _flaps("half"),
        _flaps("half"),
        "同 0201，但接头不在墙角：开在长边墙上离墙角 v，两截搭接",
    ),
    "0231": (
        _flaps("half"),
        _flaps("half"),
        "同 0201，但接头开在端墙上离墙角 v，两截搭接",
    ),
    "0222": (
        {"kind": "handle"},
        SNAP,
        "提手平顶：长边两片平铺到中间、提手板在中间立起来背靠背；端墙摇盖对开两片合到长度中间，"
        "头上插舌插进长边那片中间的缝；扣底",
    ),
    "0226": (
        _flaps("gusset"),
        _flaps("gusset"),
        "不开槽，四片摇盖深都是 ½W：端摇盖两条斜压线分出中间三角和两侧小三角折页，"
        "合上时小三角折 180° 夹在中间三角和长摇盖之间",
    ),
    "0228": (
        _flaps("half"),
        _flaps("half"),
        "接头在长边墙正中（两截各 ½(L + o) 搭接），第一截外面多一块隔板折进去把箱子分成两格，"
        "隔板头上糊口粘在对面墙里面",
    ),
    "0229": (_flaps("rim_long"), _flaps("rim_long"), "长边窄边 + 往下折的内板，端边 ½L 盖在上面"),
    "0229.1": (_flaps("rim_end"), _flaps("rim_end"), "端边窄边 + 内板，长边 ½W 盖在上面"),
    # ---- 07 预糊箱（第 3 步）：自动锁底 × 顶；hook = 0711 的钩齿锁底，tab / web 按示意图推读 ----
    "0700": (OPEN, AUTO, "开顶，自动锁底（主翼钩齿 + 糊胶三角）"),
    "0710": (_flaps("full"), AUTO, "顶部全重叠摇盖，自动锁底"),
    "0711": (_flaps("half"), AUTO, "顶部 ½W 摇盖，自动锁底（同 crashlock_rsc）"),
    "0712": (_flaps("center"), AUTO, "顶部长边 ½W、端边 ½L 合到中心，自动锁底"),
    "0713": (LID, AUTO, "顶部插舌盖（挂背面），自动锁底"),
    "0701": (
        _flaps("half"),
        AUTO_TAB,
        "顶部 ½W；插舌锁底：正面长翼整幅深、插舌插进背面墙脚的缝，端墙翼带糊胶三角——按示意图推读",
    ),
    "0702": (OPEN, AUTO_TAB, "开顶，插舌锁底——按示意图推读"),
    "0703": (_flaps("full"), AUTO_TAB, "顶部全重叠，插舌锁底——按示意图推读"),
    "0704": (_flaps("center"), AUTO_TAB, "顶部合到中心，插舌锁底——按示意图推读"),
    "0705": (LID, AUTO_TAB, "顶部插舌盖，插舌锁底——按示意图推读"),
    "0706": (
        OPEN,
        AUTO_WEB,
        "开顶；糊胶三角锁底：四片约 ½W 翼，长翼过角 45° 糊胶三角粘在端墙翼上，无钩齿"
        "（目录图端墙脚还有插缝，未画）——按示意图推读",
    ),
    "0707": (_flaps("full"), AUTO_WEB, "顶部全重叠，糊胶三角锁底——按示意图推读"),
    "0708": (_flaps("half"), AUTO_WEB, "顶部 ½W，糊胶三角锁底——按示意图推读"),
    "0709": (_flaps("center"), AUTO_WEB, "顶部合到中心，糊胶三角锁底——按示意图推读"),
    "0719": (
        LID,
        AUTO_WEB,
        "顶部插舌盖（挂背面），糊胶三角锁底（目录图端墙上还有小插缝，未画）——按示意图推读",
    ),
    "0720": (
        {"kind": "lid_tuck", "on": 2},
        AUTO_TAB,
        "顶部插舌盖挂正面（目录图盖头另有锁扣舌和三道插缝，未画），插舌锁底——按示意图推读",
    ),
    "0721": (
        {"kind": "hanger", "on": 0},
        AUTO_WEB,
        "背面加高双层挂板（挂孔）+ 正面插舌盖，糊胶三角锁底（目录图挂板在盖头上，这里按 0240 的挂板表达）"
        "——按示意图推读，待核对",
    ),
    "0722": (AUTO, AUTO, "两头都是自动锁底，正面开八角窗——按示意图推读"),
    "0728": (
        _flaps("half"),
        AUTO,
        "顶部 ½W、自动锁底，端墙 side_a 离底 v 一道齿线、两面长墙斜齿线，撕开变展示托盘"
        "（目录图另有后墙加高头卡，未画）——按示意图推读，待核对",
    ),
    "0740": (
        _flaps("half"),
        AUTO,
        "盒中袋外箱：顶部 ½W、自动锁底，背面墙上齿线圆出液口 + 齿线提片（目录图顶摇盖带锁舌，未画）"
        "——按示意图推读，待核对",
    ),
    # ---- 09 内衬里的套筒（第 3 步）----
    "0913": (
        OPEN,
        OPEN,
        "内套：四面墙围一圈、接头开在长墙正中（两头 ½L），不粘（目录图上下还有窄边折平，未画）"
        "——按示意图推读",
    ),
    # ---- 08 展示（第 4 步）——按示意图推读，待核对 ----
    "0801": (
        {"kind": "slope"},
        AUTO,
        "斜口陈列箱：开顶，正面墙矮到 v、端墙上沿斜，背面整高；自动锁底",
    ),
    "0841": (
        {"kind": "slope"},
        _flaps("half"),
        "落地斜口陈列箱：同 0801，底部 ½W 摇盖（目录图两侧弧形，按直线斜边）",
    ),
    "0866": (
        {"kind": "slope"},
        AUTO_TAB,
        "斜口陈列箱带插舌锁底（目录图另有背面头卡和底部叉车脚口，未画）",
    ),
    "0818": (
        _flaps("half"),
        _flaps("half"),
        "运输箱改展示：½W 摇盖，齿线撕开端墙 + 长墙斜线变展示托盘（目录图顶摇盖上也有齿线，未画）",
    ),
    "0819": (_flaps("center"), _flaps("half"), "运输箱改展示：顶部合到中心，齿线撕开变展示托盘"),
    "0825": (
        LID,
        AUTO,
        "取货盒：顶部插舌盖、自动锁底，正面墙下部开取货口（目录图口外还有一块弧形托板，未画）",
    ),
    "0865": (
        OPEN,
        _flaps("half"),
        "开顶陈列箱，正面墙开窗（目录图是从上沿开的 U 口、底边叉车脚口，按八角窗表达）",
    ),
    "0886": (
        {"kind": "lid_tuck", "on": 0, "tear_strip": True},
        AUTO_WEB,
        "电商箱：插舌盖挂背面、盖上撕拉条（目录图另有自封胶条，未画），糊胶三角锁底",
    ),
    "0887": (
        {"kind": "lid_tuck", "on": 0, "tear_strip": True},
        _flaps("half"),
        "电商箱：插舌盖带撕拉条（自封胶条未画），底部 ½W 摇盖",
    ),
}
# 07 / 09 开槽箱墙上的刀口选项（编号 → 盒身字段）
STYLE_BODY: dict[str, dict[str, Any]] = {
    "0722": {"window": "front"},
    "0728": {"tear": True},
    "0740": {"window": "back", "window_shape": "tap"},
    "0818": {"tear": True},
    "0819": {"tear": True},
    "0825": {"window": "front", "window_shape": "low"},
    "0865": {"window": "front"},
}


# 01 卷材、片材、简单包覆（11 款）：一张板 + 平行折线。L / W / H 的意思：折线方向上的长、槽底宽、槽边高。
SERIES_01: tuple[str, ...] = (
    "0100",
    "0110",
    "0111",
    "0112",
    "0113",
    "0119",
    "0121",
    "0122",
    "0123",
    "0129",
    "0130",
)
LIP_H_RATIO, LIP_CAP_MM = 0.5, 30.0  # 卷边 = min(0.5·H, 30)【初值】
FANFOLD_COUNT = 6  # 0130 连续折叠板的页数【初值】
SHEETS: dict[str, str] = {
    "0100": "卷材（单面瓦楞卷），刀版是展开的一段",
    "0110": "平板",
    "0111": "一道折线（顺 L）：L 形护角",
    "0112": "两道折线（顺 L）：U 形槽",
    "0113": "三道折线（顺 L）：U 形槽 + 一边往里的卷边",
    "0119": "四道折线（顺 L）：U 形槽 + 一边卷边、另一边往外的翻边",
    "0121": "一道折线（顺 W）：L 形",
    "0122": "两道折线（顺 W）：U 形槽",
    "0123": "三道折线（顺 W）：U 形槽 + 卷边",
    "0129": "四道折线（顺 W）：U 形槽 + 卷边 + 翻边",
    "0130": "连续折叠板：等距折线一正一反对折，叠成一摞",
}


def _ins(
    crease: str, panels: list[str], folds: list[float] | None = None, **extra: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {"crease": f"along_{crease}", "panels": panels, **extra}
    if folds is not None:
        out["folds"] = folds
    return out


# 07 / 09 里一张板 + 平行折线的内衬（第 3 步）：条宽写成盒子内尺寸的式子（L / W / H / v），改尺寸跟着变。
# 横截面按示意图推读、待核对（尤其 0939–0948 的管形、0943 的锯齿、0967、0973–0976 的护角层数）
INSERTS: dict[str, tuple[dict[str, Any], str]] = {
    "0900": (_ins("W", ["v"]), "垫条：宽 v、长 W 的平板"),
    "0901": (_ins("L", ["W"]), "垫板：L × W 平板（平放）"),
    "0902": (_ins("H", ["W"]), "隔板：W × H 平板（横着立）"),
    "0903": (_ins("H", ["L"]), "隔板：L × H 平板（顺着立）"),
    "0904": (
        _ins("H", ["W", "L", "W", "L"], [90, 90, 90]),
        "内套：W、L、W、L 四面围一圈（立着，不粘）",
    ),
    "0905": (_ins("W", ["H", "L", "H", "L"], [90, 90, 90]), "内套：H、L、H、L 横着围（筒长 W）"),
    "0906": (_ins("L", ["W", "H", "W", "H"], [90, 90, 90]), "内套：W、H、W、H 横着围（筒长 L）"),
    "0907": (
        _ins("W", ["L/2", "H", "L", "H", "L/2"], [90, 90, 90, 90]),
        "内套：顶面中间对开（L/2 + L/2），筒长 W",
    ),
    "0908": (
        _ins("H", ["L/2", "W", "L", "W", "L/2"], [90, 90, 90, 90]),
        "内套：立着围一圈，接头在长墙正中",
    ),
    "0909": (
        _ins("W", ["H/2", "L", "H", "L", "H/2"], [90, 90, 90, 90]),
        "内套：端面中间对开（H/2 + H/2），筒长 W",
    ),
    "0910": (
        _ins("L", ["W/2", "H", "W", "H", "W/2"], [90, 90, 90, 90]),
        "内套：顶面中间对开（W/2 + W/2），筒长 L",
    ),
    "0914": (
        _ins("L", ["H", "W", "H", "W", "H"], [90, 90, 90, 90]),
        "内套：H、W、H、W、H，首尾两块 H 重叠",
    ),
    "0920": (
        _ins("H", ["W", "L/2", "W", "L", "W", "L/2", "W"], [90] * 6),
        "内套带中隔：围一圈后首尾两块 W 折进中间背靠背成隔板，分两格",
    ),
    "0921": (
        _ins("H", ["L/2", "W", "L/2", "W", "L/2", "W", "L/2"], [90, 90, 90, -90, -90, -90]),
        "S 形内衬：一条折成两个相连的格子——按示意图推读",
    ),
    "0936": (
        _ins("H", ["v", "W", "W", "v"], [90, 180, -90]),
        "双层横隔板：对折两层、两头 v 脚折开立稳",
    ),
    "0937": (_ins("H", ["v", "L", "L", "v"], [90, 180, -90]), "双层纵隔板：对折两层、两头 v 脚"),
    "0938": (
        _ins("W", ["v", "H", "H", "v"], [90, 180, -90]),
        "双层立板（顺 W）：对折两层、v 脚贴底",
    ),
    "0939": (
        _ins("H", ["W/2"] * 8, [90, 90, -90, -90, 90, 90, -90]),
        "方波隔板：一条按 W/2 折成一排交替开口的方格（目录图是带开孔的整片格子，L ≈ 2.5W）——按示意图推读",
    ),
    "0940": (
        _ins("L", ["H", "W/2", "H", "W", "H", "W/2", "H"], [90] * 6),
        "顶板 + 两头往里卷成两个方管，把宽度分成两格——按示意图推读",
    ),
    "0941": (
        _ins("L", ["H", "H", "H", "H", "W", "H", "H", "H", "H"], [90] * 8),
        "顶板两头各卷一个封闭方管（H × H），中间悬空——按示意图推读",
    ),
    "0942": (
        _ins("L", ["1.414H", "H", "H", "W", "H", "H", "1.414H"], [135, 90, 90, 90, 90, 135]),
        "顶板两头各卷一个方管、管里一道斜撑——按示意图推读",
    ),
    "0943": (
        _ins("L", ["H", "H", "H", "W", "H", "H", "H"], [-150, 150, 105, 105, 150, -150]),
        "顶板两头各折一组锯齿（W 形）缓冲——按示意图推读，待核对",
    ),
    "0944": (
        _ins("L", ["H", "H", "W", "H", "H"], [120] * 4),
        "顶板两头各折成一个三角管（顶板那头当一边）——按示意图推读",
    ),
    "0945": (
        _ins("L", ["1.414H", "H", "W", "H", "1.414H"], [135, 90, 90, 135]),
        "顶板两头各折下一面墙 + 一条斜撑回到顶板（三角）——按示意图推读",
    ),
    "0946": (
        _ins("L", ["H", "H", "H", "W", "H", "H", "H"], [90] * 6),
        "顶板两头各卷一个三面方管——按示意图推读",
    ),
    "0947": (
        _ins("L", ["H", "H", "H", "H", "v", "H", "H", "H", "H"], [90] * 8),
        "中间窄条 v、两头各一个封闭方管——按示意图推读",
    ),
    "0948": (
        _ins(
            "L",
            ["1.414H", "H", "H", "H", "v", "H", "H", "H", "1.414H"],
            [
                135,
                90,
                90,
                -90,
                -90,
                90,
                90,
                135,
            ],  # 接窄条的两道反折：两根管立在窄条两头（原来全同向，卷成一圈撞自己）
        ),
        "中间窄条 v、两头方管里带斜撑——按示意图推读",
    ),
    "0949": (_ins("L", ["v", "W", "v"], [180, 180]), "垫板两边各翻折一条 v 压平（加厚边）"),
    "0950": (
        _ins("L", ["v", "v", "W", "v", "v"], [-180, 180, 180, -180]),
        "垫板两边各 Z 形折两层 v 压平——按示意图推读",
    ),
    "0951": (_ins("L", ["H", "W", "H"], [90, 90]), "U 形内衬：W 底 + 两面 H 墙（筒长 L）"),
    "0952": (
        _ins("W", ["H", "L", "H"], [90, 90], holes={"panel": 1, "count": 2}),
        "倒 U 形瓶托：L 顶板开两个瓶口孔、两面 H 腿（目录图四角圆角，未画）",
    ),
    "0953": (
        _ins("L", ["H", "W", "H"], [90, 90], holes={"panel": 1, "count": 2}),
        "倒 U 形瓶托（顺 L）：W 顶板开孔、两面 H 腿（目录图一头另有端板，未画）——按示意图推读",
    ),
    "0965": (_ins("L", ["W"] * 5, [180, -180, 180, -180]), "连续折叠垫：五片 W 一正一反叠成一摞"),
    "0966": (
        _ins("L", ["W"] * 5, [180, 180, 180, 180]),
        "卷叠垫：五片 W 同向卷叠成一摞——按示意图推读",
    ),
    "0967": (
        _ins("L", ["v"] * 6 + ["W/2", "W", "W/2"], [180, -180, 180, -180, 180, 90, 180, 180]),
        "叠垫 + 包边：一头六条 v 叠成垫块，其余 W/2、W、W/2 包住——按示意图推读，待核对",
    ),
    "0970": (_ins("L", ["v", "v"], [90]), "护角：两条 v 折成 90° 角条"),
    "0971": (
        _ins("L", ["v", "v", "v", "v"], [180, 90, 180]),
        "双层护角：两边各回折一层（层在里面）",
    ),
    "0972": (
        _ins("L", ["v", "v", "v", "v"], [-180, 90, -180]),
        "双层护角：两边各回折一层（层在外面）",
    ),
    "0973": (
        _ins("L", ["v", "1.414v", "v", "v", "1.414v", "v"], [135, 135, 180, 135, 135]),
        "三角护角：底边 + 两斜边，中间双层立柱——按示意图推读，待核对",
    ),
    "0974": (
        _ins("L", ["v", "1.414v", "1.414v", "v"], [135, 90, 135]),
        "三角护角：两斜边 + 两条底脚往里——按示意图推读",
    ),
    "0975": (
        _ins("L", ["v", "v", "1.414v", "1.414v", "v", "v"], [180, 135, 90, 135, 180]),
        "双层三角护角：两斜边 + 底脚回折双层——按示意图推读，待核对",
    ),
    "0976": (
        _ins("L", ["v", "v", "v", "v", "v", "v"], [180, 180, 90, 180, 180]),
        "三层护角：两边各卷两层——按示意图推读，待核对",
    ),
    "0752": (
        _ins("L", ["W", "H", "W"], [90, 90], holes={"panel": 0, "count": 6}),
        "罐装套：W、H、W 三面 U 形套，一面开一排孔看罐（目录图是两面波浪形窗口 + 顶上插舌，未画）——按示意图推读",
    ),
    # ---- 08 头卡（第 4 步）----
    "0880": (_ins("L", ["H", "H"], [180]), "双层头卡：两片 L × H 对折（目录图下沿插脚，未画）"),
    "0881": (
        _ins("H", ["L"], slots={"count": 2, "edge": "bottom", "depth": 0.15}),
        "单层头卡：L × H 平板，下沿两道短缝插在箱子上（目录图四角圆角，未画）",
    ),
    "0882": (
        _ins("H", ["W", "L", "W"], [90, 90], slots={"count": 2, "edge": "bottom", "depth": 0.15}),
        "U 形头卡：L 面 + 两侧 W 翼，下沿两道短缝（目录图两翼斜边、下沿插脚，未画）",
    ),
}
# 引擎原生的多边形筒（第 3 步）：编号 → (边数, 两端, 说明)；对边距 = L
NATIVE: dict[str, tuple[int, dict[str, Any], dict[str, Any], str]] = {
    "0730": (
        6,
        {"kind": "flaps"},
        {"kind": "flaps"},
        "六角筒：正六边形筒身，两头每面一片合到中心的扇形翼（目录图底部是六角自动锁底）"
        "——按示意图推读，待核对",
    ),
    # ---- 08 多边形陈列桶（第 4 步）——按示意图推读，待核对 ----
    "0850": (
        3,
        {"kind": "flaps"},
        {"kind": "flaps"},
        "三角柱展示：正三角形筒身，两头扇形翼（目录图边数可改）",
    ),
    "0852": (
        3,
        {"kind": "open"},
        {"kind": "lid_glue"},
        "三角立牌：三角形筒身，开顶、底部粘翼（目录图是躺着的三角棱柱立牌）",
    ),
    "0851": (
        4,
        {"kind": "open"},
        {"kind": "flaps"},
        "方口陈列桶（目录图四面是扭转的三角面，按正方筒表达）",
    ),
    "0861": (
        4,
        {"kind": "open"},
        {"kind": "lid_glue"},
        "方口陈列桶，底部粘翼（目录图四面扭转三角面，按正方筒表达）",
    ),
    "0858": (
        8,
        {"kind": "open"},
        {"kind": "flaps"},
        "八角陈列桶：开顶，底部扇形翼（目录图墙脚叉车口、顶边插舌，未画）",
    ),
    "0859": (
        8,
        {"kind": "open"},
        {"kind": "lid_glue"},
        "八角陈列桶：开顶，底部粘翼（目录图边数可改）",
    ),
    "0860": (
        6,
        {"kind": "open"},
        {"kind": "flaps"},
        "六角陈列桶：开顶，底部扇形翼（目录图另有六角底托，未画）",
    ),
}
SERIES_06: tuple[str, ...] = (
    "0601", "0602", "0605", "0606", "0607", "0608", "0610", "0615", "0616", "0620", "0621",
)  # fmt: skip
SERIES_08: tuple[str, ...] = (
    "0801", "0802", "0803", "0804", "0808", "0809", "0815", "0816", "0817", "0818", "0819", "0825", "0826",
    "0830", "0831", "0832", "0833", "0834", "0840", "0841", "0842", "0843", "0845", "0846", "0847", "0848",
    "0849", "0850", "0851", "0852", "0853", "0854", "0858", "0859", "0860", "0861", "0865", "0866", "0870",
    "0871", "0872", "0873", "0874", "0880", "0881", "0882", "0885", "0886", "0887",
)  # fmt: skip
SERIES_07: tuple[str, ...] = (
    "0700", "0701", "0702", "0703", "0704", "0705", "0706", "0707", "0708", "0709", "0710", "0711", "0712",
    "0713", "0714", "0715", "0716", "0717", "0718", "0719", "0720", "0721", "0722", "0723", "0724", "0725",
    "0726", "0727", "0728", "0730", "0740", "0747", "0748", "0751", "0752", "0759", "0761", "0770", "0771",
    "0772", "0773", "0774",
)  # fmt: skip
SERIES_09: tuple[str, ...] = (
    "0900", "0901", "0902", "0903", "0904", "0905", "0906", "0907", "0908", "0909", "0910", "0911", "0912",
    "0913", "0914", "0920", "0921", "0929", "0930", "0931", "0932", "0933", "0934", "0935", "0936", "0937",
    "0938", "0939", "0940", "0941", "0942", "0943", "0944", "0945", "0946", "0947", "0948", "0949", "0950",
    "0951", "0952", "0953", "0954", "0955", "0960", "0965", "0966", "0967", "0970", "0971", "0972", "0973",
    "0974", "0975", "0976",
)  # fmt: skip


# 04 折叠盒和托盘（86 款，第 2 步）
SERIES_04: tuple[str, ...] = (
    "0400",
    "0401",
    "0402",
    "0403",
    "0404",
    "0405",
    "0406",
    "0407",
    "0409",
    "0410",
    "0411",
    "0412",
    "0413",
    "0414",
    "0415",
    "0416",
    "0418",
    "0420",
    "0421",
    "0422",
    "0423",
    "0424",
    "0425",
    "0425.1",
    "0426",
    "0427",
    "0427.1",
    "0428",
    "0429",
    "0430",
    "0431",
    "0432",
    "0433",
    "0434",
    "0435",
    "0435.1",
    "0436",
    "0436.1",
    "0436.2",
    "0437",
    "0438",
    "0439",
    "0440",
    "0441",
    "0442",
    "0443",
    "0444",
    "0445",
    "0446",
    "0447",
    "0448",
    "0449",
    "0449.1",
    "0450",
    "0451",
    "0451.1",
    "0452",
    "0453",
    "0454",
    "0455",
    "0456",
    "0457",
    "0458",
    "0459",
    "0459.1",
    "0460",
    "0461",
    "0462",
    "0463",
    "0464",
    "0465",
    "0466",
    "0469",
    "0470",
    "0471",
    "0472",
    "0473",
    "0474",
    "0475",
    "0476",
    "0480",
    "0481",
    "0482",
    "0483",
    "0484",
    "0485",
)
# 编号 → (盒身托盘的配方字段, 一句话结构说明)；托盘开顶
TRAYS: dict[str, tuple[dict[str, Any], str]] = {
    "0424": ({"corners": "ear", "corner_on": "long", "inner_ears": True},
             "双层长墙回折内耳托盘——按示意图推读，待核对"),
    "0421": ({"corners": "ear", "lid": {"kind": "hinged_lid", "dust": False}},
             "连盖托盘：0422 型双层端墙（内墙插脚插进底板插孔）+ 耳朵，盖挂后墙（长边）、远边插舌插进前墙，"
             "盖不带防尘翼"),
    "0401": ({"corners": "open", "lid": {"kind": "flaps", "flap": "half"}},
             "折叠盒：四壁顶端折翼，长边半宽对口、端边小翼——按示意图推读，待核对"),
    "0402": ({"corners": "open", "lid": {"kind": "flaps", "flap": "center"}},
             "折叠盒：四壁顶端折翼，长边半宽对口、端边半长对口——按示意图推读，待核对"),
    "0407": ({"corners": "open", "lid": {"kind": "flaps", "flap": "full"}},
             "折叠盒：长边双全幅盖重叠、两端墙回折成双层——按示意图推读，待核对"),
    "0454": ({"corners": "ear", "ear_shape": "full", "lugs": False},
             "托盘：双层端墙夹住整幅角翼，无底插脚——按示意图推读，待核对"),
    "0455": ({"corners": "ear", "ear_shape": "full", "corner_on": "long", "lugs": False},
             "托盘：双层长边墙夹住整幅角翼，无底插脚——按示意图推读，待核对"),
    "0456": ({"corners": "ear", "ear_shape": "full", "double_walls": "all", "lugs": False},
             "托盘：四面双层墙，整幅角翼夹入墙角，无底插脚——按示意图推读，待核对"),
    "0425": ({"corners": "ear", "double_walls": "all", "inner_ears": True},
             "四面双层托盘，新增内墙也带耳片，四面插脚入底槽——按示意图推读，待核对"),
    "0425.1": ({"corners": "ear", "double_walls": "all"},
               "四面双层托盘，仅外墙带夹角耳片，四面插脚入底槽——按示意图推读，待核对"),
    "0420": ({"corners": "flap", "corner_on": "long", "rim": "narrow", "rim_on": "end",
              "lid": {"kind": "hinged_lid", "dust": False}},
             "连盖托盘：长边墙两头角翼贴端墙里面，端墙顶一条窄边，盖挂后墙、远边插舌插进前墙"),
    "0423": ({"corners": "ear", "corner_on": "long"},
             "托盘，长边墙双层（卷边 + 内墙，内墙插脚插进底板），端墙两头耳朵折进长边墙夹层（0422 转 90°）"),
    "0428": ({"corners": "ear", "lugs": False, "inner_foot": True, "lid": {"kind": "hinged_lid", "dust": False}},
             "连盖托盘：同 0421，但端墙内层下沿是一条平压在底板上的折边（不插脚、底板不开孔）"
             "——按示意图推读，待核对"),
    "0429": ({"corners": "ear", "lid": {"kind": "hinged_lid", "tuck": "full"}},
             "飞机盒类：0422 型双层端墙 + 耳朵，盖带防尘翼，远边插舌（墙高）插进前墙"),
    "0431": ({"corners": "flap", "corner_on": "long", "rim": "narrow", "rim_ends": "lugs"},
             "托盘：长边墙两头角翼贴端墙里面，四面墙顶窄边成框；长边窄边两头接立柱折下贴端墙，"
             "立柱下端插脚穿出端墙脚的槽锁住——按示意图推读，待核对"),
    "0432": ({"corners": "ear", "rim": "narrow", "rim_on": "long", "rim_ends": "tabs", "inner_wings": True},
             "托盘：0422 型双层端墙 + 耳朵，长边墙顶窄边两头的插舌插进端墙卷边上的槽，内墙两侧翼贴长边墙"
             "——按示意图推读，待核对"),
    "0433": ({"corners": "flap", "corner_on": "long", "rim": "narrow", "rim_on": "long", "rim_ends": "posts",
              "triangle_posts": True},
             "托盘：端墙卷边成双层、内层两头折成三角角柱；长边墙角翼贴端墙里面，长边窄边两头立柱插进角柱"
             "——按示意图推读，待核对"),
    "0434": ({"corners": "flap", "corner_on": "long", "corner_side": "out", "corner_meet": True, "rim": "narrow",
              "rim_on": "long", "rim_ends": "mitre"},
             "托盘：长边墙两头角翼各盖半个端墙外面，长边墙顶窄边绕过墙角斜接到端墙顶上——按示意图推读，待核对"),
    "0451.1": ({"corners": "flap", "corner_on": "end", "wall_diagonal": True, "edge_slots": True},
               "托盘：0451 + 底板四边各两个底边孔——按示意图推读，待核对"),
    "0435": ({"corners": "ear", "lug_count": 1, "stack": "center"},
             "水果箱托盘：0422 型双层端墙 + 耳朵，内墙中间一个插脚；端墙卷边上留一块堆叠凸舌竖着冒出墙顶，"
             "底板端线中间开孔接上一层的凸舌——按示意图推读，待核对"),
    "0435.1": ({"corners": "ear", "stack": "center"},
               "水果箱托盘：同 0435，内墙两个插脚、下沿中间让出缺口给下一层的凸舌——按示意图推读，待核对"),
    "0436": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "long", "rim_ends": "drop",
              "stack": "center"},
             "水果箱托盘：端墙两头角翼贴长边墙里面，长边墙顶窄边两头短舌折下贴端墙，端墙顶中间堆叠凸舌、"
             "底板端线开孔——按示意图推读，待核对"),
    "0436.1": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "end", "stack": "center"},
               "水果箱托盘：端墙两头角翼贴长边墙里面，端墙顶窄边，中间一块堆叠凸舌、底板端线开孔"
               "（长边墙中段加高的轮廓未画）——按示意图推读，待核对"),
    "0436.2": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "end", "stack": "pair"},
               "水果箱托盘：同 0436.1，端墙顶两块堆叠凸舌——按示意图推读，待核对"),
    "0484": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "end", "stack": "corners"},
             "水果箱托盘：端墙两头角翼贴长边墙里面，端墙顶窄边，靠墙角两块堆叠凸舌、底板端线靠角开孔"
             "——按示意图推读，待核对"),
    "0480": ({"corners": "flap", "corner_on": "end", "corner_side": "out", "rim": "narrow", "rim_on": "end",
              "rim_ends": "wrap"},
             "托盘：端墙两头角翼贴在长边墙外面，端墙顶窄边两头再接一段折下压住角翼——按示意图推读，待核对"),
    "0481": ({"corners": "flap", "corner_on": "long", "rim": "narrow", "rim_on": "long", "rim_ends": "posts",
              "stack": "pair", "stack_on": "long", "end_height": "half", "end_double": True},
             "水果箱托盘：长边墙高、顶上窄边两头立柱垂到矮端墙顶，长边墙两块堆叠凸舌；端墙矮（v2 默认半个墙高）、"
             "卷边双层、内墙插脚插进底板——按示意图推读，待核对"),
    "0437": ({"corners": "gusset", "corner_on": "end", "corner_side": "out", "flap_drops": True,
              "lid": {"kind": "flaps", "flap": "long"}},
             "托盘：墙角连角贴在端墙外面，长边墙两片半宽盖翼；盖翼两头各接一段折下贴端墙、插脚插进端线上的孔"
             "——按示意图推读，待核对"),
    "0438": ({"corners": "flap", "corner_on": "end", "corner_side": "out", "rim": "narrow", "rim_on": "end",
              "rim_ends": "wrap", "stack": "center"},
             "水果箱托盘：同 0480（端墙窄边两头折下压住外贴角翼），端墙顶中间堆叠凸舌、底板端线开孔"
             "（角翼斜切角、长边墙中段加高的轮廓未画）——按示意图推读，待核对"),
    "0482": ({"corners": "flap", "corner_on": "long", "rim": "narrow", "rim_on": "end", "stack": "pair"},
             "水果箱托盘：长边墙两头角翼贴端墙里面，端墙顶窄边、两块堆叠凸舌、底板端线开孔"
             "（端墙角上互卡的小缺口未画）——按示意图推读，待核对"),
    "0483": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "long", "end_window": True,
              "corner_posts": True},
             "托盘：端墙顶中间开口（那块往里翻贴墙），端墙两头角翼折成三角角柱、斜面插舌插进底板四角斜缝；"
             "长边墙顶窄边——按示意图推读，待核对"),
    "0418": ({"corners": "ear", "lid": {"kind": "hinged_lid", "lid_front": "return"}},
             "飞机盒：0422 型双层端墙 + 耳朵，盖挂后墙带防尘翼；盖前沿前翼垂下、隔一条卷边回折成双层，"
             "回折那片的三个锁舌插进盖沿压线上的槽——按示意图推读，待核对"),
    "0426": ({"corners": "flap", "corner_on": "end", "front_double": True, "lid": {"kind": "hinged_lid"}},
             "飞机盒：只有前墙卷边双层（内墙三个插脚插底板），端墙两头角翼夹进前墙夹层、贴后墙里面；"
             "盖挂后墙，带防尘翼和插舌——按示意图推读，待核对"),
    "0475": ({"corners": "flap", "corner_on": "end", "front_double": True,
              "lid": {"kind": "hinged_lid", "dust_ears": True}},
             "飞机盒：同 0426，防尘翼前头多一段耳，合盖时贴进前墙里面——按示意图推读，待核对"),
    "0440": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "end",
              "lid": {"kind": "hinged_lid", "lid_front": "lip", "lid_on": "end"}},
             "连盖托盘：端墙两头角翼贴长边墙里面，盖挂在一面端墙上、两侧防尘翼，远端前翼盖在另一面端墙外面，"
             "那面端墙顶上一条窄边——按示意图推读，待核对"),
    "0441": ({"corners": "flap", "corner_on": "long", "lid": {"kind": "hinged_lid", "lid_front": "lip"}},
             "连盖托盘：长边墙两头角翼贴端墙里面，盖挂后墙带防尘翼，前翼盖在前墙外面"
             "（前翼两头的小翼未画）——按示意图推读，待核对"),
    "0442": ({"corners": "flap", "corner_on": "long", "corner_side": "out",
              "lid": {"kind": "hinged_lid", "lid_front": "lip"}},
             "连盖托盘：长边墙两头角翼贴在端墙外面，盖挂后墙带防尘翼，前翼盖在前墙外面"
             "（角翼上的钩锁未画）——按示意图推读，待核对"),
    "0461": ({"corners": "gusset", "corner_on": "long", "front_double": True, "back_double": True},
             "托盘：墙角连角贴在长边墙里面，两面长边墙都卷边双层、内墙各三个插脚插底板（连角夹在夹层里）"
             "——按示意图推读，待核对"),
    "0464": ({"corners": "open", "stack": "pair", "flap_slots": True, "lid": {"kind": "flaps", "flap": "long"}},
             "托盘：十字形不连角，长边墙两片半宽盖翼；端墙顶两块凸舌，盖翼两头开槽、合上时扣住凸舌，"
             "底板端线开孔——按示意图推读，待核对"),
    "0465": ({"corners": "flap", "corner_on": "end", "front_double": True, "end_double": True, "hand_holes": True,
              "lid": {"kind": "hinged_lid", "dust": False, "lid_front": "lock"}},
             "连盖托盘：前墙、端墙都卷边双层（内墙插脚插底板），端墙手孔，端墙两头角翼夹进前墙夹层、贴后墙；"
             "盖挂后墙，前沿中间锁舌插进前墙卷边的槽——按示意图推读，待核对"),
    "0466": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "front",
              "lid": {"kind": "hinged_lid", "dust": False, "lid_front": "lock"}},
             "连盖托盘：端墙两头角翼贴长边墙里面，前墙顶一条窄边，盖挂后墙、前沿锁舌插进前墙窄边压线上的槽"
             "（角翼上的圆扣未画）——按示意图推读，待核对"),
    "0469": ({"corners": "wrap", "lid": {"kind": "hinged_lid", "dust": False, "lid_front": "return"}},
             "高身连盖盒：前墙 – 底 – 后墙 – 盖连成一条，端墙挂后墙两头（顶回折双层、下沿底翼），前墙端翼夹进"
             "端墙夹层；盖前翼 + 回折内衬、锁舌插盖沿槽——按示意图推读，待核对"),
    "0470": ({"corners": "wrap", "lid": {"kind": "hinged_lid", "dust": False}},
             "高身连盖盒：同 0469 的盒身，盖前沿插舌插进前墙里面——按示意图推读，待核对"),
    "0471": ({"corners": "wrap", "lid": {"kind": "hinged_lid", "dust": False, "lid_front": "lip"}},
             "高身连盖盒：同 0469 的盒身，盖前翼盖在前墙外面（目录图是圆角大前翼）——按示意图推读，待核对"),
    "0472": ({"corners": "wrap", "front_return": True,
              "lid": {"kind": "hinged_lid", "dust": False, "lid_front": "lip"}},
             "高身连盖盒：同 0471，前墙顶边再接一块回折内衬——按示意图推读，待核对"),
    "0400": ({"corners": "strip", "strip": [{"ends": "wall+rim"}, {}, {}, {}, {"inset": True}]},
             "折叠盒：底板两头端墙（顶上窄边往里折平），一条长片绕过后墙、盖、前墙，最后一块平铺在底板上当内衬"
             "——按示意图推读，待核对"),
    "0403": ({"corners": "open", "end_double": True, "end_roll": "narrow", "end_foot": True,
              "lid": {"kind": "flaps", "flap": "long"}},
             "托盘：端墙宽卷边成平顶、内墙下沿平脚压在底板上，长边墙两片半宽盖翼，墙角不连——按示意图推读，待核对"),
    "0412": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "end",
              "lid": {"kind": "flaps", "flap": "long"}},
             "托盘：端墙两头角翼贴长边墙里面，端墙顶窄边，长边墙两片半宽盖翼——按示意图推读，待核对"
             "（和 0415 的区别按立体图推读为角翼贴里 / 贴外）"),
    "0413": ({"corners": "flap", "corner_on": "end", "rim": "narrow", "rim_on": "end",
              "lid": {"kind": "hinged_lid", "dust": False, "lid_front": "lip"}},
             "连盖托盘：端墙两头角翼贴长边墙里面，端墙顶窄边，盖挂后墙、不带防尘翼，前翼盖在前墙外面"
             "——按示意图推读，待核对"),
    "0414": ({"corners": "ear", "lid": {"kind": "flaps", "flap": "long"}},
             "托盘：0422 型双层端墙 + 耳朵，长边墙两片半宽盖翼——按示意图推读，待核对"),
    "0415": ({"corners": "flap", "corner_on": "end", "corner_side": "out", "rim": "narrow", "rim_on": "end",
              "lid": {"kind": "flaps", "flap": "long"}},
             "托盘：同 0412，端墙角翼贴在长边墙外面——按示意图推读，待核对（和 0412 的区别按立体图推读）"),
    "0416": ({"corners": "flap", "corner_on": "end", "flap_ears": True, "lid": {"kind": "flaps", "flap": "long"}},
             "托盘：端墙两头角翼贴长边墙里面，长边墙两片半宽盖翼、两头梯形防尘耳折进端墙里面——按示意图推读，待核对"),
    "0443": ({"corners": "strip", "strip": [{"ends": "half"}, {"ends": "wall"}, {"ends": "dust"}, {"size": "tuck"},
                                             {"side": "left", "ends": "wing"}]},
             "折叠盒（立着放）：底 – 后墙 – 盖 – 插舌一条，前墙在底另一边；后墙两头整面端墙，前墙两头夹层翼，"
             "底两头半深翼，盖两头梯形防尘翼——按示意图推读，待核对"),
    "0444": ({"corners": "strip", "strip": [{"ends": "half"}, {"ends": "wall"}, {"side": "left", "ends": "wing"},
                                             {"side": "left", "ends": "dust"}, {"side": "left", "size": "tuck"}]},
             "折叠盒（立着放）：同 0443，但盖挂在前墙上、插舌插进后墙——按示意图推读，待核对"),
    "0445": ({"corners": "flap", "corner_on": "long", "corner_side": "out", "rim": "narrow", "rim_on": "end",
              "end_slits": True, "lid": {"kind": "hinged_lid", "dust": False}},
             "连盖托盘：长边墙两头角翼贴在端墙外面，端墙两道竖缝分成三段、顶上窄边连着，盖挂后墙、插舌"
             "——按示意图推读，待核对"),
    "0447": ({"corners": "strip", "strip": [{}, {"side": "left", "ends": "half"}, {"ends": "half"}, {"ends": "wall"},
                                             {}]},
             "折叠盒（横放）：前墙 – 底 – 后墙 – 顶 – 搭边一条，顶两头整面端翼盖住端面，前后墙两头半深翼在里面"
             "——按示意图推读，待核对"),
    "0473": ({"corners": "strip", "strip": [{"ends": "wall+over"}, {}, {}, {}, {"size": "tuck", "inset": True}]},
             "包裹式：底两头端墙、顶上再接一段折过顶面；一条长片绕过后墙、顶、前墙，再一块插舌塞进底板上"
             "（目录图是两片在一角连着、带锁舌，这里按一片长条表达）——按示意图推读，待核对"),
    "0474": ({"corners": "strip", "strip": [{"ends": "wall+over"}, {}, {}, {}]},
             "包裹式：同 0473，不带插舌——按示意图推读，待核对"),
    "0439": ({"corners": "bevel", "rim": "narrow", "rim_on": "long"},
             "切角托盘：底板四角切 45°，斜墙挂在底板斜边上，长边墙顶窄边（窄边上的锁口未画）——按示意图推读，待核对"),
    "0459": ({"corners": "bevel", "bevel_on": "long", "bevel_join": "flap"},
             "切角托盘：斜墙挂在长边墙两头，端墙两头角翼折 135° 贴在斜墙里面——按示意图推读，待核对"),
    "0459.1": ({"corners": "bevel", "bevel_on": "long", "bevel_join": "wrap"},
               "切角托盘：斜墙挂在长边墙两头，斜墙再接一段折 45° 包到端墙外面——按示意图推读，待核对"),
    "0462": ({"corners": "bevel", "lid": {"kind": "hinged_lid", "dust": False}},
             "切角连盖盒：斜墙挂在底板斜边上，八角盖挂后墙、插舌（目录图斜墙是双层带锁口，未细画）"
             "——按示意图推读，待核对"),
    "0463": ({"corners": "bevel", "bevel_on": "long", "lid": {"kind": "hinged_lid"}},
             "切角连盖盒：斜墙挂在长边墙两头，八角盖挂后墙、两侧防尘翼、插舌——按示意图推读，待核对"),
    "0476": ({"corners": "bevel", "bevel_regular": True, "lid": {"kind": "hinged_lid", "dust": False}},
             "正八角连盖盒（L = W）：八面墙挂在底板八条边上，八角盖挂后墙、插舌——按示意图推读，待核对"),
    "0457": ({"corners": "flap", "corner_on": "long", "corner_side": "out", "corner_hooks": True},
             "扣角托盘：长边墙两头角翼贴在端墙外面，角翼头上的钩舌折进端墙竖缝扣住（目录图是圆钩，画成直角舌）"
             "——按示意图推读，待核对"),
    "0460": ({"corners": "flap", "corner_on": "long", "end_height": "low"},
             "展示托盘：两侧长边墙高，两头端墙矮（默认 0.4 个墙高），长边墙两头角翼贴端墙里面、高出端墙当角柱"
             "——按示意图推读，待核对"),
    "0446": ({"corners": "flap", "corner_on": "end", "rim": "half", "rim_on": "long", "cup_holes": True},
             "杯托：端墙两头角翼贴长边墙里面，长边墙顶各一块半宽隔板平铺在顶上、开一排排圆杯孔"
             "（目录图中间的脊和隔板边上的半圆缺口未画）——按示意图推读，待核对"),
    "0485": ({"corners": "open", "front_low": True,
              "lid": {"kind": "hinged_lid", "lid_front": "lip", "tuck": "full", "lid_header": True}},
             "展示盒：前墙矮（0.4·H）、端墙顶边从后往前斜，盖挂后墙、两侧防尘翼、前裙整高盖住前面；盖中间一道"
             "压线，打开后翻起当头卡（端墙上的斜压线未画）——按示意图推读，待核对"),
    "0430": ({"corners": "flap", "corner_on": "end", "rim": "narrow"},
             "托盘，端墙两头的角翼贴在长边墙里面，四面墙顶往里折一圈窄边（框）"),
    "0422": ({"corners": "ear"}, "托盘，端墙双层（卷边 + 内墙，内墙插脚插进底板），长边墙两头耳朵折进端墙夹层"),
    "0448": ({"corners": "flap", "corner_on": "end", "corner_side": "out", "corner_diagonal": True},
             "托盘，端墙两头的角翼贴在长边墙外面，角翼对角一道压线（糊好后能压扁）"),
    "0450": ({"corners": "flap", "corner_on": "long", "wall_diagonal": True},
             "托盘，长边墙两头的角翼贴在端墙里面，长边墙两头斜压线（糊好后能压扁）"),
    "0451": ({"corners": "flap", "corner_on": "end", "wall_diagonal": True},
             "托盘，端墙两头的角翼贴在长边墙里面，长边墙两头斜压线（糊好后能压扁）"),
    "0449": ({"corners": "gusset", "corner_on": "long", "corner_side": "out"},
             "托盘，连角：角上方片对角折成三角，贴在长边墙外面"),
    "0449.1": ({"corners": "gusset", "corner_on": "long", "corner_side": "in"},
               "托盘，连角：角上方片对角折成三角，贴在长边墙里面"),
    "0452": ({"corners": "flap", "corner_on": "end"}, "托盘，端墙两头带角翼，贴在长边墙里面粘 / 钉"),
    "0453": ({"corners": "flap", "corner_on": "long"}, "托盘，长边墙两头带角翼，贴在端墙里面粘 / 钉"),
    "0458": ({"corners": "open"}, "托盘，十字形，墙角不连，用胶带 / 钉封角"),
    # ---- 07 预糊托盘（第 3 步，连角糊好、压扁出厂）——按示意图推读，待核对 ----
    "0718": ({"corners": "gusset", "corner_on": "end", "rim": "narrow", "rim_on": "long"},
             "预糊托盘：连角三角贴端墙里面，长边墙顶窄边折平"),
    "0747": ({"corners": "gusset", "corner_on": "end", "lid": {"kind": "hinged_lid"}},
             "预糊连盖托盘：连角贴端墙，盖挂后墙、带防尘翼、插舌插进前墙"),
    "0748": ({"corners": "gusset", "corner_on": "long", "lid": {"kind": "hinged_lid"}},
             "预糊连盖托盘：连角贴长边墙，盖带防尘翼和插舌"),
    "0759": ({"corners": "gusset", "corner_on": "end", "lid": {"kind": "hinged_lid", "lid_front": "lip"}},
             "预糊连盖托盘：连角贴端墙，盖前翼盖在前墙外面"),
    "0761": ({"corners": "gusset", "corner_on": "long", "lid": {"kind": "hinged_lid", "lid_front": "lip"}},
             "预糊连盖托盘：连角贴长边墙，盖前翼盖在前墙外面（目录图前翼深 v）"),
    "0751": ({"corners": "gusset", "corner_on": "long", "corner_side": "out", "base_holes": 4,
              "lid": {"kind": "hinged_lid", "dust": False}},
             "预糊连盖托盘：连角贴长边墙外面，底板一排圆孔（目录图盖上一排排杯孔、墙上也有孔，这里只开底孔）"),
    "0770": ({"corners": "flap", "corner_on": "end", "corner_posts": True},
             "预糊水果托：端墙角翼折成三角角柱（目录图角柱高出墙顶，未画）"),
    "0771": ({"corners": "gusset", "corner_on": "long", "base_holes": 3},
             "预糊水果托：连角贴长边墙，底板三个透气孔（目录图端墙另有插缝和凸耳，未画）"),
    "0772": ({"corners": "gusset", "corner_on": "end", "corner_side": "out", "base_holes": 5},
             "预糊水果托：连角贴端墙外面，底板透气孔（目录图墙脚半圆缺口，未画）"),
    "0773": ({"corners": "flap", "corner_on": "long", "corner_side": "out", "end_double": True},
             "预糊托盘：长边墙角翼贴端墙外面，端墙卷边双层（目录图角翼带锁扣缝，未画）"),
    "0774": ({"corners": "flap", "corner_on": "long", "corner_side": "out", "end_double": True, "hand_holes": True},
             "预糊托盘：同 0773，端墙开手孔（目录图孔开在长边墙上）"),
    # ---- 09 内托（第 3 步）：inside = 按盒子内尺寸缩一圈 ----
    "0911": ({"corners": "flap", "corner_on": "end", "inside": True}, "内托：端墙两头角翼，放进盒里（小一圈间隙）"),
    "0912": ({"corners": "flap", "corner_on": "long", "inside": True}, "内托：长边墙两头角翼，放进盒里"),
    "0954": ({"corners": "gusset", "corner_on": "end", "inside": True, "base_holes": 2},
             "内托：连角浅托、底板两个瓶口孔（目录图是一面 H 高端墙撑起的带孔平台，这里按带孔内托表达）"
             "——按示意图推读，待核对"),
    "0955": ({"corners": "open", "inside": True, "open_taper": True},
             "内托：十字形，四面墙两头斜收成梯形，墙角留 V 口——按示意图推读"),
    # ---- 08 展示托盘（第 4 步）：front_low 前墙矮、端墙斜边——按示意图推读，待核对 ----
    "0802": ({"corners": "flap", "corner_on": "end", "front_low": True, "back_double": True},
             "展示托盘：前墙矮、端墙斜边，端墙角翼贴长边墙，后墙卷边双层（目录图前墙也双层、后墙加高头卡，未画）"),
    "0803": ({"corners": "flap", "corner_on": "long", "front_low": True, "back_double": True},
             "展示托盘：前墙矮，长边墙角翼贴端墙，后墙双层（目录图端墙外三角翼，未画）"),
    "0804": ({"corners": "flap", "corner_on": "long", "corner_side": "out", "front_low": True, "back_double": True},
             "展示托盘：同 0803，角翼贴端墙外面（目录图墙顶卷边，未画）"),
    "0808": ({"corners": "gusset", "corner_on": "long", "front_low": True},
             "展示托盘：前墙矮，连角贴长边墙（目录图后墙两侧带耳，未画）"),
    "0809": ({"corners": "open", "front_low": True, "back_double": True},
             "展示托盘：十字形不连角，前墙矮，后墙双层（目录图中间一块隔板，未画）"),
    "0815": ({"corners": "flap", "corner_on": "end", "corner_side": "out", "front_low": True},
             "展示托盘：前墙矮，端墙角翼贴长边墙外面（目录图后墙加高，未画）"),
    "0816": ({"corners": "gusset", "corner_on": "long", "corner_side": "out", "front_low": True},
             "展示托盘：前墙矮，连角贴长边墙外面（目录图后墙加高、两侧翼，未画）"),
    "0817": ({"corners": "flap", "corner_on": "long", "front_low": True}, "展示托盘：前墙矮单层，长边墙角翼贴端墙"),
    "0826": ({"corners": "flap", "corner_on": "end", "front_low": True},
             "头卡展示托：前墙矮、端墙斜（目录图后墙加高成大头卡，未画）"),
    "0873": ({"corners": "flap", "corner_on": "end", "corner_side": "out", "front_low": True, "back_double": True},
             "杂志架：前墙矮、端墙斜，角翼贴外面，后墙双层（目录图前墙 U 口，未画）"),
    "0874": ({"corners": "flap", "corner_on": "long", "corner_side": "out", "front_low": True},
             "展示托：前墙矮，长边墙角翼贴端墙外面（可挂在 0849 上当层板）"),
    "0885": ({"corners": "flap", "corner_on": "end", "lid": {"kind": "hinged_lid", "lid_front": "lip"}},
             "电商飞机盒：连盖、前翼盖在前墙外面（目录图前翼上自封胶条 + 撕拉条，未画）"),
}  # fmt: skip
TRAY_KEYS = (
    "corners",
    "corner_on",
    "corner_side",
    "corner_diagonal",
    "wall_diagonal",
    "rim",
    "rim_on",
    "lid",
    "lid_dust",
    "double_walls",
    "inner_ears",
    "inner_foot",
    "rim_ends",
    "triangle_posts",
    "inner_wings",
    "edge_slots",
    "corner_meet",
    "stack",
    "lug_count",
    "stack_on",
    "end_height",
    "end_double",
    "flap_drops",
    "end_window",
    "corner_posts",
    "front_double",
    "back_double",
    "hand_holes",
    "flap_slots",
    "front_return",
    "strip",
    "flap_ears",
    "end_roll",
    "end_foot",
    "end_slits",
    "bevel_regular",
    "bevel_on",
    "bevel_join",
    "corner_hooks",
    "cup_holes",
    "front_low",
    "lid_header",
    "lid_front",
    "dust_ears",
    "lid_on",
    "ear_shape",
    "lugs",
    "cross_flaps",
    "inside",
    "base_holes",
    "open_taper",
)
LOW_END_RATIO = 0.4  # 展示托盘矮端墙高 0.4·H【初值】
TRAY_RIM_RATIO, TRAY_RIM_CAP_MM = 0.12, 30.0  # 托盘窄边 v = min(0.12·W, 30)【初值】


def _strip_sig(strip: Any) -> Any:
    """长条配方的比较签名（补上默认值）。"""
    if strip is None:
        return None
    from pacxio.engine.recipe import StripItem

    return [StripItem(**(s if isinstance(s, dict) else s.model_dump())).model_dump() for s in strip]


def tray_code(p: Any) -> str | None:
    """托盘配方 → FEFCO 04 编号（开顶托盘按墙角机构认）。"""
    b = p.body
    if p.top.kind not in ("open", "hinged_lid", "flaps"):
        return None
    if (
        b.double_walls == "all"
        and b.ear_shape == "calibrated"
        and (not b.lugs or not p.tuning.get("return_wall", {}).get("lugs", True))
    ):
        return None  # 0425 / 0425.1 的两对内墙都有插脚；去掉后只能称自定义结构。
    have = {"corners": "flap" if b.corners == "glue" else b.corners, "corner_on": b.corner_on,
            "corner_side": b.corner_side, "corner_diagonal": b.corner_diagonal,
            "wall_diagonal": b.wall_diagonal, "rim": b.rim is not None, "rim_on": b.rim_on,
            "lid": p.top.kind == "hinged_lid", "lid_dust": p.top.kind == "hinged_lid" and p.top.dust,
            "double_walls": b.double_walls, "inner_ears": b.inner_ears,
            "inner_foot": b.inner_foot, "rim_ends": b.rim_ends, "triangle_posts": b.triangle_posts,
            "inner_wings": b.inner_wings, "edge_slots": b.edge_slots, "corner_meet": b.corner_meet,
            "stack": b.stack, "lug_count": b.lug_count, "stack_on": b.stack_on,
            "end_height": b.end_height is not None, "end_double": b.end_double, "flap_drops": b.flap_drops,
            "end_window": b.end_window, "corner_posts": b.corner_posts, "front_double": b.front_double,
            "back_double": b.back_double, "hand_holes": b.hand_holes, "flap_slots": b.flap_slots,
            "front_return": b.front_return, "strip": _strip_sig(b.strip), "flap_ears": b.flap_ears,
            "end_roll": b.end_roll is not None, "end_foot": b.end_foot, "end_slits": b.end_slits,
            "bevel_regular": b.bevel_regular, "bevel_on": b.bevel_on, "bevel_join": b.bevel_join,
            "corner_hooks": b.corner_hooks, "cup_holes": b.cup_holes, "front_low": b.front_low,
            "inside": b.inside, "base_holes": b.base_holes, "open_taper": b.open_taper}  # fmt: skip
    hinged = p.top.kind == "hinged_lid"
    have |= {"lid_front": p.top.lid_front if hinged else "tuck", "dust_ears": hinged and p.top.dust_ears,
             "lid_on": p.top.lid_on if hinged else "back", "lid_header": hinged and p.top.lid_header}  # fmt: skip
    have |= {"ear_shape": b.ear_shape, "lugs": b.lugs}
    have["cross_flaps"] = p.top.flap if p.top.kind == "flaps" else "none"
    for code, (spec, _) in TRAYS.items():
        lid = spec.get("lid")
        want = {"corner_on": "end", "corner_side": "in", "corner_diagonal": False, "wall_diagonal": False,
                "rim_on": "all", "double_walls": "pair", "inner_ears": False, "inner_foot": False,
                "rim_ends": "none", "triangle_posts": False, "inner_wings": False, "edge_slots": False,
                "corner_meet": False, "stack": "none", "lug_count": None, "stack_on": "end",
                "end_double": False, "flap_drops": False, "end_window": False, "corner_posts": False,
                "front_double": False, "back_double": False, "hand_holes": False, "flap_slots": False,
                "front_return": False, "strip": _strip_sig(spec.get("strip")), "flap_ears": False,
                "end_foot": False, "end_slits": False, "bevel_regular": False, "bevel_on": "base",
                "bevel_join": "open", "corner_hooks": False, "cup_holes": False, "front_low": False,
                "inside": False, "base_holes": 0, "open_taper": False,
                "lid_header": bool((lid or {}).get("lid_header")),
                "lid_front": (lid or {}).get("lid_front", "tuck"), "dust_ears": bool((lid or {}).get("dust_ears")),
                "lid_on": (lid or {}).get("lid_on", "back"),
                "ear_shape": "calibrated", "lugs": True,
                "cross_flaps": lid.get("flap", "half") if lid and lid["kind"] == "flaps" else "none"} | spec | {"rim": "rim" in spec, "end_height": "end_height" in spec, "end_roll": "end_roll" in spec,
                                           "strip": _strip_sig(spec.get("strip")),
                                           "lid": lid is not None and lid["kind"] == "hinged_lid",
                                           "lid_dust": bool(lid and lid["kind"] == "hinged_lid" and lid.get("dust", True))}  # fmt: skip
        keys = (  # 不连角：角翼挂哪边 / 贴里贴外无意义，其余照比
            TRAY_KEYS
            if spec["corners"] != "open"
            else tuple(
                k for k in TRAY_KEYS if k not in ("corner_on", "corner_side", "corner_diagonal")
            )
        )
        if all(have[k] == want[k] for k in keys):
            return code
    return None


# 03 套盖（25 款，第 2 步）：多件套，每件是普通结构配方（engine/sets.py）
SERIES_03: tuple[str, ...] = (
    "0300", "0301", "0302", "0303", "0304", "0306", "0307", "0308", "0309", "0310", "0312", "0313",
    "0314", "0319", "0320", "0321", "0322", "0323", "0325", "0330", "0331", "0350", "0351", "0352", "0360",
)  # fmt: skip
LID_V_RATIO, LID_V_MIN_MM = 0.25, 20.0  # 浅盖 / 盖帽深 v = max(0.25·H, 20)【初值】


def _tray_part(corners: str, **body: Any) -> dict[str, Any]:
    return {"body": {"form": "tray", "corners": corners, **body}, "top": {"kind": "open"},
            "bottom": {"kind": "open"}}  # fmt: skip


def _poly_tube(n: int) -> dict[str, Any]:
    """正 n 边形套筒，两头开口（03 的八角 / 六角桶）。"""
    return {"body": {"base": "regular", "sides": n}, "top": OPEN, "bottom": OPEN}


def _tab_tube(axis: str) -> dict[str, Any]:
    """两头开口、接头是不粘的插片的横放筒（0510 / 0511 的里件）。"""
    return {
        "body": {"base": "rect", "axis": axis},
        "seam": {"kind": "tuck"},
        "top": OPEN,
        "bottom": OPEN,
    }


def _split_tube(axis: str) -> dict[str, Any]:
    """两头开口、一面墙在中间对开（不搭接）的套筒（0404 / 0405）。"""
    return {"body": {"base": "rect", "axis": axis}, "seam": {"kind": "none", "center_open": True}, "top": OPEN,
            "bottom": OPEN}  # fmt: skip


def _tube_part(top: dict[str, Any], bottom: dict[str, Any], axis: str = "H") -> dict[str, Any]:
    return {"body": {"base": "rect", "axis": axis}, "top": top, "bottom": bottom}


def _sht(
    crease: str, panels: list[str], folds: list[float] | None = None, **extra: Any
) -> dict[str, Any]:
    """片材件（三片式盒身、展架外壳、隔板）：条宽写成盒子尺寸的式子。"""
    return {"body": {"form": "sheet", **_ins(crease, panels, folds, **extra)}}


def _end_tray(**body: Any) -> dict[str, Any]:
    """三片式的端板：W × H 的十字形浅托（四边 v 翼包住盒身）。"""
    return {**_tray_part(body.pop("corners", "open"), **body), "dims": ["W", "H", "v"]}


def _shelf(dims: list[str] | None = None, **body: Any) -> dict[str, Any]:
    """展架层板：L × W × v 的浅托。"""
    return {**_tray_part(body.pop("corners", "flap"), **body), "dims": dims or ["L", "W", "v"]}


END_PLATE = _sht("W", ["H"])  # 三片式的平端板 W × H
SHELL = _sht("H", ["W", "L", "W"], [90, 90])  # 展架 U 形外壳：两侧 W + 背板 L，高 H
SHELL_LIP = _sht("H", ["v", "W", "L", "W", "v"], [90, 90, 90, 90])  # 外壳两侧前沿再折一条 v


def LONG(
    n: int, at: str = "even", panels: list[str] | None = None, wide: bool = False
) -> dict[str, Any]:  # noqa: N802
    """纵隔板（长 L、高 H）：从上沿开 n 道槽（横隔板几片就几道）。"""
    body: dict[str, Any] = {"form": "sheet", "crease": "along_H", "panels": panels or ["L"],
            "slots": {"count": n, "edge": "top", "at": at, "wide": wide}}  # fmt: skip
    if panels:
        body["folds"] = [90.0] * (len(panels) - 1)
    return {"body": body}


def CROSS(
    n: int, at: str = "even", panels: list[str] | None = None, wide: bool = False
) -> dict[str, Any]:  # noqa: N802
    """横隔板（长 W、高 H）：从下沿开 n 道槽（纵隔板几片就几道）。"""
    out = LONG(n, at, panels or ["W"], wide)
    out["body"]["slots"]["edge"] = "bottom"
    if not panels:
        out["body"].pop("folds", None)
    return out


# 编号 → (套的种类, 各件（depth: full 同高 / half 半高 / v 浅盖）, 套合, 一句话说明)
SETS: dict[str, tuple[str, dict[str, tuple[dict[str, Any], str]], dict[str, dict[str, Any]], str]] = {
    "0300": ("telescope", {"base": (_tray_part("flap"), "full"), "lid": (_tray_part("flap"), "full")},
             {"lid": {"over": "base"}}, "天地盖：地盒、天盖都是端墙带角翼的托盘，天盖盖到底"),
    "0301": ("telescope", {"base": (_tray_part("flap"), "full"), "lid": (_tray_part("flap", corner_on="long"), "full")},
             {"lid": {"over": "base"}}, "天地盖：地盒端墙带角翼，天盖长边墙带角翼，天盖盖到底"),
    "0308": ("telescope", {"base": (_tray_part("flap", corner_side="out", corner_diagonal=True), "full"),
                           "lid": (_tray_part("flap", corner_side="out", corner_diagonal=True), "full")},
             {"lid": {"over": "base"}},
             "天地盖：地盒、天盖都是 0448 型（角翼贴长边墙外面、对角一道压扁用的压线）——按示意图推读"),
    "0309": ("telescope", {"base": (_tray_part("gusset", corner_on="long", corner_side="in"), "full"),
                           "lid": (_tray_part("gusset", corner_on="long", corner_side="in"), "full")},
             {"lid": {"over": "base"}},
             "天地盖：地盒、天盖都是连角托盘（角上方片对角折成三角贴在长边墙里面）——按示意图推读"),
    "0303": ("telescope", {"base": (_tray_part("flap", wall_diagonal=True), "full"),
                           "lid": (_tray_part("flap", corner_on="long", wall_diagonal=True), "full")},
             {"lid": {"over": "base"}},
             "天地盖：地盒 0451 型、天盖 0450 型（角翼 + 长边墙压扁斜压线）——按示意图推读"),
    "0304": ("telescope", {"base": (_tray_part("flap", wall_diagonal=True), "full"),
                           "lid": (_tray_part("flap", wall_diagonal=True), "full")},
             {"lid": {"over": "base"}}, "天地盖：地盒、天盖都是 0451 型——按示意图推读"),
    "0427.1": ("telescope", {"base": (_tray_part("ear"), "full"), "lid": (_tray_part("open"), "full")},
               {"lid": {"over": "base"}},
               "两件式飞机盒：0422 型托盘 + 单独一张十字形盖（四边折下套在托盘外面；目录图盖的前边是圆角插舌、"
               "两侧是梯形翼，未细画）——按示意图推读，待核对"),
    "0302": ("telescope", {"base": (_tray_part("open"), "full"), "lid": (_tray_part("open"), "full")},
             {"lid": {"over": "base"}}, "天地盖：地盒、天盖都是十字形托盘，墙角用钉 / 胶带"),
    "0306": ("telescope", {"base": (_tray_part("flap"), "full"), "lid": (_tray_part("flap"), "v")},
             {"lid": {"over": "base"}}, "天地盖：端墙带角翼的托盘 + 浅盖（深 v）"),
    "0310": ("caps", {"body": (_tube_part(OPEN, OPEN), "full"), "cap": (_tray_part("flap"), "v")},
             {"cap": {"over": "body", "count": 2}}, "两头帽：套筒（上下开口）+ 两个浅盖帽（深 v）"),
    "0312": ("caps", {"body": (_tube_part(OPEN, _flaps("half")), "full"), "cap": (_tray_part("flap"), "v")},
             {"cap": {"over": "body"}}, "开顶开槽箱（底 ½W 摇盖）+ 顶上一个浅盖帽（深 v）"),
    "0313": ("caps", {"body": (_tube_part(_flaps("half"), _flaps("half")), "full"),
                      "cap": (_tray_part("flap"), "v")},
             {"cap": {"over": "body", "count": 2}}, "普通开槽箱（两头 ½W 摇盖）+ 两头各套一个浅盖帽（深 v）"),
    "0319": ("telescope", {"inner": (_tube_part(_flaps("half"), OPEN), "full"),
                           "outer": (_tube_part(OPEN, _flaps("half")), "full")},
             {"outer": {"over": "inner"}},
             "两个半截开槽箱套在一起：里件带顶摇盖、底开口，外件（大一圈）带底摇盖、从下面套上"),
    "0320": ("telescope", {"inner": (_tube_part(OPEN, _flaps("half")), "full"),
                           "outer": (_tube_part(_flaps("half"), OPEN), "full")},
             {"outer": {"over": "inner"}},
             "两个半截开槽箱套在一起：里件带底摇盖、顶开口，外件（大一圈）带顶摇盖、从上面套下来"),
    "0404": ("slide", {"inner": (_split_tube("L"), "full"), "outer": (_split_tube("W"), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "两件交叉：两个 U 形套（底 + 两墙 + 顶面中间对开的两片半宽翼），一个筒长 L、一个筒长 W，交叉套住六面"
             "——按示意图推读，待核对"),
    "0405": ("caps", {"body": (_split_tube("L"), "full"), "cap": (_tray_part("open"), "v")},
             {"cap": {"over": "body", "count": 2}},
             "U 形套（筒长 L、顶面中间对开）+ 两头端片（目录图端片是 U 形插在套里，这里按两头浅盖帽表达）"
             "——按示意图推读，待核对"),
    "0307": ("telescope", {"base": (_tray_part("flap", corner_on="long", corner_side="out", corner_hooks=True), "full"),
                           "lid": (_tray_part("flap", corner_on="long", corner_side="out", corner_hooks=True), "full")},
             {"lid": {"over": "base"}}, "天地盖：地盒、天盖都是 0457 型扣角托盘——按示意图推读，待核对"),
    "0314": ("caps", {"body": (_tube_part(OPEN, OPEN), "full"), "cap": (_tray_part("ear"), "v")},
             {"cap": {"over": "body", "count": 2}}, "套筒 + 两头 0422 型双层端墙浅盖帽——按示意图推读，待核对"),
    "0321": ("telescope", {"inner": (_tube_part(OPEN, _flaps("gusset")), "full"),
                           "outer": (_tube_part(_flaps("gusset"), OPEN), "full")},
             {"outer": {"over": "inner"}},
             "两个半截开槽箱套在一起，摇盖是斜压线三角折页（同 0226）——按示意图推读，待核对"),
    "0322": ("telescope", {"base": (_tray_part("bevel", rim="narrow", rim_on="long"), "full"),
                           "lid": (_tray_part("bevel"), "v")},
             {"lid": {"over": "base"}}, "切角天地盖：地盒切角托盘（长边窄边）+ 切角浅盖——按示意图推读，待核对"),
    "0323": ("telescope", {"base": (_tray_part("bevel", rim="half", rim_on="long"), "full"),
                           "lid": (_tray_part("bevel"), "v")},
             {"lid": {"over": "base"}},
             "切角天地盖：地盒切角托盘、长边两块半宽内盖 + 切角浅盖——按示意图推读，待核对"),
    "0325": ("caps", {"body": (_tube_part(_flaps("rim"), _flaps("rim")), "full"), "cap": (_tray_part("ear"), "v")},
             {"cap": {"over": "body", "count": 2}},
             "两头窄边折平的套筒 + 两头双层墙浅盖帽（目录图盖帽四面都卷边，这里用 0422 型）——按示意图推读，待核对"),
    "0350": ("caps", {"body": (_poly_tube(8), "full"), "cap": (_tray_part("polygon", poly_sides=8), "v")},
             {"cap": {"over": "body", "count": 2}}, "八角桶：正八边形套筒 + 两头八角浅盖帽——按示意图推读，待核对"),
    "0351": ("caps", {"body": (_poly_tube(8), "full"),
                      "cap": (_tray_part("polygon", poly_sides=8, poly_flaps=True), "v")},
             {"cap": {"over": "body", "count": 2}},
             "八角桶：同 0350，盖帽每面墙头上一块角翼贴进下一面墙——按示意图推读，待核对"),
    "0352": ("caps", {"body": ({"body": {"base": "octagon"}, "top": OPEN, "bottom": OPEN}, "full"),
                      "cap": (_tray_part("bevel", bevel_on="long", bevel_join="flap"), "v")},
             {"cap": {"over": "body", "count": 2}},
             "长八角桶：长方形切四角的八面套筒 + 两头切角浅盖帽（盖帽斜墙挂长边墙、端墙角翼贴斜墙）"
             "——按示意图推读，待核对"),
    "0360": ("caps", {"body": (_poly_tube(6), "full"), "cap": (_tray_part("polygon", poly_sides=6), "v")},
             {"cap": {"over": "body", "count": 2}},
             "六角桶：正六边形套筒 + 两头六角浅盖帽（目录图另有两块六角垫片，未画）——按示意图推读，待核对"),
    "0509": ("slide", {"inner": (_split_tube("W"), "full"), "outer": (_tube_part(OPEN, OPEN, "L"), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "抽屉式：外面 0503 套筒（筒长 L），里件是顶面中间对开的 U 形套（09 的 0907，筒长 W）——按示意图推读，待核对"),
    "0510": ("slide", {"inner": (_tab_tube("L"), "full"), "outer": (_tube_part(OPEN, OPEN), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "外面竖向套筒（0501 型），里件横放（筒长 L）、接头是插片不粘，顶面当盖从套筒上口打开——按示意图推读，待核对"),
    "0511": ("slide", {"inner": (_tab_tube("L"), "full"), "outer": (_tube_part(OPEN, OPEN, "W"), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "里件同 0510；外件目录图是三面 U 形带端翼，这里按两头开口的套筒（筒长 W）表达——按示意图推读，待核对"),
    "0512": ("slide", {"inner": (_split_tube("L"), "full"), "outer": (_tube_part(OPEN, OPEN, "W"), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "里件是三面 U 形（横放、顶面中间对开），外件目录图是三面 U 形带端翼，这里按两头开口的套筒（筒长 W）表达"
             "——按示意图推读，待核对"),
    "0504": ("slide", {"inner": (_tube_part(OPEN, OPEN, "W"), "full"), "outer": (_tube_part(OPEN, OPEN), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "两个套筒交叉套住六面：里面 0502（筒长 W），外面 0501（筒长 H）"),
    "0505": ("slide", {"inner": (_tube_part(OPEN, OPEN, "L"), "full"),
                       "outer": (_tube_part(OPEN, OPEN, "W"), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "两个套筒交叉套住六面：里面 0503（筒长 L），外面 0502（筒长 W）"),
    "0507": ("slide", {"inner": (_tube_part(OPEN, OPEN, "L"), "full"), "outer": (_tube_part(OPEN, OPEN), "full")},
             {"outer": {"over": "inner", "cover": "box"}},
             "两个套筒交叉套住六面：里面 0503（筒长 L），外面 0501（筒长 H）"),
    "0330": ("telescope", {"base": (_tray_part("ear"), "full"), "lid": (_tray_part("ear"), "full")},
             {"lid": {"over": "base"}}, "天地盖：地盒、天盖都是双层端墙 + 耳朵的托盘（0422 型，内墙插脚）"),
    "0331": ("telescope", {"base": (_tray_part("ear", lugs=False), "full"),
                           "lid": (_tray_part("ear", lugs=False), "full")},
             {"lid": {"over": "base"}}, "天地盖：同 0330，内墙不带插脚"),
    # ---- 07 两件式预糊箱（第 3 步）——按示意图推读，待核对 ----
    "0714": ("telescope", {"base": (_tube_part(OPEN, AUTO), "full"), "lid": (_tube_part(OPEN, AUTO), "half")},
             {"lid": {"over": "base"}}, "两件式：自动锁底的开顶箱 + 同样做法的半高盖（盖倒扣，锁底当盖顶）"),
    "0725": ("telescope", {"base": (_tube_part(OPEN, AUTO), "half"), "lid": (_tube_part(_flaps("half"), OPEN), "full")},
             {"lid": {"over": "base"}},
             "两件式：矮的自动锁底托（目录图两侧墙斜）+ 顶部 ½W 摇盖、底开口的高套从上面套下"),
    "0726": ("telescope", {"base": (_tube_part(OPEN, AUTO_TAB), "half"), "lid": (_tube_part(LID, OPEN), "full")},
             {"lid": {"over": "base"}},
             "两件式：矮的插舌锁底托 + 顶部插舌盖、底开口的高套（目录图托两侧斜、套上开手孔，未画）"),
    "0727": ("telescope", {"base": (_tray_part("flap", hand_holes=True), "v"), "lid": (_tube_part(OPEN, OPEN), "full")},
             {"lid": {"over": "base"}}, "两件式：带手孔的浅托 + 上下开口的套筒从上面套下"),
    # ---- 07 盒 + 隔板（目录图隔板和底翼 / 提手连在同一张版上，这里按单独的十字隔板表达）----
    "0715": ("kit", {"box": (_tube_part(_flaps("half"), AUTO), "full"), "long": (LONG(2), "full"),
                     "cross": (CROSS(1), "full")}, {}, "自动锁底箱（目录图 L = 1.5W）+ 隔板分 2 × 3 格"),
    "0716": ("kit", {"box": ({**_tube_part(_flaps("half"), AUTO), "body": {"base": "rect", "tear": True}}, "full"),
                     "long": (LONG(1), "full"), "cross": (CROSS(1), "full")}, {},
             "撕开变展示托盘的自动锁底箱（同 0728）+ 十字隔板"),
    "0717": ("kit", {"box": (_tube_part({"kind": "handle"}, AUTO), "full"), "long": (LONG(2), "full"),
                     "cross": (CROSS(1), "full")}, {}, "提手瓶托：提手平顶 + 自动锁底（目录图 L = 1.5W）+ 隔板分 2 × 3 格"),
    "0723": ("kit", {"box": (_tube_part({"kind": "handle"}, AUTO), "full"), "long": (LONG(1), "full"),
                     "cross": (CROSS(1), "full")}, {}, "提手瓶托：提手平顶 + 自动锁底（目录图 L = W）+ 十字隔板分 4 格"),
    "0724": ("kit", {"box": (_tube_part({"kind": "handle"}, AUTO_WEB), "full"), "long": (LONG(2), "full"),
                     "cross": (CROSS(1), "full")}, {}, "提手瓶托：提手平顶 + 糊胶三角锁底 + 隔板分 2 × 3 格"),
    # ---- 09 隔板组、组合内衬（第 3 步）----
    "0929": ("kit", {"wrap_l": ({"body": {"form": "sheet", "crease": "along_L", "panels": ["W", "H", "W"],
                                          "folds": [90, 90]}}, "full"),
                     "wrap_h": ({"body": {"form": "sheet", "crease": "along_H", "panels": ["W", "L", "W"],
                                          "folds": [90, 90]}}, "full")}, {},
             "两片 U 形套：一片顺 L（W、H、W）、一片顺 H（W、L、W），交叉围住盒里四面"),
    "0930": ("kit", {"long": (LONG(1), "full"), "cross": (CROSS(1), "full")}, {}, "十字隔板：两片各一道槽对插"),
    "0931": ("kit", {"long": (LONG(2, "ends"), "full"), "cross": (CROSS(2, "ends"), "full")}, {},
             "井字围框：纵、横隔板各两片，槽开在两头，插成框"),
    "0932": ("kit", {"long": (LONG(2, "ends", ["v", "L", "v"]), "full"),
                     "cross": (CROSS(2, "ends", ["v", "W", "v"]), "full")}, {},
             "围框：同 0931，每片两头再一道压线折成角柱——按示意图推读"),
    "0933": ("kit", {"long": (LONG(3), "full"), "cross": (CROSS(2), "full")}, {},
             "格子隔板：纵隔板 2 片（3 道槽）、横隔板 3 片（2 道槽），格数可改"),
    "0934": ("kit", {"long": (LONG(4, wide=True), "full"), "cross": (CROSS(3, wide=True), "full")}, {},
             "格子隔板：宽槽（2t + 2），纵 3 片、横 4 片（目录图槽画成粗线，按宽槽推读）"),
    "0935": ("kit", {"long": (LONG(3), "v"), "cross_low": (CROSS(2), "v"), "cross": (CROSS(2), "full")}, {},
             "高低格子：纵隔板和一片横隔板矮（v），另两片横隔板全高——按示意图推读"),
    "0960": ("kit", {"side": ({"body": {"form": "sheet", "crease": "along_H", "panels": ["L"], "steps": 3}}, "full"),
                     "tread": ({"body": {"form": "sheet", "crease": "along_W",
                                         "panels": ["H/3", "L/3", "H/3", "L/3", "H/3", "L/3"],
                                         "folds": [90, -90, 90, -90, 90]}}, "full")}, {},
             "阶梯内衬：两片台阶形侧板 + 一条折成三级台阶的踏板（目录图侧板和踏板用插舌连接，未画）——按示意图推读"),
    # ---- 06 三片式（第 4 步）：盒身一条围起来 + 两块端板（×2）——按示意图推读，待核对 ----
    "0601": ("kit", {"body": (_sht("L", ["H", "W", "H", "W"], [90, 90, 90], strip_flaps={"panels": [0, 1, 2]}), "full"),
                     "end": (END_PLATE, "full")}, {},
             "三片式：盒身 H、W、H、W（最后一块 W 当盖），前三块两头端翼压在端板上；两块平端板"),
    "0602": ("kit", {"body": (_sht("L", ["H", "W", "H", "W", "v"], [90] * 4, strip_flaps={"panels": [0, 1, 2]}), "full"),
                     "end": (END_PLATE, "full")}, {}, "三片式：同 0601，盖头上再一条插舌插进前墙"),
    "0605": ("kit", {"body": (_sht("L", ["v", "H", "W", "H", "v"], [90] * 4), "full"),
                     "end": (_end_tray(open_taper=True), "full")}, {},
             "三片式：盒身 U 形、两边顶上 v 窄边折进来；端板四边梯形翼包住盒身"),
    "0606": ("kit", {"body": (_sht("L", ["W/2", "H", "W", "H", "W/2"], [90] * 4, strip_flaps={}), "full"),
                     "end": (END_PLATE, "full")}, {}, "三片式：盒身顶面两片 W/2 对开，每条两头端翼压在平端板上"),
    "0607": ("kit", {"body": (_sht("L", ["j", "H", "W", "H", "W"], [90] * 4), "full"),
                     "end": (_end_tray(open_taper=True), "full")}, {},
             "三片式：盒身四面围一圈（糊口条 j），端板四边梯形翼包住盒身"),
    "0608": ("kit", {"body": (_sht("L", ["v", "W", "H", "W", "H"], [90] * 4), "full"),
                     "end": (_end_tray(open_taper=True), "full")}, {},
             "三片式：盒身四面围一圈、头上一条 v 插舌（目录图圆角），端板梯形翼"),
    "0610": ("kit", {"body": (_sht("L", ["j", "H", "W", "H", "W"], [90] * 4), "full"),
                     "end": (_end_tray(), "full")}, {}, "三片式：盒身四面围一圈（糊口条 j），端板十字形四边 v 翼包住盒身"),
    "0615": ("kit", {"body": (_sht("L", ["j", "W", "H", "W", "H"], [90] * 4), "full"),
                     "end": (_end_tray(corners="flap"), "full")}, {},
             "三片式：盒身从 W 起围一圈，端板翼带角翼（目录图翼上两道压线包过纸厚，按角翼端板表达）"),
    "0616": ("kit", {"body": (_sht("L", ["j", "W", "H", "W", "H"], [90] * 4), "full"),
                     "end": (_end_tray(), "full")}, {}, "三片式：盒身从 W 起围一圈，端板十字形 v 翼"),
    "0620": ("kit", {"body": (_sht("L", ["W/2", "H", "W", "H", "W/2"], [90] * 4), "full"),
                     "end": (_end_tray(), "full")}, {}, "三片式：盒身顶面两片 W/2 对开，端板十字形 v 翼"),
    "0621": ("kit", {"body": (_sht("L", ["H", "W", "H"], [90, 90]), "full"), "end": (_end_tray(), "full"),
                     "lid": (_sht("L", ["H", "W/2"], [90]), "full")}, {},
             "多片式：U 形盒身 + 两块十字端板 + 两片半宽盖（各带一面裙边）——按示意图推读，待核对"),
    # ---- 08 展架（第 4 步）：外壳 + 层板（× n）——按示意图推读，待核对，目录图上的挂舌 / 插槽连接未画 ----
    "0830": ("kit", {"shell": (SHELL, "full"), "shelf": (_shelf(), "full")}, {}, "落地展架：U 形外壳（两侧 + 背板）+ 三块层板托"),
    "0831": ("kit", {"shell": (SHELL, "full"), "divider": (_sht("H", ["W"], slots={"count": 3, "edge": "top"}), "full"),
                     "shelf": (_shelf(dims=["L/2", "W", "v"]), "full")}, {},
             "双列展架：U 形外壳 + 中间竖隔板 + 六块半宽层板托"),
    "0832": ("kit", {"shell": (SHELL_LIP, "full"), "shelf": (_shelf(), "full")}, {},
             "展架：外壳两侧前沿折边（目录图外壳上窄下宽），四块层板托"),
    "0833": ("kit", {"shell": (SHELL_LIP, "full"), "shelf": (_shelf(back_double=True), "full")}, {},
             "展架：外壳前沿折边，三块后沿双层的层板托"),
    "0834": ("kit", {"shell": (SHELL, "full"), "shelf": (_shelf(corner_on="long"), "full")}, {},
             "展架：U 形外壳（目录图上窄下宽、两侧弧顶），三块层板托"),
    "0840": ("kit", {"shell": (SHELL, "full"), "shelf": (_shelf(front_low=True), "full")}, {},
             "展架：U 形外壳（目录图两侧波浪形）+ 四块前低后高的层板托"),
    "0842": ("kit", {"box": (_tube_part({"kind": "slope"}, _flaps("half")), "full"), "shelf": (_shelf(), "full")}, {},
             "展架：斜口陈列箱当底座（目录图上部弧形两侧）+ 两块层板托"),
    "0843": ("kit", {"shell": (SHELL, "full"), "shelf": (_shelf(corners="open"), "full")}, {},
             "展架：U 形外壳（目录图两侧开窗）+ 三块十字层板"),
    "0845": ("kit", {"shell": (_poly_tube(8), "full"),
                     "shelf": (_shelf(corners="polygon", poly_sides=8, dims=["L", "L", "v"]), "full")},
             {}, "圆柱展架：八角筒外壳（目录图是圆筒）+ 四块八角层板"),
    "0846": ("kit", {"shell": (_poly_tube(6), "full"),
                     "shelf": (_shelf(corners="polygon", poly_sides=6, dims=["L", "L", "v"]), "full")},
             {}, "六角展架：六角筒外壳（目录图四面开窗）+ 三块六角层板"),
    "0847": ("kit", {"shell": (_sht("H", ["W", "L", "W"], [90, 90], holes={"panel": 1, "count": 5}), "full"),
                     "rack": (_sht("H", ["L"], holes={"panel": 0, "count": 5}), "full")}, {},
             "插瓶展架：U 形外壳背板开一排孔 + 两块开孔插板（目录图是一排排孔、插板十字插接）"),
    "0848": ("kit", {"box": (_tube_part(OPEN, _flaps("half")), "half"), "long": (LONG(2), "full"),
                     "cross": (CROSS(1), "full")}, {},
             "格子陈列箱：半高开顶箱 + 纵横隔板（目录图上面再一块方框托住隔板，未画）"),
    "0849": ("kit", {"shell": (SHELL, "full"), "shelf": (_shelf(corner_side="out"), "full")}, {},
             "挂板展架：U 形外壳 + 三块挂在外壳上的层板托（0874 型）"),
    "0853": ("kit", {"box": (_tube_part(_flaps("half"), AUTO), "full"),
                     "insert": ({**_tray_part("flap", inside=True, base_holes=6), "dims": ["L", "W", "v"]}, "full")},
             {}, "插瓶陈列箱：自动锁底箱 + 一块开瓶孔的内托（目录图是 0425.1 型带孔托和后墙头卡）"),
    "0854": ("kit", {"box": (_tube_part({"kind": "slope"}, AUTO), "full"), "long": (LONG(1), "full"),
                     "cross": (CROSS(2), "full")}, {}, "斜口格子陈列箱：0801 型箱 + 纵横隔板"),
    "0870": ("kit", {"box": (_tube_part(OPEN, _flaps("half")), "full"),
                     "step": (_sht("W", ["H/3", "L/3", "H/3", "L/3", "H/3", "L/3"], [90, -90, 90, -90, 90]), "full")},
             {}, "阶梯陈列箱：开顶箱 + 一条折成三级台阶的踏板"),
    "0871": ("kit", {"box": (_tube_part({"kind": "display", "on": 2}, _flaps("half")), "full"), "shelf": (_shelf(), "full")},
             {}, "展示板陈列箱：背板立起当展示板（0219 型）+ 一块斜放的层板托"),
    "0872": ("kit", {"box": (_tray_part("flap", corner_on="long"), "full"), "long": (LONG(1), "full"),
                     "cross": (CROSS(2), "full")}, {}, "格子柜：托盘当柜体（立起来开口朝前）+ 纵横隔板分格"),
}  # fmt: skip
KIT_COUNTS: dict[str, dict[str, int]] = {
    "0715": {"cross": 2}, "0717": {"cross": 2}, "0724": {"cross": 2},
    "0931": {"long": 2, "cross": 2}, "0932": {"long": 2, "cross": 2},
    "0933": {"long": 2, "cross": 3}, "0934": {"long": 3, "cross": 4},
    "0935": {"long": 2, "cross_low": 1, "cross": 2}, "0960": {"side": 2},
    **{c: {"end": 2} for c in ("0601", "0602", "0605", "0606", "0607", "0608", "0610", "0615", "0616", "0620")},
    "0621": {"end": 2, "lid": 2},
    "0830": {"shelf": 3}, "0831": {"shelf": 6}, "0832": {"shelf": 4}, "0833": {"shelf": 3}, "0834": {"shelf": 3},
    "0840": {"shelf": 4}, "0842": {"shelf": 2}, "0843": {"shelf": 3}, "0845": {"shelf": 4}, "0846": {"shelf": 3},
    "0847": {"rack": 2}, "0848": {"cross": 2}, "0849": {"shelf": 3}, "0854": {"cross": 2}, "0872": {"cross": 2},
}  # fmt: skip


def set_recipe(code: str, L: float, W: float, H: float) -> dict[str, Any]:
    """FEFCO 03 编号 + 内尺寸 → 多件套配方（engine/sets.py 的 SetParams）。套在外面的件长宽按被套件算。"""
    kind, parts, fits, _ = SETS[code]
    v = max(LID_V_RATIO * H, LID_V_MIN_MM)
    out: dict[str, Any] = {"set": kind, "parts": {}, "fits": fits}
    if code in KIT_COUNTS:
        out["counts"] = KIT_COUNTS[code]
    for name, (part, depth) in parts.items():
        h = (
            H if depth == "full" else 0.5 * H if depth == "half" else v
        )  # 开槽箱做的浅件用半高（v 太矮）
        part = dict(part)
        dims = part.pop(
            "dims", None
        )  # 这件自己的长宽高写成盒子尺寸的式子（三片式的端板、展架的层板）
        if dims is not None:
            from pacxio.engine.recipe import symbol_value

            pl, pw, ph = (symbol_value(x, L, W, H) for x in dims)
            out["parts"][name] = {**part, "body": dict(part["body"]) | {"L": pl, "W": pw, "H": ph}}
            continue
        body = dict(part["body"]) | {"L": L, "W": W, "H": h}
        if body.get("base") == "regular":  # 正多边形筒：对边距 = L
            n = body["sides"]
            body.pop("L"), body.pop("W")
            body["edge"] = L * math.tan(math.pi / n)
        if body.get("corners") == "polygon":
            body["W"] = L
        if body.get("rim") == "narrow":
            body["rim"] = min(TRAY_RIM_RATIO * W, TRAY_RIM_CAP_MM)
        if body.get("rim") == "half":
            body["rim"] = 0.5 * W - 3.0
        out["parts"][name] = {**part, "body": body}
    return out


def _signature(sp: Any) -> str:
    """多件套的“形状”：各件除尺寸外的配方 + 套合关系（反查编号用）。"""
    import json

    def part(p: Any) -> dict[str, Any]:
        body = p.body.model_dump(exclude={"L", "W", "H", "edge"})
        for k in ("end_height", "end_roll", "bevel", "corner_depth"):  # 只比有没有，不比尺寸
            body[k] = body.get(k) is not None
        rim = p.body.rim  # 窄边 / 半宽内盖（0322 / 0323）按占宽的比例分
        body["rim"] = (
            None if rim is None else ("wide" if p.body.W and rim >= 0.35 * p.body.W else "narrow")
        )
        ends = {
            k: (getattr(p, k).kind, getattr(p, k).flap, getattr(p, k).autolock_form)
            for k in ("top", "bottom")
        }
        return {"body": body, "ends": ends, "seam": p.seam.model_dump()}

    def depth(name: str) -> str:  # 套在外面的件：和被套件一样高 = 盖到底，矮 = 浅盖 v
        fit = sp.fits.get(name)
        if fit is None:
            return "full"
        return "full" if sp.parts[name].body.H >= sp.parts[fit.over].body.H - 1e-6 else "v"

    data = {"set": sp.set, "parts": {k: part(v) | {"depth": depth(k)} for k, v in sp.parts.items()},
            "fits": {k: (f.over, f.count, f.cover) for k, f in sp.fits.items()},
            "counts": {k: n > 1 for k, n in sp.counts.items()}}  # fmt: skip
    return json.dumps(data, sort_keys=True, default=str)


def set_code(sp: Any) -> str | None:
    from pacxio.engine.sets import SetParams

    sig = _signature(sp)
    for code in SETS:
        if _signature(SetParams(**set_recipe(code, 300, 200, 100))) == sig:
            return code
    return None


def _sheet(code: str, L: float, W: float, H: float) -> dict[str, Any]:
    lip = min(LIP_H_RATIO * H, LIP_CAP_MM)
    along_w = code in ("0121", "0122", "0123", "0129", "0130")
    base = L if along_w else W
    shapes: dict[str, tuple[list[float], list[float]]] = {
        "0111": ([H, base], [90]), "0121": ([H, base], [90]),
        "0112": ([H, base, H], [90, 90]), "0122": ([H, base, H], [90, 90]),
        "0113": ([lip, H, base, H], [90, 90, 90]), "0123": ([lip, H, base, H], [90, 90, 90]),
        "0119": ([lip, H, base, H, lip], [90, 90, 90, -90]),
        "0129": ([lip, H, base, H, lip], [90, 90, 90, -90]),
        "0130": ([L] * FANFOLD_COUNT, [180.0 if i % 2 == 0 else -180.0 for i in range(FANFOLD_COUNT - 1)]),
    }  # fmt: skip
    body: dict[str, Any] = {"form": "sheet", "L": L, "W": W}
    if code in shapes:
        panels, folds = shapes[code]
        body |= {"panels": panels, "folds": folds, "crease": "along_W" if along_w else "along_L"}
        body["L" if along_w else "W"] = sum(panels)
    if code == "0100":
        body["roll"] = True
    return {"body": body}


def _insert_sig(p: Any) -> str:
    import json

    body = p.body.model_dump(mode="json", exclude={"L", "W", "H"})
    n = len(p.body.panels or [0])
    body["folds"] = list(p.body.folds) if p.body.folds is not None else [90.0] * (n - 1)
    return json.dumps(body, sort_keys=True)


def insert_code(p: Any) -> str | None:
    """09 内衬片材（条宽写成盒子尺寸的式子）→ 编号：折线方向、每条的式子、折角、槽 / 孔 / 台阶都对上。"""
    from pacxio.engine.recipe import StructureParams

    sig = _insert_sig(p)
    for code in INSERTS:
        if _insert_sig(StructureParams(**recipe(code, 300, 200, 100))) == sig:
            return code
    return None


def sheet_code(p: Any) -> str | None:
    """片材配方 → FEFCO 01 编号（按折线方向、几条、正反折认）；条宽写成式子的是 09 内衬。"""
    b = p.body
    if any(isinstance(w, str) for w in b.panels or []):
        return insert_code(p)
    if b.roll:
        return "0100"
    panels = b.panels or [0.0]
    folds = list(b.folds) if b.folds is not None else [90.0] * (len(panels) - 1)
    if len(panels) == 1:
        return "0110"
    if len(panels) > 2 and all(abs(a) == 180 for a in folds) and len(set(panels)) == 1:
        return "0130"
    sign = [a > 0 for a in folds]
    pattern = {(2, (True,)): ("0111", "0121"), (3, (True, True)): ("0112", "0122"),
               (4, (True, True, True)): ("0113", "0123"),
               (5, (True, True, True, False)): ("0119", "0129")}.get((len(panels), tuple(sign)))  # fmt: skip
    if pattern is None or any(abs(a) != 90 for a in folds):
        return None
    return pattern[0] if b.crease == "along_L" else pattern[1]


WING_NARROW_RATIO, WING_NARROW_CAP_MM = 0.12, 30.0  # 0227 的窄翼 = min(0.12·W, 30)【初值】
JOINTS = {
    "0230": "long",
    "0231": "short",
    "0228": "partition",
    "0913": "center_open",
}  # 接头在墙中间：长边 / 端墙 / 正中 + 隔板
JOINT_OFFSET_RATIO = 0.25  # 接头离墙角 v = 0.25 × 那面墙的内长【初值】（目录没给 v）


def _end(spec: dict[str, Any], W: float) -> dict[str, Any]:
    """目录里的写法 → 配方：翼宽 half / narrow 换成 mm，拇指缺口 on / opposite 换成面号。"""
    out = dict(spec)
    if out.get("wing") == "half":
        out["wing"] = 0.5 * W
    elif out.get("wing") == "narrow":
        out["wing"] = min(WING_NARROW_RATIO * W, WING_NARROW_CAP_MM)
    if "thumb" in out:
        on = out.get("on", 2)  # 摇盖不给面：外摇盖在正面
        out["thumb"] = on if out["thumb"] == "on" else 2 - on
    return out


def recipe(code: str, L: float, W: float, H: float) -> dict[str, Any]:
    """FEFCO 编号 + 内尺寸 → 结构配方。01 片材：L 折线方向上的长、W 槽底宽、H 槽边高。还没做的编号报错。"""
    if code in SHEETS:
        return _sheet(code, L, W, H)
    if code in NATIVE:
        n, top_end, bottom_end, _ = NATIVE[code]
        return {"body": {"base": "regular", "sides": n, "edge": L * math.tan(math.pi / n), "H": H},
                "top": dict(top_end), "bottom": dict(bottom_end)}  # fmt: skip
    if code in INSERTS:
        return {"body": {"form": "sheet", "L": L, "W": W, "H": H, **INSERTS[code][0]}}
    if code in MAILERS:
        return {"body": {"form": "tray", "L": L, "W": W, "H": H}, "top": dict(MAILERS[code][0]),
                "bottom": {"kind": "open"}}  # fmt: skip
    if code in SLEEVES:
        return {"body": {"base": "rect", "L": L, "W": W, "H": H, "axis": SLEEVES[code][0]},
                "top": {"kind": "open"}, "bottom": {"kind": "open"}}  # fmt: skip
    if code in TRAYS:
        spec = dict(TRAYS[code][0])
        if spec.get("rim") == "narrow":
            spec["rim"] = min(TRAY_RIM_RATIO * W, TRAY_RIM_CAP_MM)
        if spec.get("end_height") == "half":
            spec["end_height"] = 0.5 * H
        if spec.get("rim") == "half":  # 两块隔板在中间对上
            spec["rim"] = 0.5 * W - 3.0
        if spec.get("end_height") == "low":
            spec["end_height"] = LOW_END_RATIO * H
        if spec.get("end_roll") == "narrow":
            spec["end_roll"] = min(TRAY_RIM_RATIO * W, TRAY_RIM_CAP_MM)
        top = dict(spec.pop("lid", {"kind": "open"}))
        if top.get("tuck") == "full":  # 插舌差不多一个墙高
            top.pop("tuck")
            top["tuck_len"] = H - 2.0
        return {"body": {"form": "tray", "L": L, "W": W, "H": H, **spec}, "top": top,
                "bottom": {"kind": "open"}}  # fmt: skip
    if code not in STYLES and code not in FOLDERS:
        raise ValueError(f"FEFCO {code} 还没有配方（已做 {len(STYLES)} 款：{', '.join(STYLES)}）")
    if code in FOLDERS:
        t, b, axis, _ = FOLDERS[code]
        result = {"body": {"base": "rect", "L": L, "W": W, "H": H, "axis": axis}, "top": _end(t, W),
                  "bottom": _end(b, W)}  # fmt: skip
        if code == "0409":
            result["seam"] = {"kind": "none", "full_overlap": True, "wall": "short"}
        elif code == "0411":
            result["seam"] = {"kind": "none", "center_open": True}
        elif code in ("0406", "0410"):
            result["seam"] = {"kind": "tuck"}
        return result
    top, bottom, _ = STYLES[code]
    out = {
        "body": {"base": "rect", "L": L, "W": W, "H": H, **STYLE_BODY.get(code, {})},
        "top": _end(top, W),
        "bottom": _end(bottom, W),
    }
    if JOINTS.get(code) == "partition":
        out["seam"] = {"partition": True}
    elif JOINTS.get(code) == "center_open":
        out["seam"] = {"kind": "none", "center_open": True}
    elif code in JOINTS:
        wall = JOINTS[code]
        out["seam"] = {"offset": JOINT_OFFSET_RATIO * (L if wall == "long" else W), "wall": wall}
    return out


def _same(spec: dict[str, Any], end: dict[str, Any]) -> bool:
    if spec["kind"] != end.get("kind"):
        return False
    if "on" in spec and {0: "back", 2: "front"}[spec["on"]] != end.get("on"):
        return False
    if spec["kind"] == "autolock":
        return bool(spec.get("autolock_form", "hook") == end.get("form", "hook"))
    if spec["kind"] == "lid_tuck" and bool(spec.get("tear_strip")) != bool(end.get("tear_strip")):
        return False
    if spec["kind"] == "flaps":
        same = (spec["flap"] == end.get("flap") and spec.get("fold", "in") == end.get("fold", "in")
                and bool(spec.get("lock")) == bool(end.get("lock"))
                and spec.get("end_flaps", True) == end.get("end_flaps", True)
                and ("wing" in spec) == ("wing" in end))  # fmt: skip
        thumb = end.get("thumb")
        where = None if thumb is None else ("on" if thumb == end.get("on") else "opposite")
        return same and spec.get("thumb") == where
    return True


# 连盖托盘（飞机盒类，第 2 步）：编号 → (盖的配方, 说明)；0427 走校准过的 fefco_0427
MAILERS: dict[str, tuple[dict[str, Any], str]] = {
    "0427": (
        {"kind": "mailer"},
        "飞机盒：双层侧墙 + 耳朵，盖挂后墙、带防尘翼，插舌包在前墙外面、插耳折进侧墙夹层",
    ),
}

# 05 套盒（10 款，第 2 步）；套筒两头开口，按哪个尺寸是筒长分三款
SERIES_05: tuple[str, ...] = (
    "0501",
    "0502",
    "0503",
    "0504",
    "0505",
    "0507",
    "0509",
    "0510",
    "0511",
    "0512",
)
SLEEVES: dict[str, tuple[str, str]] = {
    "0501": ("H", "套筒，四面围一圈、上下开口（筒长 H）"),
    "0502": ("W", "套筒，面是 L、H、L、H，两头开口（筒长 W）"),
    "0503": ("L", "套筒，面是 W、H、W、H，两头开口（筒长 L）"),
}


def sleeve_code(axis: str) -> str | None:
    return next((c for c, (a, _) in SLEEVES.items() if a == axis), None)


# 04 里“横放的开槽箱”（第 2 步）：编号 → (顶, 底, 筒长方向, 说明)
FOLDERS: dict[str, tuple[dict[str, Any], dict[str, Any], str, str]] = {
    "0409": (_flaps("full"), _flaps("full"), "L", "横放筒身两端全幅墙重叠——按示意图推读，待核对"),
    "0406": (_flaps("rim"), _flaps("rim"), "L",
             "横放折叠盒：两端各四片窄翼，侧边自由插片——按示意图推读，待核对"),
    "0410": (_flaps("half"), _flaps("half"), "L",
             "折叠盒：开槽箱横放（筒沿 L），一片 W 当盖、接头那条当插舌插进前墙（不粘），两头摇盖当端墙——按示意图推读"),
    "0411": (_flaps("half"), _flaps("half"), "L",
             "折叠盒：横放筒身顶面从中间断开成两片半宽盖，无搭接无侧缝——按示意图推读，待核对"),
}  # fmt: skip


def body_extras(body: Any) -> dict[str, Any]:
    """开槽箱墙上的刀口选项（开窗 / 出液口 / 撕开展示）：只记不是默认的。"""
    out: dict[str, Any] = {}
    if body.window is not None:
        out |= {"window": body.window, "window_shape": body.window_shape}
    if body.tear:
        out["tear"] = True
    return out


def code_of(
    top: dict[str, Any], bottom: dict[str, Any], joint: str | None = None, axis: str = "H",
    extras: dict[str, Any] | None = None,
) -> str | None:  # fmt: skip
    """拼装后的两端（on 已定：back / front）+ 接头在哪（None 墙角 / long / short）+ 筒长方向
    + 墙上的刀口选项 → FEFCO 编号；没有对应的返回 None。"""
    extras = extras or {}
    if axis == "H":
        for code, (t, b, _) in STYLES.items():
            want = dict(STYLE_BODY.get(code, {}))
            if "window" in want:
                want.setdefault("window_shape", "octagon")
            if (JOINTS.get(code) == joint and _same(t, top) and _same(b, bottom)
                    and want == extras):  # fmt: skip
                return code
    for code, (t, b, a, _) in FOLDERS.items():
        expected_joint = (
            "center_open"
            if code == "0411"
            else "free_tab"
            if code == "0406"
            else "full_overlap"
            if code == "0409"
            else None
        )
        # 旧 0410 job 没有 seam 字段，继续识别；新配方用自由插片，旧做法由 S04 提醒。
        joint_matches = joint in (None, "free_tab") if code == "0410" else joint == expected_joint
        if joint_matches and a == axis and _same(t, top) and _same(b, bottom):
            return code
    return None


__all__ = [
    "INSERTS",
    "NATIVE",
    "SERIES_06",
    "SERIES_08",
    "SERIES_07",
    "SERIES_09",
    "insert_code",
    "SERIES_03",
    "SETS",
    "set_code",
    "set_recipe",
    "SERIES_01",
    "SERIES_02",
    "SERIES_04",
    "SHEETS",
    "STYLES",
    "TRAYS",
    "code_of",
    "recipe",
    "sheet_code",
    "tray_code",
]
