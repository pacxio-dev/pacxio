from pacxio.styles.base import BoxStyle, all_styles, get_style, register
from pacxio.styles.carton import CartonStyle
from pacxio.styles.counter_display import CounterDisplayStyle
from pacxio.styles.crashlock import CrashRscStyle
from pacxio.styles.divider import DividerStyle
from pacxio.styles.drawer import DrawerStyle
from pacxio.styles.fefco_0201 import SlottedStyle
from pacxio.styles.fefco_0427 import MailerStyle
from pacxio.styles.flat import CardStyle, HeaderCardStyle
from pacxio.styles.gable import GableStyle
from pacxio.styles.hanger import (
    AutolockHangerStyle,
    HangerStyle,
    SnaplockHangerStyle,
    SteHangerStyle,
)
from pacxio.styles.insert_card import InsertCardStyle
from pacxio.styles.label import LabelConeStyle, LabelPanelStyle, LabelWrapStyle
from pacxio.styles.lockbottom import AutolockStyle, SnaplockStyle
from pacxio.styles.mailer_closures import FrontLockMailerStyle, TearSealMailerStyle
from pacxio.styles.mailer_tuck import InnerTuckMailerStyle
from pacxio.styles.pouch import (
    PouchBacksealStyle,
    PouchFlatBottomStyle,
    PouchStandupStyle,
    SachetStyle,
)
from pacxio.styles.regular_tray import GluedTrayStyle, HingedLidStyle, LockTrayStyle
from pacxio.styles.rte import RteStyle
from pacxio.styles.seal_end import SealEndStyle
from pacxio.styles.sleeve import BellyBandStyle, SleeveStyle
from pacxio.styles.slotted_variants import (
    FullOverlapSlottedStyle,
    HalfSlottedStyle,
    OverlapSlottedStyle,
)
from pacxio.styles.ste import SteStyle
from pacxio.styles.tray_lid import TrayLidStyle

register(RteStyle())
register(SteStyle())
register(SnaplockStyle())
register(AutolockStyle())
register(SlottedStyle())
register(MailerStyle())
register(TrayLidStyle())
register(DrawerStyle())
register(LabelPanelStyle())
register(LabelWrapStyle())
register(LabelConeStyle())
register(PouchStandupStyle())
register(SachetStyle())
register(PouchBacksealStyle())
register(PouchFlatBottomStyle())
for _style in (
    InnerTuckMailerStyle(),
    FrontLockMailerStyle(),
    TearSealMailerStyle(),
    GluedTrayStyle(),
    LockTrayStyle(),
    HingedLidStyle(),
    SleeveStyle(),
    BellyBandStyle(),
    InsertCardStyle(),
    DividerStyle(),
    SealEndStyle(),
    HangerStyle(),
    SteHangerStyle(),
    SnaplockHangerStyle(),
    AutolockHangerStyle(),
    HalfSlottedStyle(),
    OverlapSlottedStyle(),
    FullOverlapSlottedStyle(),
    CardStyle(),
    HeaderCardStyle(),
    CrashRscStyle(),
    GableStyle(),
    CartonStyle(),
    CounterDisplayStyle(),
):
    register(_style)

# 几何引擎（）：按配方生成，不是一个手写盒型；试验中，不进 styles list / 结构库
from pacxio.engine.style import StructureStyle  # noqa: E402

register(StructureStyle())

from pacxio.engine.set_style import StructureSetStyle  # noqa: E402

register(StructureSetStyle())

__all__ = [
    "AutolockStyle",
    "BoxStyle",
    "DrawerStyle",
    "MailerStyle",
    "RteStyle",
    "SlottedStyle",
    "SnaplockStyle",
    "SteStyle",
    "TrayLidStyle",
    "all_styles",
    "get_style",
    "register",
]
