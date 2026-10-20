"""Kiểm định walk-forward: với mỗi kỳ t, mọi phương pháp chỉ dùng các kỳ < t để chọn vé, rồi so với kỳ t.

So sánh với xác suất lý thuyết của vé ngẫu nhiên (phân phối siêu bội) và với M0 thực nghiệm.
Kiểm định z cho số trúng trung bình, hiệu chỉnh Bonferroni cho 7 phương pháp không ngẫu nhiên.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import comb

from .methods import CODES, METHODS, State, all_tickets

WARMUP = 100  # số kỳ đầu chỉ dùng để học, không tính điểm
ALPHA = 0.05


def match_probs(max_number: int, k: int = 6) -> dict[int, float]:
    """P(trúng đúng m số chính) của một vé 6 số ngẫu nhiên."""
    total = comb(max_number, k)
    return {m: comb(k, m) * comb(max_number - k, k - m) / total for m in range(k + 1)}


def one_in(p: float) -> int:
    return round(1 / p) if p > 0 else 0


def goal_odds(max_number: int) -> dict[str, dict]:
    """Bốn mục tiêu của Павел: độc đắc (6), 5 số, 4 số, 3 số — xác suất cho một vé ngẫu nhiên."""
    p = match_probs(max_number)
    return {str(m): {"p": p[m], "one_in": one_in(p[m])} for m in (6, 5, 4, 3)}


def _norm_sf2(z: float) -> float:
    """p hai phía cho phân phối chuẩn."""
    return math.erfc(abs(z) / math.sqrt(2))


@dataclass
class MethodResult:
    code: str
    name_vi: str
    description_vi: str
    draws: int = 0
    hits_sum: int = 0
    counts: dict[int, int] = field(default_factory=lambda: {m: 0 for m in range(7)})

    def as_dict(self, max_number: int, n_tests: int) -> dict:
        p = match_probs(max_number)
        mu = 6 * 6 / max_number
        var = 6 * 6 * (max_number - 6) * (max_number - 6) / (max_number ** 2 * (max_number - 1))
        mean = self.hits_sum / self.draws if self.draws else 0.0
        z = (mean - mu) / math.sqrt(var / self.draws) if self.draws else 0.0
        pval = _norm_sf2(z) if self.draws else 1.0
        better = self.code != "M0" and pval < ALPHA / n_tests and z > 0
        worse = self.code != "M0" and pval < ALPHA / n_tests and z < 0
        verdict = ("TỐT HƠN ngẫu nhiên (có ý nghĩa thống kê)" if better else
                   "KÉM HƠN ngẫu nhiên (có ý nghĩa thống kê)" if worse else
                   "đường cơ sở" if self.code == "M0" else "không khác ngẫu nhiên")
        goals = {}
        for m in (3, 4, 5, 6):
            actual = sum(v for k, v in self.counts.items() if k >= m) if m < 6 else self.counts[6]
            p_at_least = sum(p[k] for k in range(m, 7)) if m < 6 else p[6]
            goals[str(m)] = {
                "actual": actual,
                "expected": round(p_at_least * self.draws, 2),
                "actual_rate": actual / self.draws if self.draws else 0.0,
                "random_rate": p_at_least,
                "random_one_in": one_in(p_at_least),
            }
        return {"code": self.code, "name_vi": self.name_vi, "description_vi": self.description_vi,
                "draws": self.draws, "mean_hits": round(mean, 4), "random_mean_hits": round(mu, 4),
                "z": round(z, 3), "p_value": round(pval, 4), "verdict_vi": verdict, "goals": goals,
                "counts": {str(k): v for k, v in self.counts.items()}}


def run_backtest(game_code: str, max_number: int, draws: list[tuple[int, tuple[int, ...]]],
                 warmup: int = WARMUP) -> dict:
    """draws: [(số kỳ, 6 số chính)] theo thứ tự tăng dần của số kỳ, CHỈ các kỳ dùng được."""
    draws = sorted(draws)
    state = State(max_number)
    results = {m.code: MethodResult(m.code, m.name_vi, m.description_vi) for m in METHODS}
    for i, (draw_no, nums) in enumerate(draws):
        if i >= warmup:
            actual = set(nums)
            for code, t in all_tickets(state, f"{game_code}|{draw_no}").items():
                hits = len(actual.intersection(t))
                r = results[code]
                r.draws += 1
                r.hits_sum += hits
                r.counts[hits] += 1
        state.add(tuple(nums))
    n_tests = len(CODES) - 1
    next_no = draws[-1][0] + 1 if draws else 1
    tickets = all_tickets(state, f"{game_code}|{next_no}") if draws else {}
    return {
        "game": game_code, "max_number": max_number, "draws_used": len(draws), "warmup": warmup,
        "first_draw": f"{draws[0][0]:05d}" if draws else None, "last_draw": f"{draws[-1][0]:05d}" if draws else None,
        "alpha": ALPHA, "bonferroni_tests": n_tests, "goal_odds": goal_odds(max_number),
        "methods": [results[c].as_dict(max_number, n_tests) for c in CODES],
        "next_draw_no": f"{next_no:05d}",
        "next_tickets": [{"code": c, "name_vi": next(m.name_vi for m in METHODS if m.code == c),
                          "numbers": list(tickets[c])} for c in CODES] if draws else [],
        "note_vi": ("Mỗi vé, dù chọn bằng phương pháp nào, đều có cùng xác suất trúng như vé ngẫu nhiên. "
                    "Bảng trên đo xem phương pháp có khác ngẫu nhiên trong quá khứ hay không; "
                    "kết quả quá khứ không bảo đảm tương lai."),
    }
