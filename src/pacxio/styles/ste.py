"""同向插舌盒 STE（）：与 RTE 相同，区别是顶盖和底盖都挂在 back 上。

    [top_tuck]
    [top_lid ]  [dust_ta]              [dust_tb]
[glue] [  back  ]  [ side_a ]  [ front  ]  [ side_b ]
    [btm_lid ]  [dust_ba]              [dust_bb]
    [btm_tuck]
"""

from __future__ import annotations

from typing import ClassVar

from pacxio.styles.rte import RteStyle


class SteStyle(RteStyle):
    id: ClassVar[str] = "ste"
    name_zh: ClassVar[str] = "同向插舌盒"
    refs: ClassVar[dict[str, str]] = {"ecma": "待核对"}
    bottom_lid_on: ClassVar[str] = "back"
