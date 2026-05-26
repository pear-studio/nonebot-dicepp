"""COC7 衍生属性 helper 函数纯单元测试

回归测试 #55 review 提到的 2 个 bug：
- _derive_mov 边界：两者 ≥ SIZ 且至少一个 > SIZ 才返回 9
- _derive_db_build 525+ 外推：应当从 +6d6/+7 起，每 80 +1d6/+1
"""
import re

import pytest

from module.misc.coc_command import (
    _derive_db_build,
    _derive_mov,
    _format_one,
    _roll_attrs,
)


# ─────────────────────────── MOV ───────────────────────────

class TestDeriveMov:
    """MOV 公式：两者均 <SIZ → 7；均 ≥SIZ 且至少一个 >SIZ → 9；其余 → 8"""

    @pytest.mark.parametrize("str_val,dex_val,siz_val", [
        (50, 50, 60),    # 都明显 < SIZ
        (40, 50, 55),    # STR < SIZ, DEX < SIZ
        (1,  1,  100),   # 极端
    ])
    def test_both_below_siz_returns_7(self, str_val, dex_val, siz_val):
        assert _derive_mov(str_val, dex_val, siz_val) == 7

    @pytest.mark.parametrize("str_val,dex_val,siz_val", [
        (70, 65, 65),    # STR > SIZ, DEX = SIZ  ← #55 review 提到的 case
        (65, 70, 65),    # STR = SIZ, DEX > SIZ  ← 同上
        (70, 70, 65),    # 两者都严格 >
        (80, 80, 50),    # 强力角色
        (66, 65, 65),    # 仅 STR 比 SIZ 多 1
    ])
    def test_at_least_one_strictly_above_returns_9(self, str_val, dex_val, siz_val):
        """修复 #55 bug：旧实现要求两者严格 > SIZ，这些 case 会错误返回 8"""
        assert _derive_mov(str_val, dex_val, siz_val) == 9

    def test_all_equal_to_siz_returns_8(self):
        """STR=DEX=SIZ：两者均 ≥ 但没有严格 >，应该是 MOV 8"""
        assert _derive_mov(65, 65, 65) == 8

    @pytest.mark.parametrize("str_val,dex_val,siz_val", [
        (70, 50, 60),    # STR > SIZ, DEX < SIZ
        (40, 80, 60),    # STR < SIZ, DEX > SIZ
        (60, 50, 60),    # STR = SIZ, DEX < SIZ
    ])
    def test_mixed_returns_8(self, str_val, dex_val, siz_val):
        """一个 >=SIZ 一个 <SIZ：MOV 8"""
        assert _derive_mov(str_val, dex_val, siz_val) == 8


# ─────────────────────────── DB / Build ───────────────────────────

