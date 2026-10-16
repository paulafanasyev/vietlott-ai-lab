"""Đối chiếu các nguồn và quyết định trạng thái kiểm chứng của một kỳ quay.

- CONFLICT: hai nguồn bất kỳ khác nhau về ngày, bộ số chính hoặc số đặc biệt.
- VERIFIED: nguồn chính thức khớp với ít nhất một kho lưu trữ thuộc NHÓM NGUỒN GỐC khác.
- CROSS-CHECKED: không có nguồn chính thức, nhưng ít nhất hai nhóm nguồn gốc độc lập khớp nhau.
- NOT PROVEN: chỉ có một nhóm nguồn gốc.
Thiếu dữ liệu ở một nguồn KHÔNG phải xung đột (trang chính thức có thể trả về bản lưu cũ).
Các kho sao chép lẫn nhau được khai báo cùng provenance_group và chỉ tính là một xác nhận.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Iterable, Mapping

from .models import Observation

VERIFIED = "VERIFIED"
CROSS_CHECKED = "CROSS-CHECKED"
NOT_PROVEN = "NOT PROVEN"
CONFLICT = "CONFLICT"
PENDING = "PENDING"
USABLE = frozenset({VERIFIED, CROSS_CHECKED})


@dataclass(frozen=True)
class SourceInfo:
    kind: str   # official / archive / secondary
    group: str  # provenance_group


@dataclass(frozen=True)
class Decision:
    status: str
    observation: Observation | None  # bộ số được chấp nhận (None khi CONFLICT/PENDING)
    sources: tuple[str, ...]
    reason_vi: str
    draw_time: time | None = None
    draw_time_source: str | None = None  # 'source' | 'schedule'


def decide(observations: Iterable[Observation], sources: Mapping[str, SourceInfo],
           scheduled_time: time | None = None) -> Decision:
    obs = list(observations)
    if not obs:
        return Decision(PENDING, None, (), "Chưa có nguồn nào công bố kết quả")
    keys = {o.key for o in obs}
    if len(keys) != 1:
        raise ValueError(f"Các quan sát thuộc nhiều kỳ khác nhau: {sorted(keys)}")
    for o in obs:
        if o.source not in sources:
            raise ValueError(f"Nguồn chưa đăng ký: {o.source}")

    by_source: dict[str, set[tuple]] = {}
    for o in obs:
        by_source.setdefault(o.source, set()).add(o.result_signature())
    names = tuple(sorted(by_source))
    signatures = set().union(*by_source.values())
    if len(signatures) > 1:
        detail = "; ".join(f"{s}: {_fmt(sig)}" for s in names for sig in sorted(by_source[s], key=repr))
        return Decision(CONFLICT, None, names, f"Các nguồn mâu thuẫn — {detail}")

    official = [o for o in obs if sources[o.source].kind == "official"]
    accepted = official[0] if official else obs[0]
    # Giờ quay: ưu tiên giờ do nguồn công bố; hai nguồn công bố giờ khác nhau cũng là mâu thuẫn.
    times = {o.draw_time for o in obs if o.draw_time is not None}
    if len(times) > 1:
        return Decision(CONFLICT, None, names, "Các nguồn công bố giờ quay khác nhau: "
                        + ", ".join(t.isoformat() for t in sorted(times)))
    draw_time, time_src = (times.pop(), "source") if times else (scheduled_time, "schedule" if scheduled_time else None)

    groups_official = {sources[s].group for s in names if sources[s].kind == "official"}
    groups_archive = {sources[s].group for s in names if sources[s].kind == "archive"} - groups_official
    if groups_official and groups_archive:
        return Decision(VERIFIED, accepted, names,
                        f"Nguồn chính thức khớp với {len(groups_archive)} kho lưu trữ độc lập ({', '.join(names)})",
                        draw_time, time_src)
    if len(groups_archive) >= 2:
        return Decision(CROSS_CHECKED, accepted, names,
                        f"{len(groups_archive)} kho lưu trữ độc lập khớp nhau ({', '.join(names)}); "
                        "chưa có xác nhận trực tiếp từ nguồn chính thức", draw_time, time_src)
    return Decision(NOT_PROVEN, accepted, names,
                    f"Chỉ có một nhóm nguồn gốc ({', '.join(names)}); cần thêm xác nhận", draw_time, time_src)


def _fmt(sig: tuple) -> str:
    d, main, bonus = sig
    nums = " ".join(f"{n:02d}" for n in main)
    return f"{d} [{nums}]" + (f" + {bonus:02d}" if bonus is not None else "")
