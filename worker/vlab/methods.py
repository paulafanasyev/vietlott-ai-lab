"""Các phương pháp chọn số (giai đoạn 2). Mỗi phương pháp chỉ nhìn các kỳ TRƯỚC kỳ cần dự đoán.

Mỗi phương pháp trả về điểm cho từng số 1..N; vé = 6 số điểm cao nhất (hòa điểm phá bằng số ngẫu nhiên
có hạt giống cố định theo kỳ, để kết quả lặp lại được). Không phương pháp nào được giả định là tốt hơn
ngẫu nhiên: điều đó chỉ được kết luận từ kiểm định walk-forward (backtest.py).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Sequence

Draw = tuple[int, ...]  # 6 số chính của một kỳ


@dataclass(frozen=True)
class Method:
    code: str
    name_vi: str
    description_vi: str


METHODS: tuple[Method, ...] = (
    Method("M0", "Ngẫu nhiên (đường cơ sở)", "Chọn 6 số ngẫu nhiên; mọi phương pháp khác phải vượt được mốc này."),
    Method("M1", "Số nóng (toàn bộ lịch sử)", "6 số xuất hiện nhiều nhất từ trước tới nay."),
    Method("M2", "Số nóng 50 kỳ gần nhất", "6 số xuất hiện nhiều nhất trong 50 kỳ gần nhất."),
    Method("M3", "Số lạnh 50 kỳ gần nhất", "6 số xuất hiện ít nhất trong 50 kỳ gần nhất."),
    Method("M4", "Số lâu chưa về", "6 số đã lâu nhất chưa xuất hiện."),
    Method("M5", "Tần suất có trọng số thời gian", "Tần suất giảm dần theo thời gian (chu kỳ bán rã 30 kỳ)."),
    Method("M6", "Cặp số đi cùng kỳ trước", "Các số hay xuất hiện cùng với các số của kỳ vừa quay."),
    Method("M7", "Tổng hợp M1–M6", "Trung bình thứ hạng của M1–M6."),
)
CODES = tuple(m.code for m in METHODS)


class State:
    """Trạng thái tăng dần: cập nhật sau mỗi kỳ, để walk-forward chạy O(kỳ × N)."""

    def __init__(self, max_number: int, window: int = 50, half_life: float = 30.0):
        self.n = max_number
        self.window = window
        self.decay = 0.5 ** (1.0 / half_life)
        self.history: list[Draw] = []
        self.total = [0] * (max_number + 1)
        self.recent = [0] * (max_number + 1)
        self.weighted = [0.0] * (max_number + 1)
        self.last_seen = [-1] * (max_number + 1)
        self.pairs = [[0] * (max_number + 1) for _ in range(max_number + 1)]

    def add(self, draw: Draw) -> None:
        t = len(self.history)
        self.history.append(draw)
        for x in range(1, self.n + 1):
            self.weighted[x] *= self.decay
        for x in draw:
            self.total[x] += 1
            self.recent[x] += 1
            self.weighted[x] += 1.0
            self.last_seen[x] = t
        for a in draw:
            row = self.pairs[a]
            for b in draw:
                if a != b:
                    row[b] += 1
        if len(self.history) > self.window:
            for x in self.history[-self.window - 1]:
                self.recent[x] -= 1

    # điểm: càng cao càng được chọn
    def scores(self, code: str) -> list[float]:
        n, t = self.n, len(self.history)
        if code == "M1":
            return [float(self.total[x]) for x in range(n + 1)]
        if code == "M2":
            return [float(self.recent[x]) for x in range(n + 1)]
        if code == "M3":
            return [-float(self.recent[x]) for x in range(n + 1)]
        if code == "M4":
            return [float(t - self.last_seen[x]) for x in range(n + 1)]
        if code == "M5":
            return list(self.weighted)
        if code == "M6":
            last = self.history[-1] if self.history else ()
            return [float(sum(self.pairs[x][y] for y in last)) if x not in last else -1.0 for x in range(n + 1)]
        raise KeyError(code)


def _rng(seed_key: str) -> random.Random:
    return random.Random(seed_key)


def _top6(scores: Sequence[float], n: int, rng: random.Random) -> tuple[int, ...]:
    jitter = [rng.random() for _ in range(n + 1)]
    order = sorted(range(1, n + 1), key=lambda x: (-scores[x], jitter[x]))
    return tuple(sorted(order[:6]))


def ticket(state: State, code: str, seed_key: str) -> tuple[int, ...]:
    """Vé 6 số của phương pháp `code` cho kỳ kế tiếp sau các kỳ trong `state`."""
    n = state.n
    rng = _rng(f"{seed_key}|{code}")
    if code == "M0":
        return tuple(sorted(rng.sample(range(1, n + 1), 6)))
    if code == "M7":
        ranks = [0.0] * (n + 1)
        for c in ("M1", "M2", "M3", "M4", "M5", "M6"):
            s = state.scores(c)
            j = _rng(f"{seed_key}|{c}|rank")
            jit = [j.random() for _ in range(n + 1)]
            order = sorted(range(1, n + 1), key=lambda x: (-s[x], jit[x]))
            for r, x in enumerate(order):
                ranks[x] += r
        return _top6([-r for r in ranks], n, rng)
    return _top6(state.scores(code), n, rng)


def all_tickets(state: State, seed_key: str) -> dict[str, tuple[int, ...]]:
    return {code: ticket(state, code, seed_key) for code in CODES}


Builder = Callable[[State, str], dict[str, tuple[int, ...]]]
