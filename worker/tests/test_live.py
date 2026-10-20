"""Giai đoạn 3: dự đoán live ghi trước giờ quay, không trùng lặp, chuỗi băm đúng. Cần TEST_DATABASE_URL."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vlab.live import prize_tier

URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


class PrizeTier(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(prize_tier("mega645", 6, None), "jackpot")
        self.assertEqual(prize_tier("power655", 6, False), "jackpot1")
        self.assertEqual(prize_tier("power655", 5, True), "jackpot2")
        self.assertEqual(prize_tier("power655", 5, False), "giai1")
        self.assertEqual(prize_tier("mega645", 3, None), "giai3")
        self.assertIsNone(prize_tier("mega645", 2, None))


def last_past_draw(game, now):
    from vlab.schedule import VN_TZ, draw_datetime
    local = now.astimezone(VN_TZ).date()
    for back in range(0, 8):
        d = local - timedelta(days=back)
        if d.weekday() in game.draw_weekdays and draw_datetime(game, d) <= now:
            return d
    raise AssertionError


@unittest.skipUnless(URL, "cần TEST_DATABASE_URL (PostgreSQL)")
class LiveTest(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def put(self, store, job, source, draw, d, main):
        from vlab.models import Observation
        from vlab.sources.http import FetchRecord
        now = datetime.now(timezone.utc)
        fid = store.record_fetch(source, "mega645", FetchRecord(
            url="https://x/y", started_at=now, finished_at=now, ok=True, http_status=200,
            raw_sha256="a" * 64, body=b"x"), job)
        o = Observation.build(source=source, game="mega645", draw_no=draw, draw_date=d, main_numbers=main,
                              bonus_number=None, source_url="https://x/y", observed_at=now.isoformat())
        store.add_observations([o], fid)

    def test_live_flow(self):
        from vlab.games import MEGA645
        from vlab.live import run_live
        from vlab.methods import CODES
        from vlab.store import Store
        store = Store(self.conn)
        job = store.new_job_id()
        last = last_past_draw(MEGA645, datetime.now(timezone.utc))
        mains = [(9, 20, 26, 35, 36, 38), (1, 7, 14, 22, 30, 45), (3, 8, 19, 27, 33, 41),
                 (2, 11, 18, 29, 37, 44), (5, 12, 21, 31, 39, 43)]
        draws = [(100 - i, (last - timedelta(days=7 * i)).isoformat(), mains[i]) for i in range(5)]
        for no, d, main in draws:
            for src in ("pqminh4", "nhanaz"):
                self.put(store, job, src, f"{no:05d}", d, main)
        store.reconcile_game("mega645")
        self.conn.commit()

        text = run_live(self.conn, "testsha")
        self.assertIn("00101", text)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM prediction_runs WHERE game='mega645' AND target_draw_no=101 AND purpose='live'")
            self.assertEqual(cur.fetchone()[0], len(CODES))
            cur.execute("SELECT count(*) FROM tickets")
            self.assertEqual(cur.fetchone()[0], len(CODES))
            cur.execute("SELECT count(*) FROM method_versions")
            self.assertEqual(cur.fetchone()[0], len(CODES))
            cur.execute("SELECT data_cutoff_draw_no FROM prediction_runs LIMIT 1")
            self.assertEqual(cur.fetchone()[0], 100)
        self.conn.rollback()

        # Lần chạy thứ hai: không tạo trùng, không tạo phiên bản mới.
        text2 = run_live(self.conn, "testsha")
        self.assertIn("đã có dự đoán", text2)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM prediction_runs")
            self.assertEqual(cur.fetchone()[0], len(CODES))
            cur.execute("SELECT count(*) FROM method_versions")
            self.assertEqual(cur.fetchone()[0], len(CODES))
        chains = store.verify_chains()
        for t in ("method_versions", "prediction_runs", "tickets", "ai_journal"):
            self.assertEqual(chains[t], [], t)
        self.conn.rollback()

        from vlab.live import live_report
        rep = live_report(self.conn, "mega645")
        self.assertEqual(rep["draws"][0]["draw_no"], "00101")
        self.assertEqual(len(rep["draws"][0]["tickets"]), len(CODES))
        self.assertIsNone(rep["draws"][0]["result"])
        self.conn.rollback()
