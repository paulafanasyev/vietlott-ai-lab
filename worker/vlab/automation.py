"""Giai đoạn 5: tự động hóa — nhật ký tác vụ, báo cáo sau kỳ quay và hằng tuần, sao lưu + thử khôi phục.

Sao lưu: toàn bộ bảng lịch sử được xuất thành JSON (nén gzip) trong MỘT ảnh chụp nhất quán (REPEATABLE READ).
Thử khôi phục: chính tệp đó được giải nén, nạp lại vào bảng tạm và băm lại; phải khớp từng bảng với băm của
CSDL tại cùng ảnh chụp. Kết quả ghi vào backups / backup_restore_tests (chỉ ghi thêm).
Bản sao ngoài hệ thống: tải tệp ở /api/backup (tệp tự chứa băm để kiểm tra lại sau này).
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone

from .games import GAMES

BACKUP_EVERY = timedelta(hours=20)
WEEKLY_EVERY = timedelta(days=6, hours=12)


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def record_job(conn, name: str, trigger: str, started: datetime, status: str, summary_vi: str,
               details: dict | None = None) -> None:
    """Ghi job_runs bằng giao dịch riêng của kết nối (gọi khi kết nối không có giao dịch dở)."""
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO job_runs (job_name, trigger_kind, started_at, finished_at, status, summary_vi, details,
                           runner) VALUES (%s,%s,%s,%s,%s,%s,%s,'vercel')""",
                    (name, trigger, started, max(started, datetime.now(timezone.utc)), status, summary_vi[:20000],
                     _j(details or {})))
    conn.commit()


# ---------------------------------------------------------------- sao lưu
def history_tables(cur) -> list[str]:
    cur.execute("SELECT unnest(vlab_history_tables())::text")
    return [r[0] for r in cur.fetchall()]


def dump(cur) -> tuple[dict, dict]:
    """(dữ liệu, băm theo bảng) trong giao dịch hiện tại. Băm tính bằng vlab_table_digest (giờ UTC)."""
    data, digests = {}, {}
    for t in history_tables(cur):
        cur.execute(f"SELECT coalesce(max(id), 0) FROM {t}")
        max_id = cur.fetchone()[0]
        cur.execute("SELECT n, sha256 FROM vlab_table_digest(%s::regclass, %s)", (t, max_id))
        n, sha = cur.fetchone()
        cur.execute(f"SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY id), '[]'::jsonb)::text FROM {t} x WHERE id <= %s",
                    (max_id,))
        data[t] = json.loads(cur.fetchone()[0])
        digests[t] = {"max_id": max_id, "n": n, "sha256": sha}
    return data, digests


def pack(data: dict, digests: dict, created_at: datetime) -> bytes:
    doc = {"format": "vietlott-ai-lab-backup/1", "created_at": created_at.isoformat(), "digests": digests,
           "tables": data}
    return gzip.compress(json.dumps(doc, ensure_ascii=False, default=str).encode("utf-8"), mtime=0)


def restore_check(cur, blob: bytes) -> list[str]:
    """Giải nén tệp, nạp vào bảng tạm (ON COMMIT DROP), băm lại và so với băm ghi trong tệp. Rỗng = khớp."""
    doc = json.loads(gzip.decompress(blob).decode("utf-8"))
    problems = []
    cur.execute("SET LOCAL TimeZone = 'UTC'")
    for t, rows in doc["tables"].items():
        tmp = f"vbk_{t}"
        cur.execute(f"CREATE TEMP TABLE {tmp} (LIKE public.{t}) ON COMMIT DROP")
        cur.execute(f"INSERT INTO {tmp} SELECT * FROM jsonb_populate_recordset(NULL::{tmp}, %s::jsonb)",
                    (json.dumps(rows, ensure_ascii=False),))
        cur.execute(f"""SELECT count(*), encode(digest(coalesce(string_agg(to_jsonb(x)::text, E'\\n' ORDER BY id), ''),
                         'sha256'), 'hex') FROM {tmp} x""")
        n, sha = cur.fetchone()
        exp = doc["digests"].get(t)
        if not exp or exp["n"] != n or exp["sha256"] != sha:
            problems.append(f"{t}: khôi phục {n} dòng, băm {sha[:12]}… ≠ gốc {exp and exp['n']} dòng, "
                            f"{exp and exp['sha256'][:12]}…")
    missing = set(doc["digests"]) - set(doc["tables"])
    problems += [f"{t}: thiếu trong tệp" for t in sorted(missing)]
    return problems


