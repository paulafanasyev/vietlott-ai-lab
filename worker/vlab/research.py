"""Giai đoạn 4: vòng giả thuyết và kho tri thức.

Bộ sinh giả thuyết (thuật toán, không phải mô hình ngôn ngữ) đề xuất các quy tắc chọn số có tham số cố định và
một khẳng định kiểm chứng được: «vé của quy tắc trúng trung bình nhiều số hơn vé ngẫu nhiên». Mỗi giả thuyết đi
qua ba phần dữ liệu theo thời gian (không trộn):
  dev 60% (đầu, sau 100 kỳ khởi động) → chỉ để khám phá và sinh biến thể;
  validation 20% → quyết định, hiệu chỉnh Benjamini–Hochberg trên MỌI giả thuyết đã kiểm của trò chơi;
  test 20% (cuối) → niêm phong: mỗi phiên bản phương pháp chỉ chạy MỘT lần (CSDL có chỉ mục duy nhất).
Mọi thí nghiệm, kể cả thất bại, được ghi vào experiments + knowledge_docs (chỉ ghi thêm).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timezone

from .games import GAMES

WARMUP = 100
ALPHA = 0.05
MAX_PER_RUN = 14  # giả thuyết mới từ danh mục mỗi trò chơi mỗi lần chạy
EVOLVE_PER_RUN = 2  # sau khi hết danh mục: số biến thể mới mỗi lần chạy
MAX_HYPOTHESES = 100  # mỗi trò chơi; càng nhiều giả thuyết, hiệu chỉnh BH càng khắt khe

KIND_VI = {
    "hot": "Số nóng trong {W} kỳ gần nhất",
    "hot_all": "Số nóng toàn bộ lịch sử",
    "cold": "Số lạnh trong {W} kỳ gần nhất",
    "overdue": "Số lâu chưa về",
    "decay": "Tần suất giảm dần, chu kỳ bán rã {H} kỳ",
    "hot_parity": "Số nóng {W} kỳ, cân bằng 3 chẵn / 3 lẻ",
    "hot_lowhigh": "Số nóng {W} kỳ, cân bằng 3 nhỏ / 3 lớn",
    "avoid_last": "Số nóng {W} kỳ, bỏ các số của kỳ vừa quay",
    "pairs_last": "Số hay đi cùng các số của kỳ vừa quay",
}
PRINCIPLE_VI = {
    "hot": "Nếu máy quay có thiên lệch ngắn hạn, các số về nhiều gần đây sẽ tiếp tục về nhiều hơn.",
    "hot_all": "Nếu có thiên lệch lâu dài, các số về nhiều nhất từ trước tới nay sẽ tiếp tục về nhiều hơn.",
    "cold": "Ý tưởng «bù trừ»: số ít về gần đây sẽ sớm về (thường là ngộ nhận của người chơi).",
    "overdue": "Ý tưởng «tới lượt»: số lâu chưa về sẽ sớm về (thường là ngộ nhận của người chơi).",
    "decay": "Kỳ gần quan trọng hơn kỳ xa; trọng số giảm theo hàm mũ.",
    "hot_parity": "Kết hợp xu hướng nóng với cơ cấu chẵn/lẻ phổ biến nhất.",
    "hot_lowhigh": "Kết hợp xu hướng nóng với cơ cấu nhỏ/lớn phổ biến nhất.",
    "avoid_last": "Số vừa về ít khả năng về lại ngay kỳ sau.",
    "pairs_last": "Có các cặp số hay xuất hiện cùng nhau.",
}

CATALOG = [
    {"kind": "hot", "W": 10}, {"kind": "hot", "W": 30}, {"kind": "hot", "W": 100}, {"kind": "hot_all"},
    {"kind": "cold", "W": 10}, {"kind": "cold", "W": 30}, {"kind": "cold", "W": 100},
    {"kind": "overdue"}, {"kind": "decay", "H": 10}, {"kind": "decay", "H": 50},
    {"kind": "hot_parity", "W": 30}, {"kind": "hot_lowhigh", "W": 30}, {"kind": "avoid_last", "W": 30},
    {"kind": "pairs_last"},
]


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def spec_key(spec: dict) -> str:
    return _j({k: spec[k] for k in sorted(spec)})


def title_vi(spec: dict) -> str:
    return KIND_VI[spec["kind"]].format(W=spec.get("W"), H=spec.get("H"))


def neighbours(spec: dict) -> list[dict]:
    out = []
    for key in ("W", "H"):
        if key in spec:
            for f in (0.6, 1.6):
                v = max(5, min(400, round(spec[key] * f)))
                if v != spec[key]:
                    out.append({**spec, key: v})
    return out


# ---------------------------------------------------------------- đánh giá walk-forward theo lô
class _Feats:
    def __init__(self, n: int, windows: set[int], halves: set[float]):
        self.n = n
        self.t = 0
        self.total = [0] * (n + 1)
        self.win = {w: [0] * (n + 1) for w in windows}
        self.dec = {h: [0.0] * (n + 1) for h in halves}
        self.dfac = {h: 0.5 ** (1 / h) for h in halves}
        self.last_seen = [-1] * (n + 1)
        self.pairs = [[0] * (n + 1) for _ in range(n + 1)]
        self.last: tuple = ()
        self.recent: list = []

    def add(self, draw):
        n = self.n
        for x in draw:
            self.total[x] += 1
            self.last_seen[x] = self.t
        for w, arr in self.win.items():
            for x in draw:
                arr[x] += 1
            if len(self.recent) >= w:
                for x in self.recent[-w]:
                    arr[x] -= 1
        self.recent.append(draw)
        if len(self.recent) > 450:
            self.recent = self.recent[-450:]
        for h, arr in self.dec.items():
            f = self.dfac[h]
            for x in range(1, n + 1):
                arr[x] *= f
            for x in draw:
                arr[x] += 1.0
        for a in draw:
            for b in draw:
                if a != b:
                    self.pairs[a][b] += 1
        self.last = tuple(draw)
        self.t += 1


def _top(scores, cand, k, jit):
    return sorted(cand, key=lambda x: (-scores[x], jit[x]))[:k]


def ticket_for(spec: dict, f: _Feats, rng: random.Random) -> tuple[int, ...]:
    n = f.n
    jit = [rng.random() for _ in range(n + 1)]
    allx = range(1, n + 1)
    kind = spec["kind"]
    if kind == "hot":
        s = f.win[spec["W"]]
    elif kind == "hot_all":
        s = f.total
    elif kind == "cold":
        s = [-v for v in f.win[spec["W"]]]
    elif kind == "overdue":
        s = [f.t - v for v in f.last_seen]
    elif kind == "decay":
        s = f.dec[spec["H"]]
    elif kind == "pairs_last":
        s = [sum(f.pairs[x][y] for y in f.last) if x not in f.last else -1 for x in range(n + 1)]
    elif kind == "avoid_last":
        w = f.win[spec["W"]]
        s = [w[x] if x not in f.last else -10 ** 6 for x in range(n + 1)]
    elif kind == "hot_parity":
        w = f.win[spec["W"]]
        pick = _top(w, [x for x in allx if x % 2], 3, jit) + _top(w, [x for x in allx if not x % 2], 3, jit)
        return tuple(sorted(pick))
    elif kind == "hot_lowhigh":
        w = f.win[spec["W"]]
        half = n // 2
        pick = _top(w, [x for x in allx if x <= half], 3, jit) + _top(w, [x for x in allx if x > half], 3, jit)
        return tuple(sorted(pick))
    else:
        raise KeyError(kind)
    return tuple(sorted(_top(s, list(allx), 6, jit)))


def splits(T: int, warmup: int = WARMUP) -> dict[str, tuple[int, int]]:
    """Chỉ số [đầu, cuối) của từng phần theo thời gian."""
    a, b = int(T * 0.6), int(T * 0.8)
    return {"dev": (min(warmup, a), a), "validation": (a, b), "test": (b, T)}


def evaluate(specs: list[dict], max_number: int, draws: list[tuple[int, tuple]], seed: str) -> list[dict[str, list]]:
    """Trả về, cho mỗi spec, {split: [số trúng ở từng kỳ]} — vé kỳ t chỉ dùng các kỳ < t."""
    windows = {s["W"] for s in specs if "W" in s}
    halves = {s["H"] for s in specs if "H" in s}
    f = _Feats(max_number, windows, halves)
    rg = splits(len(draws))
    out = [{k: [] for k in rg} for _ in specs]
    for t, (no, nums) in enumerate(draws):
        part = next((k for k, (a, b) in rg.items() if a <= t < b), None)
        if part:
            actual = set(nums)
            for i, s in enumerate(specs):
                tk = ticket_for(s, f, random.Random(f"{seed}|{spec_key(s)}|{no}"))
                out[i][part].append(len(actual.intersection(tk)))
        f.add(nums)
    return out


def stats(hits: list[int], max_number: int) -> dict:
    from .backtest import match_probs
    n = len(hits)
    mu = 36 / max_number
    var = 36 * (max_number - 6) ** 2 / (max_number ** 2 * (max_number - 1))
    mean = sum(hits) / n if n else 0.0
    z = (mean - mu) / math.sqrt(var / n) if n else 0.0
    p_one = 0.5 * math.erfc(z / math.sqrt(2))  # một phía: «tốt hơn ngẫu nhiên»
    p = match_probs(max_number)
    hist = [hits.count(k) for k in range(7)]
    exp = {g: n * sum(p[k] for k in range(m, 7)) for g, m in (("3plus", 3), ("4plus", 4), ("5plus", 5), ("jackpot", 6))}
    return {"n": n, "mean": mean, "mu": mu, "z": z, "p_one": p_one, "hist": hist,
            "g3": sum(hist[3:]), "g4": sum(hist[4:]), "g5": sum(hist[5:]), "g6": hist[6], "expected": exp}


def bh_adjust(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    best = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        best = min(best, pvals[i] * m / rank)
        adj[i] = best
    return adj


# ---------------------------------------------------------------- tri thức nền (nguồn có thẩm quyền)
SEED_DOCS = [
    ("Luật chơi Mega 6/45 (Vietlott)", "https://vietlott.vn/vi/choi/mega-6-45/gioi-thieu-san-pham-6-45", "official", 5,
     "Chọn 6 số từ 1–45. Không có số đặc biệt. Giải Jackpot: trùng 6 số; giải Nhất: 5; Nhì: 4; Ba: 3. "
     "Quay thứ Tư, thứ Sáu, Chủ Nhật lúc 18:00."),
    ("Luật chơi Power 6/55 (Vietlott)", "https://vietlott.vn/vi/choi/power-6-55/gioi-thieu-san-pham-power-655", "official", 5,
     "Chọn 6 số từ 1–55; mỗi kỳ quay thêm 1 số đặc biệt. Jackpot 1: trùng 6 số; Jackpot 2: 5 số + số đặc biệt. "
     "Quay thứ Ba, thứ Năm, thứ Bảy lúc 18:00."),
    ("Benjamini & Hochberg (1995): kiểm soát tỉ lệ phát hiện sai", "https://doi.org/10.1111/j.2517-6161.1995.tb02031.x",
     "paper", 5, "Khi kiểm định nhiều giả thuyết cùng lúc, phải hiệu chỉnh p-value; nếu không, một số giả thuyết sẽ "
     "«đạt» chỉ do ngẫu nhiên. Hệ thống áp dụng hiệu chỉnh này cho mọi giả thuyết đã kiểm của mỗi trò chơi."),
    ("Tversky & Kahneman (1971): niềm tin vào «luật số nhỏ»", "https://doi.org/10.1037/h0031322", "paper", 5,
     "Con người thường tin rằng chuỗi ngắn phải «cân bằng» như xác suất dài hạn. Đây là gốc của ngộ nhận «số lạnh "
     "sắp về» hay «số lâu chưa về đã tới lượt». Các kỳ quay xổ số độc lập với nhau."),
    ("Xác suất trúng của một vé 6 số (phân phối siêu bội)", "https://en.wikipedia.org/wiki/Lottery_mathematics", "article", 3,
     "P(trùng đúng m số) = C(6,m)·C(N−6,6−m)/C(N,6). Mega 6/45: 3 số 1/45, 4 số 1/733, 5 số 1/34 808, 6 số 1/8 145 060. "
     "Power 6/55: 3 số 1/79, 4 số 1/1 643, 5 số 1/98 604, 6 số 1/28 989 675."),
]


def seed_knowledge(cur) -> int:
    cur.execute("SELECT count(*) FROM knowledge_docs WHERE source_type <> 'internal'")
    if cur.fetchone()[0]:
        return 0
    for title, url, kind, score, summary in SEED_DOCS:
        cur.execute("""INSERT INTO knowledge_docs (title, url, source_type, authority_score, reproducible, summary_vi)
                       VALUES (%s,%s,%s,%s,%s,%s)""", (title, url, kind, score, kind != "article" or None, summary))
    return len(SEED_DOCS)


# ---------------------------------------------------------------- vòng nghiên cứu
def _tested(cur, game: str) -> dict[str, dict]:
    cur.execute("""SELECT h.id, h.params, m.p_values FROM hypotheses h
                   LEFT JOIN experiments e ON e.hypothesis_id = h.id
                   LEFT JOIN metrics m ON m.backtest_id = ANY(e.backtest_ids) AND m.split = 'dev'
                   WHERE h.params->>'game' = %s""", (game,))
    out = {}
    for hid, params, pv in cur.fetchall():
        spec = {k: v for k, v in params.items() if k != "game"}
        out[spec_key(spec)] = {"id": hid, "spec": spec, "dev_z": (pv or {}).get("z")}
    return out


def propose(tested: dict[str, dict], limit: int) -> list[dict]:
    new = [s for s in CATALOG if spec_key(s) not in tested][:limit]
    if new or len(tested) >= MAX_HYPOTHESES:
        return new
    # Tiến hóa: biến thể quanh giả thuyết có z tốt nhất trên phần dev (không nhìn validation/test).
    ranked = sorted((v for v in tested.values() if v["dev_z"] is not None), key=lambda v: -v["dev_z"])
    for v in ranked:
        for nb in neighbours(v["spec"]):
            if spec_key(nb) not in tested and all(spec_key(nb) != spec_key(x) for x in new):
                new.append(nb)
        if len(new) >= EVOLVE_PER_RUN:
            break
    return new[:EVOLVE_PER_RUN]


def _backtest_row(cur, mv: int, game: str, split: str, draws, rng, started, st: dict, max_number: int) -> int:
    a, b = rng
    cur.execute("""INSERT INTO backtests (method_version_id, game, split, from_draw_no, to_draw_no, tickets_per_draw,
                       started_at, finished_at, status, log_text)
                   VALUES (%s,%s,%s,%s,%s,1,%s,%s,'finished',%s) RETURNING id""",
                (mv, game, split, draws[a][0], draws[b - 1][0], started, datetime.now(timezone.utc),
                 f"{st['n']} kỳ, TB {st['mean']:.4f} số/vé (ngẫu nhiên {st['mu']:.4f}), z={st['z']:.3f}"))
    bid = cur.fetchone()[0]
    cur.execute("""INSERT INTO metrics (backtest_id, method_version_id, game, split, window_label, n_draws, n_tickets,
                       hits_hist, goal_any_3plus, goal_any_4plus, goal_any_5plus, goal_any_6, expected_random, p_values)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (bid, mv, game, split, f"{draws[a][0]:05d}-{draws[b - 1][0]:05d}", st["n"], st["n"], st["hist"],
                 st["g3"], st["g4"], st["g5"], st["g6"],
                 _j({k: round(v, 4) for k, v in st["expected"].items()} | {"mean_hits": round(st["mu"], 4)}),
                 _j({"one_sided": st["p_one"], "z": round(st["z"], 4), "mean_hits": round(st["mean"], 4)})))
    return bid


