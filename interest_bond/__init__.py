"""利率债研究模块，合并自独立 bond_monitor 项目。"""

from .bond_switch import bp as bond_switch_bp, init_app as init_bond_switch
from .issuance import bp as issuance_bp, init_app as init_issuance
from .spread import bp as spread_bp, init_app as init_spread

__all__ = [
    "bond_switch_bp",
    "issuance_bp",
    "spread_bp",
    "init_bond_switch",
    "init_issuance",
    "init_spread",
]
