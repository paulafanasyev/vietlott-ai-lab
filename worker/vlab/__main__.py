"""Dòng lệnh: python -m vlab {migrate,ingest,checkpoint,verify,export}."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MIGRATE_LOCK = 7_645_655  # khóa tư vấn cố định cho migrate
GAMES = ["mega645", "power655"]


def _connect(autocommit: bool = False):
    import psycopg
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Thiếu biến môi trường DATABASE_URL")
    return psycopg.connect(url, autocommit=autocommit)


def migrate(conn, migrations_dir: Path) -> list[int]:
    """Mỗi tệp chạy trong MỘT giao dịch cùng với dòng schema_migrations; khóa chống chạy song song."""
    applied = []
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATE_LOCK,))
    conn.commit()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
            done = set()
            if cur.fetchone()[0]:
                cur.execute("SELECT version FROM public.schema_migrations")
                done = {r[0] for r in cur.fetchall()}
        conn.commit()
        for path in sorted(migrations_dir.glob("[0-9]*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version in done:
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute("SELECT 1 FROM public.schema_migrations WHERE version = %s", (version,))
                    if cur.fetchone() is None:
                        raise RuntimeError(f"{path.name} không tự ghi schema_migrations({version})")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied.append(version)
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATE_LOCK,))
        conn.commit()
    return applied


_VI = {
    "usage: ": "cách dùng: ",
    "positional arguments": "lệnh",
    "options": "tùy chọn",
    "show this help message and exit": "hiện trợ giúp",
    "invalid choice: %(value)r (choose from %(choices)s)": "giá trị không hợp lệ: %(value)r (chọn: %(choices)s)",
    "the following arguments are required: %s": "thiếu tham số bắt buộc: %s",
    "argument %(argument_name)s: %(message)s": "tham số %(argument_name)s: %(message)s",
    "invalid %(type)s value: %(value)r": "giá trị %(type)s không hợp lệ: %(value)r",
    "unrecognized arguments: %s": "tham số không nhận ra: %s",
    "expected one argument": "cần một giá trị",
}
_orig_gettext = argparse._
argparse._ = lambda text: _VI.get(text, _orig_gettext(text))  # giao diện dòng lệnh bằng tiếng Việt


class _ViParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"Lỗi tham số: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _ViParser(prog="vlab", description="Vietlott AI Lab — công cụ dòng lệnh", add_help=False)
    p.add_argument("-h", "--help", action="help", help="hiện trợ giúp")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="LỆNH", parser_class=_ViParser)
    sub.add_parser("migrate", help="áp dụng các migration CSDL")
    ing = sub.add_parser("ingest", help="thu thập và đối chiếu kết quả")
    ing.add_argument("--trigger", default="manual", choices=["schedule", "manual", "ci"], help="nguồn kích hoạt")
    ing.add_argument("--game", action="append", choices=GAMES, help="chỉ một trò chơi (có thể lặp)")
    ing.add_argument("--official-pages", type=int, default=1, help="số trang danh sách chính thức (1..20)")
    cp = sub.add_parser("checkpoint", help="tạo điểm kiểm tra toàn vẹn và in ra JSON")
    cp.add_argument("--out", type=Path, help="ghi JSON vào tệp")
    ver = sub.add_parser("verify", help="kiểm tra chuỗi băm và điểm kiểm tra")
    ver.add_argument("--checkpoint", type=Path, help="tệp JSON điểm kiểm tra bên ngoài để đối chiếu")
    exp = sub.add_parser("export", help="xuất dữ liệu ra CSV/JSON")
    exp.add_argument("--kind", choices=["draws", "history", "observations", "conflicts"], default="draws",
                     help="loại dữ liệu")
    exp.add_argument("--format", choices=["csv", "json"], default="csv", help="định dạng")
    exp.add_argument("--game", choices=GAMES, help="chỉ một trò chơi")
    exp.add_argument("--out", type=Path, required=True, help="tệp đầu ra")
    rb = sub.add_parser("record-backup", help="ghi nhận một bản sao lưu và kết quả thử khôi phục")
    rb.add_argument("--location", required=True, help="vị trí bản sao lưu (ví dụ github:owner/repo/path)")
    rb.add_argument("--file", type=Path, required=True, help="tệp sao lưu để tính kích thước và băm")
    rb.add_argument("--restore-ok", choices=["true", "false"], required=True, help="thử khôi phục có thành công")
    rb.add_argument("--details", default="", help="mô tả kết quả thử khôi phục (tiếng Việt)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    conn = _connect()
    try:
        if args.cmd == "migrate":
            root = Path(__file__).resolve().parents[2] / "db" / "migrations"
            print("Đã áp dụng:", migrate(conn, root) or "không có gì mới")
            return 0
        from .store import Store
        if args.cmd == "ingest":
            from .ingest import run
            audit = _connect(autocommit=True)
            try:
                return run(conn, audit, trigger=args.trigger, games=args.game, official_pages=args.official_pages)
            finally:
                audit.close()
        store = Store(conn)
        if args.cmd == "checkpoint":
            cp = store.checkpoint()
            conn.commit()
            text = json.dumps(cp, ensure_ascii=False, indent=1, sort_keys=True)
            if args.out:
                args.out.write_text(text, encoding="utf-8")
            print(text)
            return 0
        if args.cmd == "verify":
            bad = {t: rows for t, rows in store.verify_chains().items() if rows}
            cp = json.loads(args.checkpoint.read_text(encoding="utf-8")) if args.checkpoint else store.latest_checkpoint()
            cp_bad = store.verify_checkpoint(cp) if cp else []
            if bad or cp_bad:
                print("PHÁT HIỆN SAI LỆCH:", {"chuỗi băm": bad, "điểm kiểm tra": cp_bad})
                return 2
            where = f"điểm kiểm tra #{cp['checkpoint_id']}" if cp else "chưa có điểm kiểm tra"
            print(f"Toàn vẹn: chuỗi băm đúng; khớp {where}.")
            return 0
        if args.cmd == "export":
            cols, rows = store.export_rows(args.kind, args.game)
            text = Store.to_csv(cols, rows) if args.format == "csv" else Store.to_json(cols, rows)
            args.out.write_text(text, encoding="utf-8")
            print(f"Đã xuất {len(rows)} dòng ({args.kind}) vào {args.out}")
            return 0
        if args.cmd == "record-backup":
            import hashlib
            data = args.file.read_bytes()
            bid = store.record_backup(args.location, len(data), hashlib.sha256(data).hexdigest())
            ok = args.restore_ok == "true"
            store.record_restore_test(bid, ok, args.details or ("Khôi phục và kiểm tra toàn vẹn thành công" if ok
                                                                 else "Khôi phục hoặc kiểm tra toàn vẹn THẤT BẠI"))
            conn.commit()
            print(f"Đã ghi bản sao lưu #{bid}; thử khôi phục: {'đạt' if ok else 'KHÔNG đạt'}")
            return 0 if ok else 3
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
