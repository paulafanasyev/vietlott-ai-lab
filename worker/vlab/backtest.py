"""Kiểm định walk-forward: với mỗi kỳ t, mọi phương pháp chỉ dùng các kỳ < t để chọn vé, rồi so với kỳ t.

So sánh với xác suất lý thuyết của vé ngẫu nhiên (phân phối siêu bội) và với M0 thực nghiệm.
Kiểm định z hai phía cho số trúng trung bình; báo cáo khoảng tin cậy 95% và hiệu chỉnh Holm step-down.
Giữ Bonferroni để đối chiếu tương thích với báo cáo cũ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import comb

from .methods import CODES, METHODS, State, all_tickets, learn_weights

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


def _holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjustment; controls family-wise error under arbitrary dependence."""
    ranked = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running_max = 0.0
    count = len(ranked)
    for index, (code, p_value) in enumerate(ranked):
        candidate = min(1.0, (count - index) * p_value)
        running_max = max(running_max, candidate)
        adjusted[code] = running_max
    return adjusted


def _binomial_sf(k: int, n: int, probability: float) -> float:
    """Exact P[X >= k] for X~Binomial(n,p), evaluated from the first tail term."""
    if n <= 0 or k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if not 0.0 < probability < 1.0:
        return 1.0 if probability >= 1.0 else 0.0
    log_pmf = (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
               + k * math.log(probability) + (n - k) * math.log1p(-probability))
    term = math.exp(log_pmf)
    total = term
    for x in range(k, n):
        term *= ((n - x) / (x + 1)) * (probability / (1.0 - probability))
        total += term
    return min(1.0, total)


@dataclass
class MethodResult:
    code: str
    name_vi: str
    description_vi: str
    draws: int = 0
    hits_sum: int = 0
    counts: dict[int, int] = field(default_factory=lambda: {m: 0 for m in range(7)})
    special_hits: int = 0
    jackpot2_hits: int = 0

    def as_dict(self, max_number: int, n_tests: int,
                p_holm: float = 1.0, special_holm: float = 1.0) -> dict:
        p = match_probs(max_number)
        mu = 6 * 6 / max_number
        var = 6 * 6 * (max_number - 6) * (max_number - 6) / (max_number ** 2 * (max_number - 1))
        mean = self.hits_sum / self.draws if self.draws else 0.0
        z = (mean - mu) / math.sqrt(var / self.draws) if self.draws else 0.0
        pval = _norm_sf2(z) if self.draws else 1.0
        effect = mean - mu
        standard_error = math.sqrt(var / self.draws) if self.draws else 0.0
        ci_margin = 1.959963984540054 * standard_error
        better = self.code != "M0" and pval < ALPHA / n_tests and z > 0
        holm_better = self.code != "M0" and p_holm < ALPHA and z > 0
        holm_worse = self.code != "M0" and p_holm < ALPHA and z < 0
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
        out = {"code": self.code, "name_vi": self.name_vi, "description_vi": self.description_vi,
               "draws": self.draws, "mean_hits": round(mean, 4), "random_mean_hits": round(mu, 4),
               "effect_vs_random": round(effect, 4),
               "effect_ci95": {"lower": round(effect - ci_margin, 4),
                               "upper": round(effect + ci_margin, 4)},
               "z": round(z, 3), "p_value": round(pval, 4),
               "p_value_bonferroni": round(min(1.0, pval * n_tests), 6),
               "p_value_holm": round(p_holm, 6), "verdict_vi": verdict,
               "verdict_holm_vi": ("TỐT HƠN ngẫu nhiên (Holm-adjusted)" if holm_better else
                                   "KÉM HƠN ngẫu nhiên (Holm-adjusted)" if holm_worse else
                                   "đường cơ sở" if self.code == "M0" else "không khác ngẫu nhiên"),
               "goals": goals, "counts": {str(k): v for k, v in self.counts.items()}}
        if max_number == 55:
            jackpot2_p = p[5] / (max_number - 6)
            special_probability = 6 / max_number
            special_p = _binomial_sf(self.special_hits, self.draws, special_probability)
            special_adjusted = min(1.0, special_p * n_tests)
            special_expected = self.draws * special_probability
            special_holm_better = (self.code != "M0" and self.special_hits > special_expected
                                   and special_holm < ALPHA)
            special_holm_worse = (self.code != "M0" and self.special_hits < special_expected
                                  and special_holm < ALPHA)
            special_better = self.code != "M0" and self.special_hits > special_expected and special_adjusted < ALPHA
            out["special_number"] = {
                "actual": self.special_hits,
                "expected": round(special_expected, 3),
                "actual_rate": self.special_hits / self.draws if self.draws else 0.0,
                "random_rate": special_probability,
                "random_one_in": one_in(special_probability),
                "p_value_one_sided": round(special_p, 6),
                "p_value_bonferroni": round(special_adjusted, 6),
                "p_value_holm": round(special_holm, 6),
                "verdict_vi": ("TỐT HƠN ngẫu nhiên (có ý nghĩa thống kê)" if special_better else
                               "đường cơ sở" if self.code == "M0" else "không khác ngẫu nhiên"),
                "verdict_holm_vi": ("TỐT HƠN ngẫu nhiên (Holm-adjusted)" if special_holm_better else
                                    "KÉM HƠN ngẫu nhiên (Holm-adjusted)" if special_holm_worse else
                                    "đường cơ sở" if self.code == "M0" else "không khác ngẫu nhiên"),
            }
            out["jackpot2"] = {
                "actual": self.jackpot2_hits,
                "expected": round(jackpot2_p * self.draws, 3),
                "actual_rate": self.jackpot2_hits / self.draws if self.draws else 0.0,
                "random_rate": jackpot2_p,
                "random_one_in": one_in(jackpot2_p),
            }
        return out


