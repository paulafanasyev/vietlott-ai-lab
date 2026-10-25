"""Giai đoạn 3: dự đoán live và tự học.

Mỗi lần chạy (sau khi thu thập):
1. Đăng ký phương pháp và phiên bản (method_versions chỉ ghi thêm; đổi tham số → phiên bản mới).
2. Chấm điểm: vé live nào có kỳ mục tiêu đã có kết quả dùng được → ghi ticket_results.
3. Dự đoán: nếu kỳ kế tiếp chưa quay và chưa có dự đoán → ghi prediction_runs + tickets TRƯỚC giờ quay.
   CSDL tự từ chối dự đoán sau giờ quay, sai lịch, hoặc khi kỳ đã có kết quả (trigger check_run/check_ticket).
Phương pháp M8 tự cập nhật trọng số sau mỗi kỳ (xem methods.State.add); trọng số được ghi kèm vé.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .games import GAMES
from .methods import BASE, CODES, FAMILY, METHOD_PARAMS, METHODS, State, all_tickets, learn_weights
from .schedule import VN_TZ, next_draw_after

USABLE = ("VERIFIED", "CROSS-CHECKED")
LIVE_LOCK = "vlab:live"


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def seed_of(game: str, target: int, code: str) -> int:
    return int(hashlib.sha256(f"{game}|{target}|{code}".encode()).hexdigest()[:15], 16)


def prize_tier(game: str, hits: int, bonus_hit: bool | None) -> str | None:
    if game == "power655":
        if hits == 6:
            return "jackpot1"
        if hits == 5 and bonus_hit:
            return "jackpot2"
    elif hits == 6:
        return "jackpot"
    return {5: "giai1", 4: "giai2", 3: "giai3"}.get(hits)


def ensure_methods(cur, git_sha: str) -> dict[str, int]:
    """Trả về {mã: id phiên bản hiện hành}."""
    out = {}
    for m in METHODS:
        params = _j(METHOD_PARAMS[m.code])
        cur.execute("""INSERT INTO methods (code, name_vi, family, description_vi) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (code) DO NOTHING""", (m.code, m.name_vi, FAMILY[m.code], m.description_vi))
        cur.execute("SELECT id FROM methods WHERE code = %s", (m.code,))
        mid = cur.fetchone()[0]
        cur.execute("""SELECT id, version, params FROM method_versions WHERE method_id = %s
                       ORDER BY version DESC LIMIT 1""", (mid,))
        row = cur.fetchone()
        if row and _j(row[2]) == params:
            out[m.code] = row[0]
            continue
        cur.execute("""INSERT INTO method_versions (method_id, version, params, code_git_sha, change_kind,
                           change_note_vi, row_hash, prev_hash) VALUES (%s,%s,%s,%s,%s,%s,'','') RETURNING id""",
                    (mid, (row[1] + 1) if row else 1, params, git_sha, "params" if row else "initial",
                     "Đổi tham số" if row else "Phiên bản đầu tiên"))
        out[m.code] = cur.fetchone()[0]
    return out


def score(cur) -> int:
    cur.execute("""
        SELECT t.id, t.main_numbers, c.version_id, c.main_numbers, c.bonus_number, r.game
        FROM tickets t JOIN prediction_runs r ON r.id = t.run_id
        JOIN draw_current c ON c.game = r.game AND c.draw_no = r.target_draw_no
        WHERE r.purpose = 'live' AND c.status IN ('VERIFIED', 'CROSS-CHECKED')
          AND NOT EXISTS (SELECT 1 FROM ticket_results x WHERE x.ticket_id = t.id AND x.draw_version_id = c.version_id)
        ORDER BY t.id""")
    n = 0
    for tid, nums, vid, main, bonus, game in cur.fetchall():
        hits = len(set(nums) & set(main))
        bonus_hit = (bonus in nums) if bonus is not None else None
        cur.execute("""INSERT INTO ticket_results (ticket_id, draw_version_id, main_hits, bonus_hit, prize_tier)
                       VALUES (%s,%s,%s,%s,%s)""", (tid, vid, hits, bonus_hit, prize_tier(game, hits, bonus_hit)))
        n += 1
    return n


def target_for(cur, game: str, now: datetime) -> tuple[int | None, datetime | None, str]:
    """Kỳ mục tiêu = kỳ cuối đã biết + 1, chỉ khi chưa có giờ quay nào trôi qua kể từ kỳ cuối
    (nếu đã quay mà chưa có kết quả thì chờ lần chạy sau)."""
    cur.execute("""SELECT draw_no, draw_at FROM draw_current WHERE game = %s AND status <> 'PENDING'
                   ORDER BY draw_no DESC LIMIT 1""", (game,))
    row = cur.fetchone()
    if not row or row[1] is None:
        return None, None, "chưa có dữ liệu"
    last_no, last_at = row
    g = GAMES[game]
    nxt = next_draw_after(g, last_at)
    if nxt <= now:
        return None, None, f"đã quay kỳ {last_no + 1:05d} lúc {nxt.astimezone(VN_TZ):%d/%m %H:%M} nhưng chưa có kết quả"
    return last_no + 1, nxt, ""


def predict(cur, game: str, versions: dict[str, int], now: datetime) -> str:
    target, at, why = target_for(cur, game, now)
    name = GAMES[game].name_vi
    if target is None:
        return f"{name}: không dự đoán ({why})"
    cur.execute("""SELECT count(*) FROM prediction_runs WHERE purpose = 'live' AND game = %s AND target_draw_no = %s
                   AND method_version_id = ANY(%s)""", (game, target, list(versions.values())))
    if cur.fetchone()[0] >= len(versions):
        return f"{name}: kỳ {target:05d} đã có dự đoán"
    cur.execute("""SELECT draw_no, main_numbers FROM draw_current WHERE game = %s AND status IN ('VERIFIED','CROSS-CHECKED')
                   AND main_numbers IS NOT NULL AND draw_no < %s ORDER BY draw_no""", (game, target))
    rows = [(int(n), tuple(int(x) for x in m)) for n, m in cur.fetchall()]
    snapshot = hashlib.sha256(_j(rows).encode()).hexdigest()
    state = State(GAMES[game].max_number)
    for draw_no, d in rows:
        state.add(d, seed_key=f"{game}|{draw_no}")
    tickets = all_tickets(state, f"{game}|{target}")
    weights = learn_weights(state)
    desc = {m.code: m.description_vi for m in METHODS}
    made = 0
    for code in CODES:
        cur.execute("""SELECT 1 FROM prediction_runs WHERE purpose = 'live' AND method_version_id = %s
                       AND game = %s AND target_draw_no = %s""", (versions[code], game, target))
        if cur.fetchone():
            continue
        cur.execute("""INSERT INTO prediction_runs (method_version_id, game, target_draw_no, purpose, split,
                           data_cutoff_draw_no, data_snapshot_sha256, random_seed, target_draw_at, row_hash, prev_hash)
                       VALUES (%s,%s,%s,'live','live',%s,%s,%s,%s,'','') RETURNING id""",
                    (versions[code], game, target, rows[-1][0] if rows else 0, snapshot,
                     seed_of(game, target, code), at))
        run_id = cur.fetchone()[0]
        expl = desc[code]
        if code == "M8":
            expl += " Trọng số hiện tại: " + ", ".join(f"{c} {weights[c] * 100:.1f}%" for c in BASE) + "."
        cur.execute("""INSERT INTO tickets (run_id, rank, main_numbers, explanation_vi, row_hash, prev_hash)
                       VALUES (%s,1,%s,%s,'','')""", (run_id, list(tickets[code]), expl))
        made += 1
    cur.execute("""INSERT INTO ai_journal (actor, action, change_kind, details, row_hash, prev_hash)
                   VALUES ('job','live_prediction','data',%s,'','')""",
                (_j({"game": game, "target_draw_no": target, "runs": made, "data_cutoff": rows[-1][0] if rows else 0,
                     "snapshot_sha256": snapshot, "learn_weights": {c: round(w, 4) for c, w in weights.items()}}),))
    return f"{name}: đã ghi {made} vé dự đoán cho kỳ {target:05d} (quay {at.astimezone(VN_TZ):%d/%m %H:%M})"


def run_live(conn, git_sha: str = "unknown", now: datetime | None = None) -> str:
    """Một giao dịch; khóa tư vấn cấp giao dịch chống chạy song song."""
    now = now or datetime.now(timezone.utc)
    lines = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (LIVE_LOCK,))
            if not cur.fetchone()[0]:
                conn.rollback()
                return "Dự đoán: bỏ qua (một lần chạy khác đang ghi)."
            versions = ensure_methods(cur, git_sha)
            n = score(cur)
            lines.append(f"Chấm điểm: {n} vé có kết quả mới.")
            for game in GAMES:
                lines.append(predict(cur, game, versions, now))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return "\n".join(lines)


def live_report(conn, game: str, limit: int = 30) -> dict:
    """Kết quả dự đoán live: vé gần đây và thống kê từng phương pháp so với ngẫu nhiên."""
    from .backtest import goal_odds, match_probs
    g = GAMES[game]
    p = match_probs(g.max_number)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.target_draw_no, r.target_draw_at, r.created_at, m.code, t.main_numbers, t.explanation_vi,
                   res.main_hits, res.bonus_hit, res.prize_tier
            FROM prediction_runs r JOIN method_versions v ON v.id = r.method_version_id
            JOIN methods m ON m.id = v.method_id JOIN tickets t ON t.run_id = r.id
            LEFT JOIN LATERAL (SELECT main_hits, bonus_hit, prize_tier FROM ticket_results x
                               WHERE x.ticket_id = t.id ORDER BY x.id DESC LIMIT 1) res ON true
            WHERE r.purpose = 'live' AND r.game = %s ORDER BY r.target_draw_no DESC, m.code""", (game,))
        rows = cur.fetchall()
        cur.execute("SELECT draw_no, main_numbers, bonus_number FROM draw_current WHERE game = %s AND draw_no = ANY(%s)",
                    (game, list({r[0] for r in rows})))
        actual = {n: (m, b) for n, m, b in cur.fetchall()}
    draws: dict[int, dict] = {}
    stats = {c: {"code": c, "scored": 0, "hits_sum": 0, "goals": {"3": 0, "4": 0, "5": 0, "6": 0}} for c in CODES}
    for target, at, created, code, nums, expl, hits, bonus_hit, tier in rows:
        d = draws.setdefault(target, {"draw_no": f"{target:05d}", "draw_at": at, "created_at": created,
                                      "result": list(actual[target][0]) if target in actual and actual[target][0] else None,
                                      "bonus": actual.get(target, (None, None))[1], "tickets": []})
        d["tickets"].append({"code": code, "numbers": list(nums), "hits": hits, "bonus_hit": bonus_hit,
                             "prize_tier": tier, "explanation_vi": expl})
        if hits is not None and code in stats:
            s = stats[code]
            s["scored"] += 1
            s["hits_sum"] += hits
            for k in ("3", "4", "5", "6"):
                if hits >= int(k):
                    s["goals"][k] += 1
    names = {m.code: m.name_vi for m in METHODS}
    out_stats = []
    for c in CODES:
        s = stats[c]
        n = s["scored"]
        out_stats.append({"code": c, "name_vi": names[c], "scored": n,
                          "mean_hits": round(s["hits_sum"] / n, 3) if n else None,
                          "random_mean_hits": round(36 / g.max_number, 3),
                          "goals": {k: {"actual": v, "expected": round(n * sum(p[j] for j in range(int(k), 7)), 3)}
                                    for k, v in s["goals"].items()}})
    return {"ready": True, "game": game, "goal_odds": goal_odds(g.max_number),
            "draws": list(draws.values())[:limit], "stats": out_stats,
            "note_vi": "Vé live được ghi vào CSDL chỉ-ghi-thêm TRƯỚC giờ quay; CSDL từ chối mọi vé ghi sau giờ quay."}