def research_game(cur, game: str, git_sha: str, limit: int = MAX_PER_RUN) -> list[str]:
    g = GAMES[game]
    cur.execute("""SELECT draw_no, main_numbers FROM draw_current WHERE game = %s AND status IN ('VERIFIED','CROSS-CHECKED')
                   AND main_numbers IS NOT NULL ORDER BY draw_no""", (game,))
    draws = [(int(n), tuple(int(x) for x in m)) for n, m in cur.fetchall()]
    if len(draws) < WARMUP * 2:
        return [f"{g.name_vi}: chưa đủ dữ liệu cho nghiên cứu"]
    tested = _tested(cur, game)
    specs = propose(tested, limit)
    if not specs:
        return [f"{g.name_vi}: không còn giả thuyết mới"]
    started = datetime.now(timezone.utc)
    seed = f"research|{game}"
    results = evaluate(specs, g.max_number, draws, seed)
    rg = splits(len(draws))
    cur.execute("""SELECT (m.p_values->>'one_sided')::float FROM metrics m
                   JOIN method_versions v ON v.id = m.method_version_id JOIN methods x ON x.id = v.method_id
                   WHERE m.split = 'validation' AND m.game = %s AND x.family = 'ai_hypothesis'""", (game,))
    prev_val = [r[0] for r in cur.fetchall()]
    lines = []
    for spec, res in zip(specs, results):
        params = {**spec, "game": game}
        title = f"[{g.name_vi}] {title_vi(spec)}"
        cur.execute("""INSERT INTO hypotheses (title_vi, principle_vi, params, testable_claim_vi, goal, created_by,
                           row_hash, prev_hash) VALUES (%s,%s,%s,%s,'3plus','ai','','') RETURNING id""",
                    (title, PRINCIPLE_VI[spec["kind"]] + " (Đề xuất tự động bởi bộ sinh giả thuyết.)", _j(params),
                     "Vé 6 số của quy tắc này trúng trung bình nhiều số chính hơn vé ngẫu nhiên "
                     f"(kiểm định một phía, α = {ALPHA}, hiệu chỉnh Benjamini–Hochberg)."))
        hid = cur.fetchone()[0]
        cur.execute("INSERT INTO hypothesis_status_events (hypothesis_id, status, note_vi) VALUES (%s,'proposed',%s)",
                    (hid, "Bộ sinh giả thuyết đề xuất"))
        code = "H-" + hashlib.sha256(_j(params).encode()).hexdigest()[:10]
        cur.execute("""INSERT INTO methods (code, name_vi, family, description_vi) VALUES (%s,%s,'ai_hypothesis',%s)
                       ON CONFLICT (code) DO NOTHING""", (code, title, PRINCIPLE_VI[spec["kind"]]))
        cur.execute("SELECT id FROM methods WHERE code = %s", (code,))
        mid = cur.fetchone()[0]
        cur.execute("""INSERT INTO method_versions (method_id, version, params, code_git_sha, change_kind, change_note_vi,
                           row_hash, prev_hash) VALUES (%s,1,%s,%s,'initial','Phiên bản đầu tiên của giả thuyết','','')
                       RETURNING id""", (mid, _j(params), git_sha))
        mv = cur.fetchone()[0]
        cur.execute("INSERT INTO hypothesis_status_events (hypothesis_id, status, note_vi) VALUES (%s,'testing',%s)",
                    (hid, "Bắt đầu kiểm định walk-forward"))
        st = {k: stats(res[k], g.max_number) for k in res}
        bids = [_backtest_row(cur, mv, game, "dev", draws, rg["dev"], started, st["dev"], g.max_number),
                _backtest_row(cur, mv, game, "validation", draws, rg["validation"], started, st["validation"], g.max_number)]
        prev_val.append(st["validation"]["p_one"])
        adj = bh_adjust(prev_val)[-1]
        checks = {"n_hypotheses_tested": len(prev_val), "bh_adjusted_p_validation": round(adj, 6),
                  "dev_z": round(st["dev"]["z"], 3), "validation_z": round(st["validation"]["z"], 3),
                  "sealed_test_used": False, "splits": {k: [draws[a][0], draws[b - 1][0]] for k, (a, b) in rg.items()}}
        succeeded = False
        if adj < ALPHA and st["validation"]["z"] > 0:
            cur.execute("""INSERT INTO hypothesis_status_events (hypothesis_id, status, note_vi)
                           VALUES (%s,'supported_on_validation',%s)""", (hid, f"Đạt trên validation (p hiệu chỉnh {adj:.4f})"))
            bids.append(_backtest_row(cur, mv, game, "test", draws, rg["test"], started, st["test"], g.max_number))
            checks["sealed_test_used"] = True
            checks["test_z"] = round(st["test"]["z"], 3)
            succeeded = st["test"]["p_one"] < ALPHA
            status = "confirmed_on_test" if succeeded else "rejected"
            note = (f"Bộ kiểm thử niêm phong: p = {st['test']['p_one']:.4f} → "
                    + ("XÁC NHẬN" if succeeded else "không lặp lại được, bác bỏ"))
        else:
            status, note = "rejected", f"Không đạt trên validation (p hiệu chỉnh {adj:.4f}); bộ test không bị dùng"
        cur.execute("INSERT INTO hypothesis_status_events (hypothesis_id, status, note_vi) VALUES (%s,%s,%s)",
                    (hid, status, note))
        dv, vl = st["dev"], st["validation"]
        summary = (f"dev: {dv['n']} kỳ, TB {dv['mean']:.3f} số/vé (ngẫu nhiên {dv['mu']:.3f}), z = {dv['z']:.2f}; "
                   f"validation: {vl['n']} kỳ, TB {vl['mean']:.3f}, z = {vl['z']:.2f}, 3+: {vl['g3']} lần "
                   f"(ngẫu nhiên kỳ vọng {vl['expected']['3plus']:.1f}).")
        if checks["sealed_test_used"]:
            ts = st["test"]
            summary += f" test: {ts['n']} kỳ, TB {ts['mean']:.3f}, z = {ts['z']:.2f}."
        conclusion = ("Giả thuyết được XÁC NHẬN trên bộ test niêm phong." if succeeded else
                      "Giả thuyết bị bác bỏ: không khác ngẫu nhiên một cách có ý nghĩa.")
        cur.execute("""INSERT INTO experiments (hypothesis_id, method_version_id, backtest_ids, result_summary_vi,
                           overfitting_checks, conclusion_vi, succeeded, row_hash, prev_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'','')""", (hid, mv, bids, summary, _j(checks), conclusion, succeeded))
        cur.execute("""INSERT INTO knowledge_docs (title, source_type, authority_score, reproducible, summary_vi, content)
                       VALUES (%s,'internal',3,true,%s,%s)""",
                    (f"Thí nghiệm: {title}", f"{conclusion} {summary}",
                     _j({"hypothesis_id": hid, "method_version_id": mv, "backtest_ids": bids, "checks": checks})))
        cur.execute("""INSERT INTO ai_journal (actor, action, change_kind, details, row_hash, prev_hash)
                       VALUES ('job','experiment','knowledge',%s,'','')""",
                    (_j({"hypothesis_id": hid, "game": game, "status": status, "checks": checks}),))
        lines.append(f"{title}: {'XÁC NHẬN' if succeeded else 'bác bỏ'} (z dev {dv['z']:.2f}, val {vl['z']:.2f}, "
                     f"p BH {adj:.3f})")
    return lines