def run_backtest(game_code: str, max_number: int, draws: list[tuple],
                 warmup: int = WARMUP) -> dict:
    """draws: [(số kỳ, 6 số chính[, số đặc biệt])] theo thứ tự tăng dần; chỉ các kỳ dùng được."""
    draws = sorted(draws, key=lambda row: row[0])
    state = State(max_number)
    results = {m.code: MethodResult(m.code, m.name_vi, m.description_vi) for m in METHODS}
    for i, row in enumerate(draws):
        draw_no, nums = row[0], tuple(row[1])
        bonus_number = row[2] if len(row) > 2 else None
        if i >= warmup:
            actual = set(nums)
            for code, t in all_tickets(state, f"{game_code}|{draw_no}").items():
                hits = len(actual.intersection(t))
                r = results[code]
                r.draws += 1
                r.hits_sum += hits
                r.counts[hits] += 1
                if max_number == 55 and bonus_number is not None:
                    special_hit = bonus_number in t
                    r.special_hits += int(special_hit)
                    r.jackpot2_hits += int(hits == 5 and special_hit)
        state.add(tuple(nums), seed_key=f"{game_code}|{draw_no}")
    n_tests = len(CODES) - 1
    # Adjust each family separately: main-number mean and Power special-number rate.
    # M0 is a control, not one of the candidate methods in either family.
    main_raw_p: dict[str, float] = {}
    special_raw_p: dict[str, float] = {}
    for code in CODES:
        if code == "M0":
            continue
        r = results[code]
        mean = r.hits_sum / r.draws if r.draws else 0.0
        mu = 36 / max_number
        variance = (36 * (max_number - 6) * (max_number - 6)
                    / (max_number * max_number * (max_number - 1)))
        z = ((mean - mu) / math.sqrt(variance / r.draws)) if r.draws else 0.0
        main_raw_p[code] = _norm_sf2(z) if r.draws else 1.0
        if max_number == 55:
            special_raw_p[code] = _binomial_sf(r.special_hits, r.draws, 6 / max_number)
    holm_main = _holm_adjust(main_raw_p)
    holm_special = _holm_adjust(special_raw_p)
    next_no = draws[-1][0] + 1 if draws else 1
    tickets = all_tickets(state, f"{game_code}|{next_no}") if draws else {}
    return {
        "game": game_code, "max_number": max_number, "draws_used": len(draws), "warmup": warmup,
        "first_draw": f"{draws[0][0]:05d}" if draws else None, "last_draw": f"{draws[-1][0]:05d}" if draws else None,
        "alpha": ALPHA, "bonferroni_tests": n_tests,
        "multiple_testing": {"method": "Holm step-down", "family_size": n_tests, "alpha": ALPHA,
                             "families": ["main-number mean", "Power special-number rate"]},
        "confidence_interval": "95% normal interval for mean-hit effect versus theoretical random expectation",
        "goal_odds": goal_odds(max_number),
        "power_jackpot2_odds": ({"p": match_probs(55)[5] / 49, "one_in": one_in(match_probs(55)[5] / 49)}
                                if max_number == 55 else None),
        "methods": [results[c].as_dict(max_number, n_tests,
                                       holm_main.get(c, 1.0), holm_special.get(c, 1.0))
                    for c in CODES],
        "next_draw_no": f"{next_no:05d}",
        "learn_weights": {c: round(w, 4) for c, w in learn_weights(state).items()},
        "next_tickets": [{"code": c, "name_vi": next(m.name_vi for m in METHODS if m.code == c),
                          "numbers": list(tickets[c])} for c in CODES] if draws else [],
        "note_vi": ("Mỗi vé, dù chọn bằng phương pháp nào, đều có cùng xác suất trúng như vé ngẫu nhiên. "
                    "Bảng trên đo xem phương pháp có khác ngẫu nhiên trong quá khứ hay không; "
                    "kết quả quá khứ không bảo đảm tương lai."),
    }
