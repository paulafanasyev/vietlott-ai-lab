"""Định nghĩa trò chơi và kiểm tra bộ số (nguồn quy tắc: vietlott.vn)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from typing import Sequence


@dataclass(frozen=True)
class Game:
    code: str
    name_vi: str
    main_count: int
    max_number: int
    has_bonus: bool
    draw_weekdays: tuple[int, ...]  # Monday=0
    draw_time_local: time
    rules_url: str


MEGA645 = Game(
    code="mega645",
    name_vi="Mega 6/45",
    main_count=6,
    max_number=45,
    has_bonus=False,
    draw_weekdays=(2, 4, 6),  # Thứ Tư, Thứ Sáu, Chủ Nhật
    draw_time_local=time(18, 0),
    rules_url="https://vietlott.vn/vi/choi/mega-6-45/gioi-thieu-san-pham-6-45",
)

POWER655 = Game(
    code="power655",
    name_vi="Power 6/55",
    main_count=6,
    max_number=55,
    has_bonus=True,
    draw_weekdays=(1, 3, 5),  # Thứ Ba, Thứ Năm, Thứ Bảy
    draw_time_local=time(18, 0),
    rules_url="https://vietlott.vn/vi/choi/power-6-55/gioi-thieu-san-pham-power-655",
)

GAMES: dict[str, Game] = {g.code: g for g in (MEGA645, POWER655)}


class ValidationError(ValueError):
    """Bộ số không hợp lệ theo quy tắc trò chơi."""


def get_game(code: str) -> Game:
    try:
        return GAMES[code]
    except KeyError as exc:
        raise ValidationError(f"Trò chơi không được hỗ trợ: {code!r}") from exc


def normalize_draw_no(value: object) -> str:
    """Số kỳ quay luôn là 5 chữ số ('1576' -> '01576')."""
    if isinstance(value, bool):
        raise ValidationError(f"Số kỳ không hợp lệ: {value!r}")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        m = re.fullmatch(r"\s*#?([0-9]{1,5})\s*", value)  # chỉ chữ số ASCII, tối đa một dấu '#'
        if not m:
            raise ValidationError(f"Số kỳ không hợp lệ: {value!r}")
        text = m.group(1)
    else:
        raise ValidationError(f"Số kỳ không hợp lệ: {value!r}")
    if len(text) > 5 or int(text) <= 0:
        raise ValidationError(f"Số kỳ không hợp lệ: {value!r}")
    return text.zfill(5)


def _strict_int(value: object, what: str) -> int:
    """Chỉ chấp nhận số nguyên thật hoặc chuỗi chữ số; từ chối bool, số thực, None."""
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{what} không hợp lệ: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValidationError(f"{what} không phải số nguyên: {value!r}")


def validate_numbers(game: Game, main: Sequence[int], bonus: int | None) -> tuple[tuple[int, ...], int | None]:
    """Trả về (bộ số chính đã sắp xếp, số phụ). Ném ValidationError nếu sai."""
    if isinstance(main, (str, bytes)) or not isinstance(main, Sequence):
        raise ValidationError(f"Bộ số phải là danh sách: {main!r}")
    nums = [_strict_int(n, "Số chính") for n in main]
    if len(nums) != game.main_count:
        raise ValidationError(f"{game.name_vi}: cần {game.main_count} số chính, nhận {len(nums)}")
    if len(set(nums)) != len(nums):
        raise ValidationError(f"{game.name_vi}: các số chính bị trùng {nums}")
    if any(n < 1 or n > game.max_number for n in nums):
        raise ValidationError(f"{game.name_vi}: số ngoài khoảng 1..{game.max_number}: {nums}")
    if game.has_bonus:
        if bonus is None:
            raise ValidationError(f"{game.name_vi}: thiếu số đặc biệt")
        b = _strict_int(bonus, "Số đặc biệt")
        if b < 1 or b > game.max_number:
            raise ValidationError(f"{game.name_vi}: số đặc biệt ngoài khoảng: {b}")
        if b in nums:
            raise ValidationError(f"{game.name_vi}: số đặc biệt {b} trùng số chính")
        return tuple(sorted(nums)), b
    if bonus is not None:
        raise ValidationError(f"{game.name_vi} không có số đặc biệt, nhận {bonus!r}")
    return tuple(sorted(nums)), None
