"""Lịch quay (giờ Việt Nam, UTC+7, không có giờ mùa hè)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .games import Game

VN_TZ = timezone(timedelta(hours=7), name="ICT")
# Kết quả thường được công bố trong khoảng 18:00–18:30; sau mốc này mới coi là "đang chờ".
PUBLISH_GRACE = timedelta(hours=2)


def draw_datetime(game: Game, d: date) -> datetime:
    return datetime.combine(d, game.draw_time_local, tzinfo=VN_TZ)


def require_aware(moment: datetime) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"Thời điểm phải kèm múi giờ: {moment!r}")
    return moment


def next_draw_after(game: Game, moment: datetime) -> datetime:
    """Giờ quay đầu tiên nghiêm ngặt sau thời điểm `moment` (bắt buộc kèm múi giờ)."""
    require_aware(moment)
    local = moment.astimezone(VN_TZ)
    for offset in range(0, 8):
        d = local.date() + timedelta(days=offset)
        if d.weekday() in game.draw_weekdays:
            dt = draw_datetime(game, d)
            if dt > moment:
                return dt
    raise AssertionError("không tìm thấy kỳ quay trong 7 ngày")


def scheduled_after(game: Game, last_draw_date: date, now: datetime) -> list[datetime]:
    """Các giờ quay theo lịch sau ngày của kỳ cuối cùng đã biết và trước `now - PUBLISH_GRACE`."""
    require_aware(now)
    out = []
    cursor = draw_datetime(game, last_draw_date)
    while True:
        cursor = next_draw_after(game, cursor)
        if cursor + PUBLISH_GRACE > now:
            return out
        out.append(cursor)