def run_research(conn, git_sha: str = "unknown", limit: int = MAX_PER_RUN) -> str:
    lines = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('vlab:research'))")
            if not cur.fetchone()[0]:
                conn.rollback()
                return "Nghiên cứu: bỏ qua (một lần chạy khác đang ghi)."
            n = seed_knowledge(cur)
            if n:
                lines.append(f"Kho tri thức: thêm {n} tài liệu nền.")
            for game in GAMES:
                lines.extend(research_game(cur, game, git_sha, limit))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return "\n".join(lines)


# ---------------------------------------------------------------- đọc
def research_report(conn, game: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT h.id, h.title_vi, h.principle_vi, h.params, h.created_at,
                   (SELECT status FROM hypothesis_status_events s WHERE s.hypothesis_id = h.id ORDER BY s.id DESC LIMIT 1),
                   (SELECT note_vi FROM hypothesis_status_events s WHERE s.hypothesis_id = h.id ORDER BY s.id DESC LIMIT 1),
                   e.result_summary_vi, e.conclusion_vi, e.succeeded, e.overfitting_checks
            FROM hypotheses h LEFT JOIN experiments e ON e.hypothesis_id = h.id
            WHERE h.params->>'game' = %s ORDER BY h.id DESC""", (game,))
        cols = ("id", "title_vi", "principle_vi", "params", "created_at", "status", "status_note_vi",
                "result_summary_vi", "conclusion_vi", "succeeded", "checks")
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    confirmed = [r for r in rows if r["status"] == "confirmed_on_test"]
    return {"ready": True, "game": game, "n_tested": len(rows), "n_confirmed": len(confirmed),
            "n_failed": sum(1 for r in rows if r["succeeded"] is False), "hypotheses": rows,
            "note_vi": ("Mọi giả thuyết đều được lưu, kể cả thất bại. «Xác nhận» chỉ khi đạt trên validation sau hiệu chỉnh "
                        "Benjamini–Hochberg VÀ lặp lại trên bộ test niêm phong (mỗi phiên bản chỉ chạy một lần).")}


def search_knowledge(conn, q: str, limit: int = 30) -> dict:
    q = (q or "").strip()[:200]
    with conn.cursor() as cur:
        if q:
            cur.execute("""SELECT id, title, url, source_type, authority_score, summary_vi, created_at FROM knowledge_docs
                           WHERE search_tsv @@ plainto_tsquery('simple', %s) OR title ILIKE %s OR summary_vi ILIKE %s
                           ORDER BY authority_score DESC, id DESC LIMIT %s""", (q, f"%{q}%", f"%{q}%", limit))
        else:
            cur.execute("""SELECT id, title, url, source_type, authority_score, summary_vi, created_at FROM knowledge_docs
                           ORDER BY (source_type = 'internal'), authority_score DESC, id DESC LIMIT %s""", (limit,))
        cols = ("id", "title", "url", "source_type", "authority_score", "summary_vi", "created_at")
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM knowledge_docs")
        total = cur.fetchone()[0]
    return {"ready": True, "q": q, "total": total, "docs": rows}
