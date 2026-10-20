"""Giai đoạn 5: sao lưu → khôi phục → đối chiếu băm; tệp bị sửa phải bị phát hiện; báo cáo và nhật ký tác vụ."""

import gzip
import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


@unittest.skipUnless(URL, "cần TEST_DATABASE_URL (PostgreSQL)")
class AutomationDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from vlab.__main__ import migrate
        admin = psycopg.connect(URL, autocommit=True)
        with admin.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        admin.close()
        cls.conn = psycopg.connect(URL)
        migrate(cls.conn, MIGRATIONS)
        from vlab.models import Observation
        from vlab.sources.http import FetchRecord
        from vlab.store import Store
        store = Store(cls.conn)
        job = store.new_job_id()
        for src in ("pqminh4", "nhanaz"):
            now = datetime.now(timezone.utc)
            fid = store.record_fetch(src, "mega645", FetchRecord(url="https://x/y", started_at=now, finished_at=now,
                                     ok=True, http_status=200, raw_sha256="a" * 64, body=b"x"), job)
            obs = [Observation.build(source=src, game="mega645", draw_no=f"{i + 1:05d}",
                                     draw_date=(date(2016, 7, 20) + timedelta(days=7 * i)).isoformat(),
                                     main_numbers=(1 + i % 30, 32, 33, 34, 35, 36), bonus_number=None,
                                     source_url="https://x/y", observed_at=now.isoformat()) for i in range(30)]
            store.add_observations(obs, fid)
        store.reconcile_game("mega645")
        store.journal("x", "none", {"a": 1})
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_backup_restore_roundtrip_and_tamper(self):
        from vlab.automation import backup_and_verify, backup_file, restore_check
        text = backup_and_verify(self.conn, force=True)
        self.assertIn("ĐẠT", text)
        with self.conn.cursor() as cur:
            cur.execute("SELECT t.ok, t.details_vi FROM backups b JOIN backup_restore_tests t ON t.backup_id = b.id")
            ok, details = cur.fetchone()
            self.assertTrue(ok, details)
        self.conn.rollback()
        self.assertIn("bỏ qua", backup_and_verify(self.conn))  # không sao lưu dày hơn 20 giờ
        blob, name = backup_file(self.conn)
        self.assertTrue(name.endswith(".json.gz"))
        doc = json.loads(gzip.decompress(blob))
        doc["tables"]["draw_versions"][0]["reason_vi"] = "bị sửa"
        bad = gzip.compress(json.dumps(doc, ensure_ascii=False).encode())
        with self.conn.cursor() as cur:
            self.assertEqual(restore_check(cur, blob), [])
        self.conn.rollback()
        with self.conn.cursor() as cur:
            probs = restore_check(cur, bad)
        self.conn.rollback()
        self.assertTrue(any(p.startswith("draw_versions") for p in probs), probs)

    def test_reports_and_jobs(self):
        from vlab.automation import automation_report, record_job, run_reports
        record_job(self.conn, "backup", "ci", datetime.now(timezone.utc), "success", "ok")
        text = run_reports(self.conn)
        self.assertIn("Báo cáo tuần", text)
        self.assertNotIn("Báo cáo tuần", run_reports(self.conn))  # chỉ một báo cáo tuần
        rep = automation_report(self.conn)
        self.assertTrue(any(j["job_name"] == "backup" for j in rep["jobs"]))
        self.assertTrue(any(r["kind"] == "weekly" for r in rep["reports"]))