def backup_and_verify(conn, force: bool = False) -> str:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT max(created_at) FROM backups")
        last = cur.fetchone()[0]
    conn.rollback()
    if not force and last and now - last < BACKUP_EVERY:
        return f"Sao lưu: bỏ qua (bản gần nhất lúc {last:%d/%m %H:%M} UTC)."
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            data, digests = dump(cur)
            blob = pack(data, digests, now)
            sha = hashlib.sha256(blob).hexdigest()
            problems = restore_check(cur, blob)
            rows = sum(d["n"] for d in digests.values())
            cur.execute("INSERT INTO backups (location, size_bytes, sha256) VALUES (%s,%s,%s) RETURNING id",
                        ("neon:restore-test (tải bản sao: /api/backup)", len(blob), sha))
            bid = cur.fetchone()[0]
            ok = not problems
            details = (f"Khôi phục {len(digests)} bảng, {rows} dòng vào bảng tạm; băm từng bảng "
                       + ("KHỚP." if ok else "KHÔNG KHỚP: " + "; ".join(problems[:10])))
            cur.execute("INSERT INTO backup_restore_tests (backup_id, ok, details_vi) VALUES (%s,%s,%s)",
                        (bid, ok, details))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return f"Sao lưu #{bid}: {len(blob) // 1024} KB, sha256 {sha[:12]}…; thử khôi phục: {'ĐẠT' if ok else 'KHÔNG ĐẠT'}."


def backup_file(conn) -> tuple[bytes, str]:
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            data, digests = dump(cur)
        conn.rollback()
    except Exception:
        conn.rollback()
        raise
    now = datetime.now(timezone.utc)
    return pack(data, digests, now), f"vietlott-backup-{now:%Y%m%d-%H%M}.json.gz"


# ---------------------------------------------------------------- báo cáo
def post_draw_reports(cur) -> list[str]:
    """Một báo cáo cho mỗi kỳ đã có vé live được chấm điểm mà chưa có báo cáo."""
    from .backtest import match_probs
    cur.execute("""
        SELECT DISTINCT r.game, r.target_draw_no FROM prediction_runs r JOIN tickets t ON t.run_id = r.id
        JOIN ticket_results x ON x.ticket_id = t.id
        WHERE r.purpose = 'live' AND NOT EXISTS (SELECT 1 FROM reports p WHERE p.kind = 'post_draw'
              AND p.game = r.game AND p.draw_no = r.target_draw_no)
        ORDER BY 1, 2""")
    made = []
    for game, no in cur.fetchall():
        g = GAMES[game]
        cur.execute("SELECT draw_date, main_numbers, bonus_number, status FROM draw_current WHERE game=%s AND draw_no=%s",
                    (game, no))
        ddate, main, bonus, status = cur.fetchone()
        cur.execute("""
            SELECT m.code, t.main_numbers, res.main_hits, res.bonus_hit, res.prize_tier
            FROM prediction_runs r JOIN method_versions v ON v.id = r.method_version_id JOIN methods m ON m.id = v.method_id
            JOIN tickets t ON t.run_id = r.id
            JOIN LATERAL (SELECT * FROM ticket_results x WHERE x.ticket_id = t.id ORDER BY x.id DESC LIMIT 1) res ON true
            WHERE r.purpose = 'live' AND r.game = %s AND r.target_draw_no = %s ORDER BY m.code""", (game, no))
        rows = cur.fetchall()
        mu = 36 / g.max_number
        lines = [f"# Báo cáo sau kỳ quay {g.name_vi} {no:05d} ({ddate:%d/%m/%Y})", "",
                 f"Kết quả ({status}): {' '.join(f'{x:02d}' for x in main)}"
                 + (f" · số đặc biệt {bonus:02d}" if bonus is not None else ""), "",
                 "| Phương pháp | Vé | Trúng | Giải |", "|---|---|---|---|"]
        for code, nums, hits, bh, tier in rows:
            lines.append(f"| {code} | {' '.join(f'{x:02d}' for x in nums)} | {hits}{' + ĐB' if bh else ''} | {tier or '—'} |")
        avg = sum(r[2] for r in rows) / len(rows) if rows else 0
        lines += ["", f"Trung bình {avg:.2f} số/vé; vé ngẫu nhiên kỳ vọng {mu:.2f}. "
                  f"Một kỳ không nói lên điều gì; xem thống kê tích lũy trên trang.",
                  f"Xác suất mỗi vé trúng độc đắc: 1 trên {round(1 / match_probs(g.max_number)[6]):,}".replace(",", " ") + "."]
        cur.execute("""INSERT INTO reports (kind, game, draw_no, content_vi, data_refs, row_hash, prev_hash)
                       VALUES ('post_draw',%s,%s,%s,%s,'','')""",
                    (game, no, "\n".join(lines), _j({"tickets": len(rows), "mean_hits": round(avg, 3)})))
        made.append(f"{g.name_vi} {no:05d}")
    return made


