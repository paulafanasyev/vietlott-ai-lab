"""Các phương pháp chọn số (giai đoạn 2). Mỗi phương pháp chỉ nhìn các kỳ TRƯỚC kỳ cần dự đoán.

Mỗi phương pháp trả về điểm cho từng số 1..N; vé = 6 số điểm cao nhất (hòa điểm phá bằng số ngẫu nhiên
có hạt giống cố định theo kỳ, để kết quả lặp lại được). Không phương pháp nào được giả định là tốt hơn
ngẫu nhiên: điều đó chỉ được kết luận từ kiểm định walk-forward (backtest.py).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

Draw = tuple[int, ...]  # 6 số chính của một kỳ
ML_FEATURES = 5
ML_RATE = 0.05
ML_L2 = 0.01


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
    Method("M8", "Tự học (trọng số thích nghi)",
           "Kết hợp M1–M6 với trọng số tự cập nhật sau mỗi kỳ theo thành tích gần đây (thuật toán Hedge)."),
    Method("M9", "Học máy (hồi quy logistic)",
           "Mô hình hồi quy logistic học trực tuyến sau mỗi kỳ: dự đoán khả năng xuất hiện của từng số từ tần suất "
           "gần đây, tần suất có trọng số, khoảng cách từ lần về cuối, tần suất toàn bộ và mức đi cùng kỳ trước."),
    Method("M10", "Vé cân bằng",
           "Vé ngẫu nhiên có cấu trúc điển hình: tổng nằm giữa 25%–75% các kỳ trước, 2–4 số lẻ, 2–4 số nhỏ, "
           "không quá 2 số liên tiếp."),
    Method("M11", "Số ít người chọn",
           "Ưu tiên số lớn hơn 31 (ngoài ngày sinh) và tránh dãy/mẫu dễ đoán. Không tăng xác suất trúng, nhưng nếu "
           "trúng độc đắc thì ít người phải chia giải hơn."),
    Method("M12", "Phủ bộ vé",
           "Chọn các số ít xuất hiện nhất trong vé của các phương pháp khác cùng kỳ, để cả bộ vé phủ nhiều số hơn. "
           "Không đổi xác suất của từng vé; chỉ tăng nhẹ khả năng có ít nhất một vé trúng 3 số."),
    Method("M13", "Chuỗi Markov (lặp lại kỳ trước)",
           "Ước lượng xác suất mỗi số về kỳ tới tùy theo nó có về ở kỳ vừa quay hay không (chuỗi Markov 2 trạng thái)."),
    Method("M14", "Lấy mẫu Bayes (Thompson)",
           "Mỗi số có hậu nghiệm Beta-Bernoulli: tính cả số lần xuất hiện và vắng mặt; lấy mẫu xác suất rồi chọn 6 số cao nhất."),
    Method("M15", "Chu kỳ riêng của từng số",
           "Với khoảng cách hiện tại từ lần về cuối, tính tỷ lệ «về ngay ở khoảng cách này» theo lịch sử riêng của từng số."),
    Method("M16", "Bỏ phiếu hợp nhất đa phương pháp",
           "Tổng hợp phiếu có trọng số từ M1–M11, M13–M15, gồm cả hai tổ hợp M7/M8; trọng số tự cập nhật theo kết quả các kỳ trước. Loại vé ngẫu nhiên M0 và vé phủ ngược M12 khỏi đồng thuận."),
)
BASE = ("M1", "M2", "M3", "M4", "M5", "M6")
FAMILY = {"M0": "random", "M1": "statistics", "M2": "statistics", "M3": "statistics", "M4": "statistics",
          "M5": "timeseries", "M6": "statistics", "M7": "ensemble", "M8": "ensemble",
          "M9": "ml", "M10": "montecarlo", "M11": "statistics", "M12": "coverage",
          "M13": "timeseries", "M14": "montecarlo", "M15": "timeseries", "M16": "ensemble"}
CODES = tuple(m.code for m in METHODS)
# M16 bỏ phiếu cho các phương pháp dự báo và hai tổ hợp M7/M8.
# Loại M0 (ngẫu nhiên), M12 (cố ý phủ số ít được chọn) và chính M16 để tránh tự bỏ phiếu.
VOTERS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M13", "M14", "M15")
EXTRA = tuple(c for c in VOTERS if c not in BASE)


class State:
    """Trạng thái tăng dần: cập nhật sau mỗi kỳ, để walk-forward chạy O(kỳ × N)."""

    def __init__(self, max_number: int, window: int = 50, half_life: float = 30.0,
                 learn_half_life: float = 50.0, learn_rate: float = 0.5):
        self.n = max_number
        # Tự học: điểm của mỗi phương pháp cơ sở = tổng (số trúng − kỳ vọng ngẫu nhiên), giảm dần theo thời gian.
        self.learn_decay = 0.5 ** (1.0 / learn_half_life)
        self.learn_rate = learn_rate
        self.learn_score = {c: 0.0 for c in BASE}
        self.learn_hits = {c: 0 for c in BASE}
        self.window = window
        self.decay = 0.5 ** (1.0 / half_life)
        self.history: list[Draw] = []
        self.total = [0] * (max_number + 1)
        self.recent = [0] * (max_number + 1)
        self.weighted = [0.0] * (max_number + 1)
        self.last_seen = [-1] * (max_number + 1)
        self.pairs = [[0] * (max_number + 1) for _ in range(max_number + 1)]
        # M9: hồi quy logistic dùng chung cho mọi số, học trực tuyến (SGD) trước khi ghi nhận mỗi kỳ.
        self.ml_w = [0.0] * (ML_FEATURES + 1)
        self.sums: list[int] = []
        # M13: đếm chuyển trạng thái (về/không về ở kỳ trước → về/không về ở kỳ này).
        self.mk = [[0, 0, 0, 0] for _ in range(max_number + 1)]  # [lần ở trạng thái «vừa về», trong đó về lại, «không về», trong đó về]
        # M15: phân bố khoảng cách giữa hai lần về của từng số.
        self.gaps = [dict() for _ in range(max_number + 1)]
        # M16: điểm tự học cho các phương pháp ngoài BASE (BASE dùng learn_score như M8).
        self.ext_score = {c: 0.0 for c in EXTRA}

    def add(self, draw: Draw, seed_key: str | None = None) -> None:
        t = len(self.history)
        # Use the exact seed key used when the method's ticket was generated for this draw.
        # Otherwise keep a deterministic fallback for generic callers/tests.
        training_key = seed_key if seed_key is not None else f"learn|{t}"
        # Score every ticket from the same pre-draw state before changing any learner weights.
        # This is especially important for M8 and M16: updating BASE scores first would leak
        # the just-observed draw into the ticket used to score an aggregate method.
        if self.history:
            mu = 36 / self.n
            actual = set(draw)
            scored = {
                c: len(actual.intersection(ticket(self, c, training_key)))
                for c in (*BASE, *EXTRA)
            }
            for c in BASE:
                hits = scored[c]
                self.learn_hits[c] += hits
                self.learn_score[c] = self.learn_score[c] * self.learn_decay + (hits - mu)
            for c in EXTRA:
                hits = scored[c]
                self.ext_score[c] = self.ext_score[c] * self.learn_decay + (hits - mu)
            prev = set(self.history[-1])
            for x in range(1, self.n + 1):
                m = self.mk[x]
                if x in prev:
                    m[0] += 1
                    m[1] += x in actual
                else:
                    m[2] += 1
                    m[3] += x in actual
        for x in draw:
            if self.last_seen[x] >= 0:
                g = t - self.last_seen[x]
                self.gaps[x][g] = self.gaps[x].get(g, 0) + 1
        if len(self.history) >= 10:
            fm = self.feature_matrix()
            grad = [0.0] * len(self.ml_w)
            for x in range(1, self.n + 1):
                f = fm[x]
                z = sum(w * v for w, v in zip(self.ml_w, f))
                p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
                g = (1.0 if x in actual else 0.0) - p
                for i, v in enumerate(f):
                    grad[i] += g * v
            for i in range(len(self.ml_w)):
                self.ml_w[i] += ML_RATE * (grad[i] / self.n - ML_L2 * self.ml_w[i] * (i > 0))
        self.sums.append(sum(draw))
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

    def feature_matrix(self) -> list[list[float]]:
        """Đặc trưng chuẩn hóa (z-score theo các số) chỉ từ các kỳ đã ghi nhận; hàng 0 bỏ trống."""
        n, t = self.n, len(self.history)
        last = self.history[-1] if self.history else ()
        raw = []
        for x in range(1, n + 1):
            gap = (t - self.last_seen[x]) if self.last_seen[x] >= 0 else t
            raw.append([float(self.recent[x]), self.weighted[x], float(min(gap, 60)), float(self.total[x]),
                        float(sum(self.pairs[x][y] for y in last))])
        cols = list(zip(*raw))
        stats = []
        for c in cols:
            m = sum(c) / len(c)
            sd = math.sqrt(sum((v - m) ** 2 for v in c) / len(c)) or 1.0
            stats.append((m, sd))
        out = [[]]
        for r in raw:
            out.append([1.0] + [(v - m) / sd for v, (m, sd) in zip(r, stats)])
        return out

    def ml_scores(self) -> list[float]:
        fm = self.feature_matrix()
        return [0.0] + [sum(w * v for w, v in zip(self.ml_w, fm[x])) for x in range(1, self.n + 1)]

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
        if code == "M9":
            return self.ml_scores()
        if code == "M13":
            last = set(self.history[-1]) if self.history else set()
            out = [0.0]
            for x in range(1, n + 1):
                m = self.mk[x]
                out.append((m[1] + 1) / (m[0] + 2) if x in last else (m[3] + 1) / (m[2] + 2))
            return out
        if code == "M15":
            out = [0.0]
            for x in range(1, n + 1):
                cur = (t - self.last_seen[x]) if self.last_seen[x] >= 0 else t
                h = self.gaps[x]
                at = h.get(cur, 0)
                later = sum(v for g, v in h.items() if g >= cur)
                out.append((at + 0.5) / (later + 0.5 * cur + 1.0))
            return out
        raise KeyError(code)


def learn_weights(state: State) -> dict[str, float]:
    m = max(state.learn_score.values())
    raw = {c: math.exp(state.learn_rate * (v - m)) for c, v in state.learn_score.items()}
    total = sum(raw.values())
    return {c: raw[c] / total for c in BASE}


def vote_weights(state: State) -> dict[str, float]:
    """Trọng số Hedge trên M1–M15 (điểm BASE dùng chung với M8)."""
    sc = {**{c: state.learn_score[c] for c in BASE}, **state.ext_score}
    m = max(sc.values())
    raw = {c: math.exp(state.learn_rate * (sc[c] - m)) for c in VOTERS}
    total = sum(raw.values())
    return {c: raw[c] / total for c in VOTERS}


def _rng(seed_key: str) -> random.Random:
    return random.Random(seed_key)


def _top6(scores: Sequence[float], n: int, rng: random.Random) -> tuple[int, ...]:
    jitter = [rng.random() for _ in range(n + 1)]
    order = sorted(range(1, n + 1), key=lambda x: (-scores[x], jitter[x]))
    return tuple(sorted(order[:6]))


def m14_posterior_params(state: State, number: int) -> tuple[float, float]:
    """Tham số hậu nghiệm Beta-Bernoulli cho xác suất một số xuất hiện trong một kỳ.

    Prior mean = 6/N và prior strength = 20 kỳ tương đương. Mọi kỳ đã biết đều đóng góp
    một quan sát: xuất hiện (success) hoặc vắng mặt (failure).
    """
    prior_strength = 20.0
    prior_mean = 6.0 / state.n
    alpha = prior_strength * prior_mean + state.total[number]
    beta = prior_strength * (1.0 - prior_mean) + len(state.history) - state.total[number]
    return alpha, beta


def ticket(state: State, code: str, seed_key: str) -> tuple[int, ...]:
    """Vé 6 số của phương pháp `code` cho kỳ kế tiếp sau các kỳ trong `state`."""
    n = state.n
    rng = _rng(f"{seed_key}|{code}")
    if code == "M0":
        return tuple(sorted(rng.sample(range(1, n + 1), 6)))
    if code in ("M7", "M8"):
        weights = learn_weights(state) if code == "M8" else {c: 1.0 for c in BASE}
        ranks = [0.0] * (n + 1)
        for c in BASE:
            s = state.scores(c)
            j = _rng(f"{seed_key}|{c}|rank")
            jit = [j.random() for _ in range(n + 1)]
            order = sorted(range(1, n + 1), key=lambda x: (-s[x], jit[x]))
            for r, x in enumerate(order):
                ranks[x] += weights[c] * r
        return _top6([-r for r in ranks], n, rng)
    if code == "M10":
        return _balanced(state, rng)
    if code == "M11":
        return _unpopular(state, rng)
    if code == "M14":
        # Thompson sampling for Bernoulli inclusion probabilities. Beta accounts for both
        # observed appearances and absences; Gamma(count) ignored the absence evidence.
        samples = [0.0]
        for x in range(1, n + 1):
            alpha, beta = m14_posterior_params(state, x)
            samples.append(rng.betavariate(alpha, beta))
        return _top6(samples, n, rng)
    if code == "M16":
        w = vote_weights(state)
        votes = [0.0] * (n + 1)
        for c in VOTERS:
            for x in ticket(state, c, f"{seed_key}|vote"):
                votes[x] += w[c]
        return _top6(votes, n, rng)
    if code == "M12":
        raise ValueError("M12 cần vé của các phương pháp khác: dùng all_tickets()")
    return _top6(state.scores(code), n, rng)


def _max_run(t: Sequence[int]) -> int:
    best = cur = 1
    for a, b in zip(t, t[1:]):
        cur = cur + 1 if b == a + 1 else 1
        best = max(best, cur)
    return best


def _balanced(state: State, rng: random.Random) -> tuple[int, ...]:
    n = state.n
    if len(state.sums) >= 20:
        ss = sorted(state.sums)
        lo, hi = ss[len(ss) // 4], ss[(3 * len(ss)) // 4]
    else:
        mid = 3 * (n + 1)
        lo, hi = mid - n // 2, mid + n // 2
    t: tuple[int, ...] = ()
    for _ in range(5000):
        t = tuple(sorted(rng.sample(range(1, n + 1), 6)))
        odd = sum(x % 2 for x in t)
        low = sum(1 for x in t if x <= n // 2)
        if lo <= sum(t) <= hi and 2 <= odd <= 4 and 2 <= low <= 4 and _max_run(t) <= 2:
            return t
    return t


def _unpopular(state: State, rng: random.Random) -> tuple[int, ...]:
    """4 số > 31 và 2 số ≤ 31; tránh liên tiếp, tránh cùng hàng chục/cùng bội số, tránh trùng kỳ vừa quay."""
    n = state.n
    last = set(state.history[-1]) if state.history else set()
    t: tuple[int, ...] = ()
    for _ in range(5000):
        t = tuple(sorted(rng.sample(range(32, n + 1), 4) + rng.sample(range(1, 32), 2)))
        if _max_run(t) > 1 or len(set(t) & last) > 1:
            continue
        if len({x % 10 for x in t}) < 5 or len({x // 10 for x in t}) < 3:
            continue
        if any(all(x % k == 0 for x in t) for k in (2, 3, 5, 7)):
            continue
        return t
    return t


def _coverage(others: dict[str, tuple[int, ...]], n: int, rng: random.Random) -> tuple[int, ...]:
    use = [0] * (n + 1)
    for t in others.values():
        for x in t:
            use[x] += 1
    return _top6([-float(u) for u in use], n, rng)


def all_tickets(state: State, seed_key: str) -> dict[str, tuple[int, ...]]:
    out = {code: ticket(state, code, seed_key) for code in CODES if code != "M12"}
    out["M12"] = _coverage(out, state.n, _rng(f"{seed_key}|M12"))
    return {code: out[code] for code in CODES}


# Bump every method fingerprint after aligning online learning with the actual seeded tickets.
PARAMS = {"window": 50, "half_life": 30.0, "learn_half_life": 50.0, "learn_rate": 0.5, "algo": 3}
# Tham số riêng của từng phương pháp (M0–M8 giữ nguyên PARAMS nên không sinh phiên bản mới).
METHOD_PARAMS = {c: dict(PARAMS) for c in CODES}
METHOD_PARAMS["M9"].update({"ml_rate": ML_RATE, "ml_l2": ML_L2,
                            "ml_features": ["recent50", "weighted", "gap", "total", "pairs_last"], "standardize": "z"})
METHOD_PARAMS["M10"].update({"sum_band": "p25-p75", "odd": [2, 4], "low": [2, 4], "max_run": 2})
METHOD_PARAMS["M11"].update({"over31": 4, "max_run": 1})
METHOD_PARAMS["M12"].update({"coverage_of": "all_other"})
METHOD_PARAMS["M13"].update({"markov_states": 2, "laplace": 1})
METHOD_PARAMS["M14"].update({"sampler": "thompson_beta_bernoulli", "prior_mean": "6/max_number",
                            "prior_strength_draws": 20, "posterior": "Beta(prior_a+appearances, prior_b+absences)",
                            "implementation": "beta_bernoulli_thompson_v1"})
METHOD_PARAMS["M15"].update({"hazard_smoothing": 0.5})
METHOD_PARAMS["M16"].update({"voters": list(VOTERS), "weights": "hedge"})
