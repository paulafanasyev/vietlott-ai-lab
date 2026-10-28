"""Giai đoạn 6: nghiệm thu tự động trả đủ 14 mục; mục bất biến phải ĐẠT (UPDATE thật bị CSDL từ chối)."""

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


@unittest.skipUnless(URL, "cần TEST_DATABASE_URL (PostgreSQL)")
class AcceptanceDB(unittest.TestCase):
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
                                     source_url="https://x/y", observed_at=now.isoformat()) for i in range(10)]
            store.add_observations(obs, fid)
        store.reconcile_game("mega645")
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_fourteen_items(self):
        from vlab.acceptance import PASS, run_acceptance
        rep = run_acceptance(self.conn)
        self.assertEqual(len(rep["items"]), 14)
        by = {i["no"]: i for i in rep["items"]}
        self.assertEqual(by[4]["status"], PASS, by[4])
        self.assertEqual(by[2]["status"], PASS, by[2])
        self.assertEqual(sum(rep["summary"].values()), 14)
        self.assertNotIn("Vercel đặt tại Mỹ", by[1]["evidence_vi"])
        self.assertIn("CROSS-CHECKED", by[1]["evidence_vi"])
        with self.conn.cursor() as cur:  # thử UPDATE đã bị hoàn tác, kết nối vẫn dùng được
            cur.execute("SELECT count(*) FROM draw_versions")
            self.assertGreaterEqual(cur.fetchone()[0], 10)
        self.conn.rollback()