class TestDeriveDbBuild:
    """COC7 伤害加值表，含表外延伸 525+。"""

    @pytest.mark.parametrize("total,expected_db,expected_build", [
        (2,    "-2",   -2),
        (64,   "-2",   -2),
        (65,   "-1",   -1),
        (84,   "-1",   -1),
        (85,   "0",     0),
        (124,  "0",     0),
        (125,  "+1d4", +1),
        (164,  "+1d4", +1),
        (165,  "+1d6", +2),
        (204,  "+1d6", +2),
        (205,  "+2d6", +3),
        (284,  "+2d6", +3),
        (285,  "+3d6", +4),
        (364,  "+3d6", +4),
        (365,  "+4d6", +5),
        (444,  "+4d6", +5),
        (445,  "+5d6", +6),
        (524,  "+5d6", +6),
    ])
    def test_in_table_range(self, total, expected_db, expected_build):
        # STR + SIZ 拆分不影响结果（只看 total）
        str_val = min(total - 1, 99)
        siz_val = total - str_val
        db, build = _derive_db_build(str_val, siz_val)
        assert db == expected_db, f"total={total}: db {db} != {expected_db}"
        assert build == expected_build, f"total={total}: build {build} != {expected_build}"

    def test_525_extrapolation_first_step(self):
        """修复 #55 bug：525 应该是 +6d6/+7（旧实现错误返回 +5d6/+6 同档）"""
        db, build = _derive_db_build(300, 225)  # total=525
        assert db == "+6d6"
        assert build == 7

    @pytest.mark.parametrize("total,expected_db,expected_build", [
        (525,  "+6d6", 7),
        (604,  "+6d6", 7),
        (605,  "+7d6", 8),
        (684,  "+7d6", 8),
        (685,  "+8d6", 9),
        (765,  "+9d6", 10),
    ])
    def test_above_table_extrapolation(self, total, expected_db, expected_build):
        """525+ 每 80 +1d6/+1 — pear review 的 #55 bug"""
        str_val = min(total - 1, 99)
        siz_val = total - str_val
        db, build = _derive_db_build(str_val, siz_val)
        assert db == expected_db, f"total={total}: db {db} != {expected_db}"
        assert build == expected_build, f"total={total}: build {build} != {expected_build}"

    def test_boundary_524_to_525(self):
        """524 和 525 必须落到不同档位（这是旧 bug 的核心）"""
        db_524, build_524 = _derive_db_build(262, 262)  # total=524
        db_525, build_525 = _derive_db_build(262, 263)  # total=525
        assert (db_524, build_524) == ("+5d6", 6)
        assert (db_525, build_525) == ("+6d6", 7)


# ─────────────────────────── _roll_attrs ───────────────────────────

class TestRollAttrs:
    """属性投骰范围检查（5x3d6 + 3x(2d6+6)*5 + LUCK）"""

    def test_returns_9_attrs(self):
        attrs = _roll_attrs()
        assert len(attrs) == 9

    def test_attr_ranges(self):
        """3d6×5 范围 15-90；(2d6+6)×5 范围 40-90"""
        for _ in range(100):  # 多次采样
            attrs = _roll_attrs()
            # 0=STR 1=CON 3=DEX 4=APP 6=POW 8=LUCK：3d6×5
            for idx in (0, 1, 3, 4, 6, 8):
                assert 15 <= attrs[idx] <= 90, f"attrs[{idx}]={attrs[idx]} out of 3d6×5 range"
            # 2=SIZ 5=INT 7=EDU：(2d6+6)×5
            for idx in (2, 5, 7):
                assert 40 <= attrs[idx] <= 90, f"attrs[{idx}]={attrs[idx]} out of (2d6+6)×5 range"


# ─────────────────────────── _format_one ───────────────────────────

class TestFormatOne:
    def test_contains_all_attribute_names(self):
        attrs = [60, 50, 65, 70, 55, 90, 65, 80, 75]  # 固定一组，方便断言
        out = _format_one(attrs)
        for name in ["力量", "体质", "体型", "敏捷", "外貌", "智力", "意志", "教育", "幸运"]:
            assert name in out
        assert "HP" in out
        assert "MP" in out
        assert "DB" in out
        assert "体格" in out
        assert "MOV" in out

    def test_hp_formula(self):
        # HP = (CON + SIZ) / 10 = (50 + 65) / 10 = 11
        attrs = [60, 50, 65, 70, 55, 90, 65, 80, 75]
        out = _format_one(attrs)
        assert re.search(r"HP\s+11\b", out), out

    def test_mp_formula(self):
        # MP = POW / 5 = 65 / 5 = 13
        attrs = [60, 50, 65, 70, 55, 90, 65, 80, 75]
        out = _format_one(attrs)
        assert re.search(r"MP\s+13\b", out), out

    def test_totals(self):
        attrs = [60, 50, 65, 70, 55, 90, 65, 80, 75]
        out = _format_one(attrs)
        # 不含幸运合计 = 60+50+65+70+55+90+65+80 = 535
        # 总和 = 535 + 75 = 610
        assert "535" in out
        assert "610" in out