def weekly_report(cur, force: bool = False) -> str | None:
    now = datetime.now(timezone.utc)
    cur.execute("SELECT max(created_at) FROM reports WHERE kind = 'weekly'")
    last = cur.fetchone()[0]
    if not force and last and now - last < WEEKLY_EVERY:
        return None
    since = now - timedelta(days=7)
    lines = [f"# Báo cáo tuần ({since:%d/%m} – {now:%d/%m/%Y})", ""]
    for game, g in GAMES.items():
        cur.execute("""SELECT count(*), max(draw_no) FROM draw_current WHERE game = %s AND main_numbers IS NOT NULL
                       AND version_created_at >= %s""", (game, since))
        n_new, last_no = cur.fetchone()
        cur.execute("""SELECT count(*), avg(x.main_hits), count(*) FILTER (WHERE x.main_hits >= 3)
                       FROM ticket_results x JOIN tickets t ON t.id = x.ticket_id JOIN prediction_runs r ON r.id = t.run_id
                       WHERE r.game = %s AND r.purpose = 'live'""", (game,))
        n_t, avg_h, g3 = cur.fetchone()
        cur.execute("""SELECT count(*), count(*) FILTER (WHERE e.succeeded) FROM experiments e
                       JOIN hypotheses h ON h.id = e.hypothesis_id WHERE h.params->>'game' = %s""", (game,))
        n_e, n_ok = cur.fetchone()
        mu = 36 / g.max_number
        lines += [f"## {g.name_vi}",
                  f"- Kỳ mới trong tuần: {n_new}" + (f" (mới nhất {last_no:05d})" if last_no else ""),
                  f"- Vé live đã chấm (tích lũy): {n_t}; TB {float(avg_h or 0):.3f} số/vé (ngẫu nhiên {mu:.3f}); "
                  f"từ 3 số: {g3}",
                  f"- Giả thuyết đã kiểm (tích lũy): {n_e}; xác nhận: {n_ok}", ""]
    cur.execute("""SELECT job_name, count(*), count(*) FILTER (WHERE status = 'success'),
                          count(*) FILTER (WHERE status = 'failed') FROM job_runs WHERE started_at >= %s
                   GROUP BY 1 ORDER BY 1""", (since,))
    lines.append("## Tác vụ tự động (7 ngày)")
    for name, n, ok, bad in cur.fetchall():
        lines.append(f"- {name}: {n} lần, thành công {ok}, lỗi {bad}")
    cur.execute("""SELECT b.created_at, t.ok FROM backups b JOIN backup_restore_tests t ON t.backup_id = b.id
                   WHERE b.created_at >= %s ORDER BY b.id""", (since,))
    bk = cur.fetchall()
    lines += ["", f"## Sao lưu: {len(bk)} bản trong tuần, thử khôi phục đạt {sum(1 for _, ok in bk if ok)}/{len(bk)}"]
    cur.execute("""INSERT INTO reports (kind, game, content_vi, data_refs, row_hash, prev_hash)
                   VALUES ('weekly', NULL, %s, %s, '', '')""", ("\n".join(lines), _j({"since": since.isoformat()})))
    return "Báo cáo tuần: đã tạo."


def run_reports(conn) -> str:
    try:
        with conn.cursor() as cur:
            made = post_draw_reports(cur)
            weekly = weekly_report(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    out = [f"Báo cáo sau kỳ quay: {', '.join(made)}" if made else "Báo cáo sau kỳ quay: không có kỳ mới được chấm."]
    if weekly:
        out.append(weekly)
    return "\n".join(out)


# ---------------------------------------------------------------- đọc
def automation_report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT id, job_name, trigger_kind, started_at, finished_at, status, left(summary_vi, 600)
                       FROM job_runs ORDER BY id DESC LIMIT 20""")
        jobs = [dict(zip(("id", "job_name", "trigger", "started_at", "finished_at", "status", "summary_vi"), r))
                for r in cur.fetchall()]
        cur.execute("""SELECT DISTINCT ON (job_name) job_name, finished_at FROM job_runs WHERE status IN ('success','partial')
                       ORDER BY job_name, finished_at DESC""")
        last_ok = {n: t for n, t in cur.fetchall()}
        cur.execute("""SELECT b.id, b.created_at, b.size_bytes, b.sha256, t.ok, t.details_vi FROM backups b
                       LEFT JOIN backup_restore_tests t ON t.backup_id = b.id ORDER BY b.id DESC LIMIT 10""")
        backups = [dict(zip(("id", "created_at", "size_bytes", "sha256", "restore_ok", "details_vi"), r))
                   for r in cur.fetchall()]
        cur.execute("""SELECT id, kind, game, draw_no, created_at, content_vi FROM reports
                       WHERE kind IN ('post_draw', 'weekly') ORDER BY id DESC LIMIT 15""")
        reports = [dict(zip(("id", "kind", "game", "draw_no", "created_at", "content_vi"), r)) for r in cur.fetchall()]
    conn.rollback()
    return {"ready": True, "jobs": jobs, "last_ok": last_ok, "backups": backups, "reports": reports,
            "schedule_vi": "Vercel Cron chạy 2 lần/ngày: ~19:45 (sau giờ quay) và ~07:00 (thu thập lại kết quả công bố muộn). "
                           "Mỗi lần: thu thập → đối chiếu → chấm vé live → ghi vé kỳ tới → giả thuyết → báo cáo; "
                           "sao lưu + thử khôi phục mỗi ngày một lần."}
