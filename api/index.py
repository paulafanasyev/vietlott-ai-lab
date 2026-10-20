"""Vercel: API và lịch thu thập tự động cho Vietlott AI Lab.

Mọi văn bản trả cho người dùng bằng tiếng Việt. Một hàm duy nhất phục vụ các đường dẫn:
  /api/cron    — thu thập + đối chiếu (Vercel Cron gọi mỗi ngày; người dùng có thể gọi tay)
  /api/status  — tình trạng CSDL, số kỳ theo trạng thái, các lần chạy gần nhất, kiểm tra toàn vẹn
  /api/draws   — kết quả hiện hành (?game=mega645|power655&limit=50)
  /api/export  — xuất dữ liệu (?kind=draws|history|observations|conflicts&format=csv|json&game=)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

GAMES = ("mega645", "power655")
KINDS = ("draws", "history", "observations", "conflicts")
MANUAL_COOLDOWN = timedelta(minutes=10)
JSON_HDR = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]


def db_url() -> str:
    """Tích hợp Neon của Vercel đặt nhiều biến; ưu tiên kết nối TRỰC TIẾP (không qua pooler),
    vì khóa tư vấn cấp phiên không hoạt động qua PgBouncer chế độ transaction."""
    for key in ("DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING", "DATABASE_URL", "POSTGRES_URL"):
        value = os.environ.get(key)
        if value:
            return value.replace("-pooler.", ".")
    raise LookupError("Chưa kết nối CSDL: trong Vercel mở Storage → Neon → Connect cho dự án này.")


def connect(autocommit: bool = False):
    url = db_url()
    import psycopg
    return psycopg.connect(url, autocommit=autocommit, connect_timeout=20)


def _j(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=1).encode("utf-8")


def _ready(cur) -> bool:
    cur.execute("SELECT to_regclass('public.draw_current') IS NOT NULL")
    return cur.fetchone()[0]


# ---------------------------------------------------------------- thu thập
def _cron_allowed(environ, q) -> tuple[bool, str, str]:
    """Trả về (được phép, trigger, lý do). Vercel Cron gửi 'Authorization: Bearer $CRON_SECRET' nếu biến này
    được đặt, và luôn gửi User-Agent 'vercel-cron/1.0'. Gọi tay: chỉ khi lần chạy trước đã cách ít nhất 10 phút."""
    secret = os.environ.get("CRON_SECRET")
    auth = environ.get("HTTP_AUTHORIZATION", "")
    agent = environ.get("HTTP_USER_AGENT", "")
    if secret and auth == f"Bearer {secret}":
        return True, "schedule", ""
    if not secret and agent.startswith("vercel-cron"):
        return True, "schedule", ""
    if q.get("manual") != "1":
        return False, "", "Chỉ lịch tự động của Vercel hoặc nút «Chạy ngay» được gọi đường dẫn này."
    try:
        conn = connect()
    except LookupError as exc:
        return False, "", str(exc)
    try:
        with conn.cursor() as cur:
            if not _ready(cur):
                return True, "manual", ""
            cur.execute("SELECT max(started_at) FROM job_runs WHERE job_name = 'ingest'")
            last = cur.fetchone()[0]
    finally:
        conn.close()
    if last and datetime.now(timezone.utc) - last < MANUAL_COOLDOWN:
        wait = int((MANUAL_COOLDOWN - (datetime.now(timezone.utc) - last)).total_seconds() // 60) + 1
        return False, "", f"Vừa chạy xong; thử lại sau khoảng {wait} phút."
    return True, "manual", ""


def do_ingest(trigger: str) -> dict:
    from vlab.__main__ import migrate
    from vlab.ingest import run
    from vlab.sources.http import Http
    conn = connect()
    try:
        applied = migrate(conn, ROOT / "db" / "migrations")
        audit = connect(autocommit=True)
        try:
            buf = io.StringIO()
            # Thời gian chờ ngắn: hàm Vercel có giới hạn thời gian; nguồn bị chặn chỉ làm lần chạy thành 'partial'.
            http = Http(timeout=12, retries=1, min_interval=1.0)
            with contextlib.redirect_stdout(buf):
                code = run(conn, audit, trigger=trigger, http=http)
        finally:
            audit.close()
    finally:
        conn.close()
    return {"ok": code == 0, "exit_code": code, "migrations_applied": applied, "summary_vi": buf.getvalue()}


# ---------------------------------------------------------------- đọc dữ liệu
def status() -> dict:
    from vlab.store import Store
    conn = connect()
    try:
        with conn.cursor() as cur:
            if not _ready(cur):
                return {"ready": False, "message_vi": "CSDL chưa được khởi tạo. Bấm «Chạy ngay» để tạo và thu thập lần đầu."}
            cur.execute("SELECT game, status, count(*) FROM draw_current GROUP BY 1, 2 ORDER BY 1, 2")
            counts: dict = {}
            for game, st, n in cur.fetchall():
                counts.setdefault(game, {})[st] = n
            cur.execute("""SELECT game, max(draw_no), max(draw_date) FROM draw_current
                           WHERE main_numbers IS NOT NULL GROUP BY game""")
            latest = {g: {"draw_no": f"{n:05d}", "draw_date": d} for g, n, d in cur.fetchall()}
            cur.execute("""SELECT id, trigger_kind, started_at, finished_at, status, summary_vi FROM job_runs
                           WHERE job_name = 'ingest' ORDER BY id DESC LIMIT 5""")
            runs = [dict(zip(("id", "trigger", "started_at", "finished_at", "status", "summary_vi"), r))
                    for r in cur.fetchall()]
        store = Store(conn)
        bad = {t: len(rows) for t, rows in store.verify_chains().items() if rows}
        cp = store.latest_checkpoint()
        cp_bad = store.verify_checkpoint(cp) if cp else []
        conn.rollback()
        return {"ready": True, "counts": counts, "latest": latest, "runs": runs,
                "integrity": {"ok": not bad and not cp_bad, "checkpoint_id": cp["checkpoint_id"] if cp else None,
                              "broken_chains": bad, "checkpoint_problems": [list(p) for p in cp_bad]}}
    finally:
        conn.close()


def draws(game: str, limit: int) -> dict:
    conn = connect()
    try:
        with conn.cursor() as cur:
            if not _ready(cur):
                return {"ready": False, "rows": []}
            cur.execute("""SELECT draw_no, draw_date, draw_time, main_numbers, bonus_number, status, reason_vi
                           FROM draw_current WHERE game = %s ORDER BY draw_no DESC LIMIT %s""", (game, limit))
            cols = ("draw_no", "draw_date", "draw_time", "main_numbers", "bonus_number", "status", "reason_vi")
            rows = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                row["draw_no"] = f"{row['draw_no']:05d}"
                rows.append(row)
        return {"ready": True, "game": game, "rows": rows}
    finally:
        conn.close()


def export(kind: str, fmt: str, game: str | None) -> tuple[bytes, str, str]:
    from vlab.store import Store
    conn = connect()
    try:
        cols, rows = Store(conn).export_rows(kind, game)
    finally:
        conn.close()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    name = f"vietlott-{kind}{'-' + game if game else ''}-{stamp}.{fmt}"
    if fmt == "csv":
        return Store.to_csv(cols, rows).encode("utf-8-sig"), "text/csv; charset=utf-8", name
    return Store.to_json(cols, rows).encode("utf-8"), "application/json; charset=utf-8", name


# ---------------------------------------------------------------- định tuyến
def route(path: str, q: dict, environ) -> tuple[str, list, bytes]:
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if name == "cron":
        ok, trigger, why = _cron_allowed(environ, q)
        if not ok:
            return "403 Forbidden", JSON_HDR, _j({"ok": False, "error_vi": why})
        return "200 OK", JSON_HDR, _j(do_ingest(trigger))
    if name == "status":
        return "200 OK", JSON_HDR, _j(status())
    if name == "draws":
        game = q.get("game", "mega645")
        if game not in GAMES:
            return "400 Bad Request", JSON_HDR, _j({"error_vi": f"Trò chơi không hợp lệ: {game}"})
        try:
            limit = max(1, min(int(q.get("limit", "50")), 500))
        except ValueError:
            limit = 50
        return "200 OK", JSON_HDR, _j(draws(game, limit))
    if name == "export":
        kind, fmt, game = q.get("kind", "draws"), q.get("format", "csv"), q.get("game") or None
        if kind not in KINDS or fmt not in ("csv", "json") or (game and game not in GAMES):
            return "400 Bad Request", JSON_HDR, _j({"error_vi": "Tham số xuất dữ liệu không hợp lệ"})
        body, ctype, fname = export(kind, fmt, game)
        return "200 OK", [("Content-Type", ctype), ("Content-Disposition", f'attachment; filename="{fname}"'),
                          ("Cache-Control", "no-store")], body
    return "404 Not Found", JSON_HDR, _j({"error_vi": "Không có đường dẫn này"})


def app(environ, start_response):
    q = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
    try:
        status_line, headers, body = route(environ.get("PATH_INFO", ""), q, environ)
    except LookupError as exc:
        status_line, headers, body = "503 Service Unavailable", JSON_HDR, _j({"ok": False, "error_vi": str(exc)})
    except Exception as exc:  # không lộ chuỗi kết nối: chỉ tên lỗi và thông điệp
        status_line, headers, body = "500 Internal Server Error", JSON_HDR, _j(
            {"ok": False, "error_vi": f"Lỗi máy chủ: {type(exc).__name__}: {str(exc)[:500]}"})
    start_response(status_line, headers + [("Content-Length", str(len(body)))])
    return [body]
