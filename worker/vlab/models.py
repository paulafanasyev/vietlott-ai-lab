"""Bản ghi quan sát: một nguồn nói gì về một kỳ quay."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from urllib.parse import urlparse

from .games import ValidationError, get_game, normalize_draw_no, validate_numbers


def _parse_time(value) -> time | None:
    if value is None or value == "":
        return None
    try:
        t = value if isinstance(value, time) else time.fromisoformat(str(value))
    except ValueError as exc:
        raise ValidationError(f"Giờ quay không hợp lệ: {value!r}") from exc
    if t.tzinfo is not None:
        # Giờ quay luôn là giờ Việt Nam không kèm múi giờ; không tự chuyển đổi để tránh đoán sai.
        raise ValidationError(f"Giờ quay không được kèm múi giờ: {value!r}")
    return t


def _valid_url(value, https_only: bool = False) -> bool:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        p = urlparse(value)
        port = p.port  # urlparse chỉ báo lỗi cổng khi đọc thuộc tính này
    except ValueError:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    schemes = ("https",) if https_only else ("https", "http")
    return (p.scheme in schemes and bool(p.hostname) and p.username is None and p.password is None
            and "@" not in p.netloc)


def _parse_observed_at(value) -> datetime:
    """Thời điểm ghi nhận: chuỗi ISO-8601 bắt buộc kèm múi giờ (thường là UTC)."""
    try:
        t = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        t = None
    if t is None or t.tzinfo is None:
        raise ValidationError(f"observed_at phải là ISO-8601 kèm múi giờ: {value!r}")
    return t.astimezone(timezone.utc)


@dataclass(frozen=True)
class Observation:
    source: str
    game: str
    draw_no: str
    draw_date: date
    main_numbers: tuple[int, ...]
    bonus_number: int | None
    source_url: str
    observed_at: str  # ISO-8601 UTC
    draw_time: time | None = None
    evidence_urls: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def build(cls, *, source: str, game: str, draw_no: object, draw_date: date | str, main_numbers,
              bonus_number, source_url: str, observed_at, draw_time=None, evidence_urls=(),
              extra: dict | None = None) -> "Observation":
        g = get_game(game)
        main, bonus = validate_numbers(g, main_numbers, bonus_number)
        if isinstance(draw_date, datetime):  # datetime là lớp con của date: không để giờ lọt vào
            raise ValidationError(f"Ngày quay phải là ngày, không phải thời điểm: {draw_date!r}")
        if isinstance(draw_date, date):
            d = draw_date
        elif not isinstance(draw_date, str):
            raise ValidationError(f"Ngày quay phải là chuỗi ISO hoặc date: {draw_date!r}")
        else:
            try:
                text = str(draw_date)
                d = date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text).date()
            except ValueError as exc:
                raise ValidationError(f"Ngày quay không hợp lệ: {draw_date!r}") from exc
        if not _valid_url(source_url):
            raise ValidationError(f"URL nguồn không hợp lệ: {source_url!r}")
        if not isinstance(evidence_urls, (list, tuple)):
            raise ValidationError(f"Danh sách URL bằng chứng sai kiểu: {evidence_urls!r}")
        bad_ev = [u for u in evidence_urls if not _valid_url(u, https_only=True)]
        if bad_ev:
            raise ValidationError(f"URL bằng chứng không hợp lệ: {bad_ev!r}")
        observed_at = _parse_observed_at(observed_at).isoformat()  # chuẩn hóa về UTC
        return cls(source=source, game=g.code, draw_no=normalize_draw_no(draw_no), draw_date=d,
                   main_numbers=main, bonus_number=bonus, source_url=source_url, observed_at=observed_at,
                   draw_time=_parse_time(draw_time),
                   evidence_urls=tuple(evidence_urls),
                   extra=extra or {})

    @property
    def key(self) -> tuple[str, str]:
        return (self.game, self.draw_no)

    def result_signature(self) -> tuple:
        """Phần dùng để so sánh giữa các nguồn: ngày, bộ số chính, số đặc biệt."""
        return (self.draw_date.isoformat(), self.main_numbers, self.bonus_number)

    def content_hash(self) -> str:
        """Băm nội dung kết quả (không gồm URL: đổi đường dẫn không phải là dữ liệu mới)."""
        payload = json.dumps({
            "source": self.source, "game": self.game, "draw_no": self.draw_no,
            "draw_date": self.draw_date.isoformat(),
            "draw_time": self.draw_time.isoformat() if self.draw_time else None,
            "main": list(self.main_numbers), "bonus": self.bonus_number,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def dedupe_batch(observations: list[Observation]) -> tuple[list[Observation], list[str]]:
    """Một lần tải mà cùng một kỳ xuất hiện với hai kết quả khác nhau: loại cả hai, ghi lý do."""
    by_key: dict[tuple, list[Observation]] = {}
    for o in observations:
        by_key.setdefault(o.key, []).append(o)
    kept, rejected = [], []
    for key, group in by_key.items():
        sigs = {(o.result_signature(), o.draw_time) for o in group}
        if len(sigs) > 1:
            rejected.append(f"kỳ {key[1]}: nguồn ghi {len(sigs)} kết quả khác nhau trong cùng một lần tải")
            continue
        kept.append(group[0])
    return kept, rejected
