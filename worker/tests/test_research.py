"""Giai đoạn 4: bộ sinh giả thuyết, phần chia dữ liệu, hiệu chỉnh BH; với CSDL: thí nghiệm (kể cả thất bại) được lưu."""

import os
import random
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from vlab.research import CATALOG, bh_adjust, evaluate, propose, spec_key, splits, stats

URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


def fake(n, max_number=45, seed=5):
    rng = random.Random(seed)
    return [(i + 1, tuple(sorted(rng.sample(range(1, max_number + 1), 6)))) for i in range(n)]


class Pure(unittest.TestCase):
    def test_splits_ordered_disjoint(self):
        s = splits(1000)
        self.assertEqual(s, {"dev": (100, 600), "validation": (600, 800), "test": (800, 1000)})

    def test_bh(self):
        adj = bh_adjust([0.01, 0.04, 0.03, 0.5])
        self.assertAlmostEqual(adj[0], 0.04)
        self.assertAlmostEqual(adj[3], 0.5)
        self.assertTrue(all(a >= p for a, p in zip(adj, [0.01, 0.04, 0.03, 0.5])))

    def test_no_lookahead(self):
        d = fake(400)
        specs = CATALOG[:5]
        a = evaluate(specs, 45, d, "s")
        d2 = d[:-1] + [(400, (1, 2, 3, 4, 5, 6))]
        b = evaluate(specs, 45, d2, "s")
        for x, y in zip(a, b):  # chỉ kết quả kỳ cuối (đã đổi) được phép khác
            self.assertEqual(x["dev"], y["dev"])
            self.assertEqual(x["validation"], y["validation"])
            self.assertEqual(x["test"][:-1], y["test"][:-1])

    def test_random_data_mean_near_baseline(self):
        res = evaluate(CATALOG, 45, fake(1200), "s")
        for r in res:
            st = stats(r["dev"], 45)
            self.assertLess(abs(st["mean"] - 0.8), 0.15)

    def test_window_counts(self):
        from vlab.research import _Feats
        d = fake(500)
        f = _Feats(45, {7, 400}, {10})
        for _, m in d:
            f.add(m)
        for w in (7, 400):
            exp = [0] * 46
            for _, m in d[-w:]:
                for x in m:
                    exp[x] += 1
            self.assertEqual(f.win[w], exp)

    def test_propose_catalog_then_neighbours(self):
        first = propose({}, 4)
        self.assertEqual(first, CATALOG[:4])
        tested = {spec_key(s): {"id": i, "spec": s, "dev_z": float(i)} for i, s in enumerate(CATALOG)}
        nxt = propose(tested, 3)
        self.assertTrue(nxt)
        self.assertTrue(all(spec_key(s) not in tested for s in nxt))


@unittest.skipUnless(URL, "cần TEST_DATABASE_URL (PostgreSQL)")
class ResearchDB(unittest.TestCase):
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

    def load(self, store, n=300):
        from vlab.models import Observation
        from vlab.sources.http import FetchRecord
        job = store.new_job_id()
        start = date(2016, 7, 20)
        for src in ("pqminh4", "nhanaz"):
            now = datetime.now(timezone.utc)
            fid = store.record_fetch(src, "mega645", FetchRecord(url="https://x/y", started_at=now, finished_at=now,
                                     ok=True, http_status=200, raw_sha256="a" * 64, body=b"x"), job)
            obs = [Observation.build(source=src, game="mega645", draw_no=f"{no:05d}",
                                     draw_date=(start + timedelta(days=7 * i)).isoformat(), main_numbers=m,
                                     bonus_number=None, source_url="https://x/y", observed_at=now.isoformat())
                   for i, (no, m) in enumerate(fake(n))]
            store.add_observations(obs, fid)
        store.reconcile_game("mega645")
        self.conn.commit()

    def test_cycle_saves_failed_experiments(self):
        import psycopg
        from vlab.research import research_report, run_research, search_knowledge
        from vlab.store import Store
        store = Store(self.conn)
        self.load(store)
        text = run_research(self.conn, "testsha", limit=3)
        self.assertIn("Kho tri thức", text)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE NOT succeeded) FROM experiments")
            n, failed = cur.fetchone()
            self.assertEqual(n, 3)
            self.assertGreaterEqual(failed, 1)  # thí nghiệm thất bại cũng được lưu
            cur.execute("SELECT count(*) FROM backtests WHERE split IN ('dev','validation')")
            self.assertEqual(cur.fetchone()[0], 6)
            cur.execute("SELECT count(*) FROM metrics")
            self.assertGreaterEqual(cur.fetchone()[0], 6)
            cur.execute("SELECT count(*) FROM hypothesis_status_events")
            self.assertGreaterEqual(cur.fetchone()[0], 9)
            cur.execute("SELECT count(*) FROM knowledge_docs WHERE source_type = 'internal'")
            self.assertEqual(cur.fetchone()[0], 3)
            # Bộ test niêm phong: chạy lần hai cho cùng phiên bản bị CSDL từ chối.
            cur.execute("""SELECT method_version_id FROM experiments
                           WHERE NOT (overfitting_checks->>'sealed_test_used')::boolean LIMIT 1""")
            mv = cur.fetchone()[0]
            sql = """INSERT INTO backtests (method_version_id, game, split, from_draw_no, to_draw_no, tickets_per_draw,
                     started_at, status) VALUES (%s,'mega645','test',1,2,1,now(),'finished')"""
            cur.execute("SAVEPOINT s")
            cur.execute(sql, (mv,))
            with self.assertRaises(psycopg.errors.UniqueViolation):
                cur.execute(sql, (mv,))
            cur.execute("ROLLBACK TO SAVEPOINT s")
            # Bất biến: không sửa được thí nghiệm.
            cur.execute("SAVEPOINT s2")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("UPDATE experiments SET succeeded = true")
            cur.execute("ROLLBACK TO SAVEPOINT s2")
        self.conn.rollback()
        # Lần chạy sau: không kiểm lại giả thuyết cũ.
        run_research(self.conn, "testsha", limit=3)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(DISTINCT params) , count(*) FROM hypotheses")
            d, n = cur.fetchone()
            self.assertEqual((d, n), (6, 6))
        self.conn.rollback()
        chains = store.verify_chains()
        for t in ("hypotheses", "experiments", "method_versions", "ai_journal"):
            self.assertEqual(chains[t], [], t)
        self.conn.rollback()
        rep = research_report(self.conn, "mega645")
        self.assertEqual(rep["n_tested"], 6)
        found = search_knowledge(self.conn, "Benjamini")
        self.assertTrue(found["docs"])
        self.conn.rollback()
