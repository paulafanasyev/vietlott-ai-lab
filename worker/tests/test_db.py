"""Kiểm thử với PostgreSQL thật. Chạy trong CI (dịch vụ postgres); bỏ qua nếu không có TEST_DATABASE_URL."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
FIX = Path(__file__).parent / "fixtures"
MAIN = (9, 20, 26, 35, 36, 38)


@unittest.skipUnless(URL, "cần TEST_DATABASE_URL (PostgreSQL)")
class DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from vlab.__main__ import migrate
        admin = psycopg.connect(URL, autocommit=True)
        with admin.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        admin.close()
        cls.conn = psycopg.connect(URL)
        cls.applied = migrate(cls.conn, MIGRATIONS)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def tearDown(self):
        self.conn.rollback()

    # ---------------------------------------------------------------- tiện ích
    def store(self):
        from vlab.store import Store
        return Store(self.conn)

    def obs(self, source, main=MAIN, draw="01576", d="2026-10-16", game="mega645", bonus=None):
        from vlab.models import Observation
        return Observation.build(source=source, game=game, draw_no=draw, draw_date=d, main_numbers=main,
                                 bonus_number=bonus, source_url="https://vietlott.vn/x",
                                 observed_at="2026-10-16T12:00:00+00:00")

    def fetch(self, store, source, job_id, ok=True):
        from vlab.sources.http import FetchRecord
        now = datetime.now(timezone.utc)
        return store.record_fetch(source, "mega645", FetchRecord(
            url="https://vietlott.vn/x", started_at=now, finished_at=now, ok=ok, http_status=200 if ok else 503,
            error_text=None if ok else "HTTP 503", raw_sha256="a" * 64 if ok else None, body=b"x" if ok else b""),
            job_id)

    def put(self, store, job, source, **kw):
        return store.add_observations([self.obs(source, **kw)], self.fetch(store, source, job))

    def versions(self, draw_no=1576):
        with self.conn.cursor() as cur:
            cur.execute("""SELECT v.version_no, v.status FROM draw_versions v JOIN draws d ON d.id = v.draw_id
                           WHERE d.draw_no = %s ORDER BY v.version_no""", (draw_no,))
            return cur.fetchall()

    def assert_raises_sql(self, err, sql, args=()):
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT s")
            try:
                with self.assertRaises(err, msg=sql):
                    cur.execute(sql, args)
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT s")

    # ---------------------------------------------------------------- kiểm thử
    def test_migration_applied_once(self):
        from vlab.__main__ import migrate
        self.assertEqual(self.applied, [1])
        self.assertEqual(migrate(self.conn, MIGRATIONS), [])

    def test_full_reconcile_flow_and_immutability(self):
        import psycopg
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "pqminh4")
        self.put(store, job, "nhanaz")
        changes = store.reconcile_game("mega645")
        self.assertEqual([(c.draw_no, c.new_status) for c in changes], [(1576, "CROSS-CHECKED")])
        self.assertEqual(store.reconcile_game("mega645"), [])  # không đổi → không có phiên bản mới
        with self.conn.cursor() as cur:
            cur.execute("SELECT draw_time::text, draw_time_source FROM draw_current WHERE draw_no = 1576")
            self.assertEqual(cur.fetchone(), ("18:00:00", "schedule"))

        self.put(store, job, "vietlott_ajax")
        self.assertEqual(store.reconcile_game("mega645")[0].new_status, "VERIFIED")
        self.assertEqual(self.versions(), [(1, "CROSS-CHECKED"), (2, "VERIFIED")])

        # Một kho sửa thành bộ số khác → CONFLICT + bản ghi xung đột; không dùng được nữa.
        self.assertEqual(self.put(store, job, "nhanaz", main=(9, 20, 26, 35, 36, 39)), 1)
        self.assertEqual(store.reconcile_game("mega645")[0].new_status, "CONFLICT")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM draw_conflicts")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM draws_usable")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM ai_journal WHERE action = 'draw_version'")
            self.assertEqual(cur.fetchone()[0], 3)  # trigger ghi nhật ký cho mọi phiên bản

        # Tất cả nguồn cùng "sửa" thành bộ số khác: tác vụ tự động vẫn KHÔNG được thay bản VERIFIED.
        self.put(store, job, "vietlott_ajax", main=(9, 20, 26, 35, 36, 39))
        self.put(store, job, "pqminh4", main=(9, 20, 26, 35, 36, 39))
        ch = store.reconcile_game("mega645")
        self.assertEqual(ch, [])  # vẫn CONFLICT, cùng nội dung → không có phiên bản mới
        with self.conn.cursor() as cur:
            cur.execute("SELECT status, main_numbers FROM draw_current WHERE draw_no = 1576")
            self.assertEqual(cur.fetchone(), ("CONFLICT", None))
            cur.execute("SELECT count(*) FROM draw_conflicts")
            self.assertEqual(cur.fetchone()[0], 2)  # bộ quan sát mới → bản ghi xung đột mới

        # Nguồn quay lại bộ số gốc → lại VERIFIED.
        for src in ("vietlott_ajax", "pqminh4", "nhanaz"):
            self.put(store, job, src)
        self.assertEqual(store.reconcile_game("mega645")[0].new_status, "VERIFIED")

        # Bảng job_runs phải có dòng: trigger theo dòng không chạy khi DELETE không chạm dòng nào.
        store.finish_job(job, "ingest", "ci", datetime.now(timezone.utc), datetime.now(timezone.utc),
                         "success", "ok", {}, "test")
        for sql in ("UPDATE draw_versions SET status='VERIFIED'", "DELETE FROM draw_observations",
                    "TRUNCATE draw_versions CASCADE", "UPDATE fetch_attempts SET ok = false",
                    "DELETE FROM job_runs"):
            self.assert_raises_sql(psycopg.errors.InsufficientPrivilege, sql)
        self.assertEqual(store.verify_chains()["draw_versions"], [])
        self.assertEqual(store.verify_chains()["ai_journal"], [])

    def test_conflict_on_first_sight_records_conflict(self):
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "pqminh4", draw="00500")
        self.put(store, job, "nhanaz", draw="00500", main=(1, 2, 3, 4, 5, 6))
        self.assertEqual(store.reconcile_game("mega645")[0].new_status, "CONFLICT")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM draw_conflicts c JOIN draws d ON d.id = c.draw_id WHERE d.draw_no = 500")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_verified_change_blocked_for_app_role(self):
        import psycopg
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "vietlott_ajax", draw="00600")
        self.put(store, job, "pqminh4", draw="00600")
        store.reconcile_game("mega645")
        with self.conn.cursor() as cur:
            cur.execute("""SELECT draw_id, version_id, version_no, observation_ids FROM draw_current WHERE draw_no = 600""")
            draw_id, vid, vno, ids = cur.fetchone()
            cur.execute("SET LOCAL ROLE vlab_app")
        sql = """INSERT INTO draw_versions (draw_id, version_no, draw_date, draw_time, draw_time_source, main_numbers,
                     status, observation_ids, reason_vi, previous_version_id, created_by, row_hash, prev_hash)
                 VALUES (%s,%s,'2026-10-16','18:00','schedule','{1,2,3,4,5,6}','VERIFIED',%s,'x',%s,'job:x','','')"""
        self.assert_raises_sql(psycopg.errors.InsufficientPrivilege, sql, (draw_id, vno + 1, ids, vid))
        # Chỉ đổi giờ quay của bản VERIFIED cũng bị chặn.
        sql_t = sql.replace("'18:00','schedule','{1,2,3,4,5,6}'", "'18:30','source','{9,20,26,35,36,38}'")
        self.assert_raises_sql(psycopg.errors.InsufficientPrivilege, sql_t, (draw_id, vno + 1, ids, vid))
        # Bằng chứng giả: id quan sát không tồn tại hoặc của kỳ khác.
        self.assert_raises_sql(psycopg.errors.CheckViolation, sql, (draw_id, vno + 1, [999999], vid))
        # vlab_app cũng không được giải quyết xung đột hay sửa danh mục nguồn.
        self.assert_raises_sql(psycopg.errors.InsufficientPrivilege,
                               "INSERT INTO sources VALUES ('x','x','archive','x','https://x','')")
        self.assert_raises_sql(psycopg.errors.InsufficientPrivilege, "UPDATE games SET max_number = 46")

    def test_observation_requires_ok_fetch_of_same_source(self):
        import psycopg
        store = self.store()
        job = store.new_job_id()
        bad = self.fetch(store, "pqminh4", job, ok=False)
        other = self.fetch(store, "nhanaz", job)
        for fid in (bad, other):
            with self.conn.cursor() as cur:
                cur.execute("SAVEPOINT s")
                with self.assertRaises(psycopg.errors.RaiseException):
                    store.add_observations([self.obs("pqminh4", draw="00700")], fid)
                cur.execute("ROLLBACK TO SAVEPOINT s")

    def test_identical_observation_not_duplicated(self):
        store = self.store()
        job = store.new_job_id()
        f = self.fetch(store, "pqminh4", job)
        self.assertEqual(store.add_observations([self.obs("pqminh4", draw="00010")], f), 1)
        f2 = self.fetch(store, "pqminh4", job)
        self.assertEqual(store.add_observations([self.obs("pqminh4", draw="00010")], f2), 0)

    def test_pending_only_next_draw(self):
        store = self.store()
        when = datetime(2026, 10, 16, 11, 0, tzinfo=timezone.utc)  # 18:00 giờ VN
        self.assertTrue(store.add_pending("mega645", 9001, when))
        self.assertFalse(store.add_pending("mega645", 9001, when))
        self.assertEqual(self.versions(9001), [(1, "PENDING")])
        with self.conn.cursor() as cur:
            cur.execute("SELECT draw_date::text, draw_time::text, draw_time_source, main_numbers FROM draw_current WHERE draw_no = 9001")
            self.assertEqual(cur.fetchone(), ("2026-10-16", "18:00:00", "schedule", None))

    def test_app_role_can_run_whole_stage1_flow(self):
        """Quyền tối thiểu vẫn đủ cho toàn bộ tác vụ tự động; nhưng không đủ cho việc của người duyệt."""
        import psycopg
        with self.conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE vlab_app")
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "vietlott_ajax", draw="01000")
        self.put(store, job, "pqminh4", draw="01000")
        self.assertEqual(store.reconcile_game("mega645")[0].new_status, "VERIFIED")
        from vlab.games import MEGA645
        from vlab.schedule import next_draw_after
        store.add_pending("mega645", 1001, next_draw_after(MEGA645, datetime.now(timezone.utc)))
        store.journal("x", "none", {})
        store.report("ingest", None, "Báo cáo", {})
        cp = store.checkpoint()
        self.assertEqual(store.verify_checkpoint(cp), [])
        bid = store.record_backup("github:x/y", 10, "c" * 64)
        store.record_restore_test(bid, True, "ok")
        store.finish_job(job, "ingest", "ci", datetime.now(timezone.utc), datetime.now(timezone.utc),
                         "success", "ok", {}, "test")
        for sql in ("INSERT INTO settings_history (key, new_value, changed_by) VALUES ('a', '1', 'x')",
                    "INSERT INTO audit_log (actor, action, row_hash, prev_hash) VALUES ('x','y','','')",
                    "INSERT INTO methods (code, name_vi, family, description_vi) VALUES ('z','z','random','')",
                    "INSERT INTO draw_conflict_resolutions (conflict_id, resolved_version_id, resolved_by, note_vi) VALUES (1,1,'x','x')",
                    "INSERT INTO chain_checkpoints (tables, row_hash, prev_hash) VALUES ('{}', '', '')",
                    "SELECT vlab_make_append_only('draws')",
                    "SELECT * FROM vlab_table_digest('pg_authid'::regclass, 1)",
                    "SELECT * FROM methods"):
            self.assert_raises_sql(psycopg.errors.InsufficientPrivilege, sql)

    def test_checkpoint_detects_tail_delete_and_edit(self):
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "pqminh4", draw="01100")
        self.put(store, job, "nhanaz", draw="01100")
        store.reconcile_game("mega645")
        cp = store.checkpoint()
        self.assertEqual(store.verify_checkpoint(cp), [])
        self.put(store, job, "vietvudanh", draw="01101")  # dòng mới sau điểm kiểm tra không phải sai lệch
        self.assertEqual(store.verify_checkpoint(cp), [])
        with self.conn.cursor() as cur:  # chủ sở hữu tắt trigger, xóa dòng CUỐI (chuỗi băm không thấy được)
            cur.execute("SAVEPOINT s")
            cur.execute("ALTER TABLE draw_versions DISABLE TRIGGER append_only_row")
            cur.execute("DELETE FROM draw_versions WHERE id = (SELECT max(id) FROM draw_versions)")
            self.assertEqual(store.verify_chains()["draw_versions"], [])  # chuỗi vẫn "đúng"
            self.assertIn("draw_versions", [t for t, _ in store.verify_checkpoint(cp)])
            cur.execute("ROLLBACK TO SAVEPOINT s")
            cur.execute("ALTER TABLE fetch_attempts DISABLE TRIGGER append_only_row")
            # 201 vẫn hợp lệ với ràng buộc ok → 2xx; chỉ nội dung dòng thay đổi.
            cur.execute("UPDATE fetch_attempts SET http_status = 201 WHERE id = (SELECT min(id) FROM fetch_attempts)")
            self.assertIn("fetch_attempts", [t for t, _ in store.verify_checkpoint(cp)])
            cur.execute("ROLLBACK TO SAVEPOINT s")
        self.assertEqual(store.verify_checkpoint(cp), [])

    def test_resolution_must_match_conflict_draw(self):
        import psycopg
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "pqminh4", draw="01200")
        self.put(store, job, "nhanaz", draw="01200", main=(1, 2, 3, 4, 5, 6))
        self.put(store, job, "pqminh4", draw="01201")
        store.reconcile_game("mega645")
        with self.conn.cursor() as cur:
            cur.execute("SELECT c.id FROM draw_conflicts c JOIN draws d ON d.id=c.draw_id WHERE d.draw_no=1200")
            cid = cur.fetchone()[0]
            cur.execute("SELECT version_id FROM draw_current WHERE draw_no = 1201")
            other = cur.fetchone()[0]
        self.assert_raises_sql(psycopg.errors.CheckViolation,
            "INSERT INTO draw_conflict_resolutions (conflict_id, resolved_version_id, resolved_by, note_vi) VALUES (%s,%s,'u','x')",
            (cid, other))

    def test_verified_time_change_becomes_conflict(self):
        """Nguồn đổi giờ quay của kỳ đã VERIFIED → CONFLICT, không ghi đè."""
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "vietlott_ajax", draw="01300")
        self.put(store, job, "pqminh4", draw="01300")
        self.assertEqual(store.reconcile_game("mega645")[0].new_status, "VERIFIED")
        from vlab.models import Observation
        o = Observation.build(source="pqminh4", game="mega645", draw_no="01300", draw_date="2026-10-16",
                              draw_time="18:30:00", main_numbers=MAIN, bonus_number=None,
                              source_url="https://vietlott.vn/x", observed_at="2026-10-16T12:00:00+00:00")
        store.add_observations([o], self.fetch(store, "pqminh4", job))
        self.assertEqual(store.reconcile_game("mega645")[0].new_status, "CONFLICT")

    def test_database_rejects_invalid_numbers(self):
        import psycopg
        store = self.store()
        f = self.fetch(store, "pqminh4", store.new_job_id())
        for main, bonus in (([1, 2, 3, 4, 5, 46], None), ([1, 1, 2, 3, 4, 5], None), ([1, 2, 3, 4, 5, 6], 7)):
            self.assert_raises_sql(psycopg.errors.RaiseException,
                """INSERT INTO draw_observations (source_code, fetch_attempt_id, game, draw_no, draw_date,
                   main_numbers, bonus_number, source_url, observed_at, content_sha256)
                   VALUES ('pqminh4', %s, 'mega645', 99, '2026-01-01', %s, %s, 'u', now(), %s)""",
                (f, main, bonus, "0" * 64))

    def test_tampering_detected_by_hash_chain(self):
        store = self.store()
        store.journal("a", "none", {"x": 1})
        store.journal("b", "none", {"x": 2})
        self.assertEqual(store.verify_chains()["ai_journal"], [])
        with self.conn.cursor() as cur:  # mô phỏng chủ sở hữu tắt trigger và sửa dữ liệu
            cur.execute("ALTER TABLE ai_journal DISABLE TRIGGER append_only_row")
            cur.execute("UPDATE ai_journal SET details = '{\"x\": 999}' WHERE action = 'a'")
            cur.execute("ALTER TABLE ai_journal ENABLE TRIGGER append_only_row")
        self.assertTrue(store.verify_chains()["ai_journal"])

    def test_settings_history_and_export(self):
        store = self.store()
        store.set_setting("tickets.per_draw", 10, "user:paul")
        store.set_setting("tickets.per_draw", 10, "user:paul")  # không đổi → không ghi
        store.set_setting("tickets.per_draw", 12, "user:paul")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM settings_history WHERE key = 'tickets.per_draw'")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute("SELECT value FROM settings_current WHERE key = 'tickets.per_draw'")
            self.assertEqual(cur.fetchone()[0], 12)
        job = store.new_job_id()
        self.put(store, job, "pqminh4", draw="00800")
        self.put(store, job, "nhanaz", draw="00800")
        store.reconcile_game("mega645")
        cols, rows = store.export_rows("draws", "mega645")
        rows = [r for r in rows if r["draw_no"] == 800]
        csv_text = store.to_csv(cols, rows)
        self.assertIn("00800", csv_text)
        self.assertIn("09 20 26 35 36 38", csv_text)
        self.assertIn('"draw_no": "00800"', store.to_json(cols, rows))
        for kind in ("history", "observations", "conflicts"):
            cols, rows = store.export_rows(kind)
            store.to_csv(cols, rows)
            store.to_json(cols, rows)
        cols, rows = store.export_rows("observations", "mega645")
        obs800 = [r for r in rows if r["draw_no"] == 800]
        self.assertEqual({r["source_code"] for r in obs800}, {"pqminh4", "nhanaz"})
        self.assertTrue(all(r["fetch_url"] and r["fetch_finished_at"] and r["raw_sha256"] for r in obs800))

    def test_live_prediction_rules(self):
        import psycopg
        from vlab.games import MEGA645
        from vlab.schedule import next_draw_after
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO methods (code, name_vi, family, description_vi) VALUES ('m0','Ngẫu nhiên','random','') RETURNING id")
            mid = cur.fetchone()[0]
            cur.execute("""INSERT INTO method_versions (method_id, version, code_git_sha, change_kind, change_note_vi,
                           row_hash, prev_hash) VALUES (%s, 1, 'abc', 'initial', 'đầu tiên', '', '') RETURNING id""", (mid,))
            mv = cur.fetchone()[0]
        now = datetime.now(timezone.utc)
        nxt = next_draw_after(MEGA645, now + timedelta(minutes=5))
        ok = """INSERT INTO prediction_runs (method_version_id, game, target_draw_no, purpose, split,
                data_cutoff_draw_no, data_snapshot_sha256, random_seed, target_draw_at, row_hash, prev_hash)
                VALUES (%s,'mega645',%s,'live','live',%s,%s,1,%s,'','')"""
        with self.conn.cursor() as cur:
            cur.execute(ok, (mv, 2000, 1999, "a" * 64, nxt))
        CV, UV = psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation
        self.assert_raises_sql(CV, ok, (mv, 2001, 2001, "a" * 64, nxt))                       # rò rỉ tương lai
        self.assert_raises_sql(CV, ok, (mv, 2002, 2001, "a" * 64, now - timedelta(days=1)))  # sau giờ quay
        self.assert_raises_sql(CV, ok, (mv, 2003, 2002, "a" * 64, nxt + timedelta(hours=1)))  # sai giờ lịch
        self.assert_raises_sql(CV, ok, (mv, 2004, 2003, "a" * 64, nxt + timedelta(days=21)))  # quá xa
        self.assert_raises_sql(UV, ok, (mv, 2000, 1999, "a" * 64, nxt))                       # live hai lần

    def test_ingest_end_to_end_with_fixtures(self):
        """Chạy toàn bộ tác vụ với HTTP giả: nguồn chính thức bị chặn, kho lưu trữ trả về tệp mẫu."""
        from vlab import ingest
        from vlab.sources.http import FetchError, FetchRecord

        class FakeHttp:
            session = None

            def request(self, method, url, **kw):
                now = datetime.now(timezone.utc)
                rec = FetchRecord(url=url, started_at=now, finished_at=now)
                if "vietlott.vn" in url:
                    rec.http_status, rec.error_text = 403, "HTTP 403"
                    raise FetchError("blocked", rec)
                body = (FIX / "nhanaz_mega645.csv").read_bytes() if "NhanAZ" in url and "mega645" in url else b""
                rec.ok, rec.http_status, rec.body, rec.raw_sha256 = True, 200, body, "b" * 64
                return rec

        class FakeClient(ingest.OfficialClient):
            pass

        store = self.store()
        s = ingest.ingest_game(store, FakeHttp(), "mega645", store.new_job_id(),
                               client=FakeClient(FakeHttp()))
        self.assertFalse(s.sources["vietlott_ajax"].reachable)
        self.assertEqual(s.sources["nhanaz"].parsed, 1)
        self.assertFalse(s.all_ok)
        with self.conn.cursor() as cur:
            cur.execute("SELECT status FROM draw_current WHERE game='mega645' AND draw_no = 1")
            self.assertEqual(cur.fetchone()[0], "NOT PROVEN")
            cur.execute("SELECT count(*) FROM fetch_attempts WHERE source_code='vietlott_ajax' AND NOT ok")
            self.assertGreaterEqual(cur.fetchone()[0], 1)

    def test_observation_must_match_fetch_game(self):
        import psycopg
        from vlab.sources.http import FetchRecord
        store = self.store()
        now = datetime.now(timezone.utc)
        fid = store.record_fetch("pqminh4", "power655", FetchRecord(url="https://vietlott.vn/x", started_at=now,
                                 finished_at=now, ok=True, http_status=200, raw_sha256="a" * 64, body=b"x"),
                                 store.new_job_id())
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT s")
            with self.assertRaises(psycopg.errors.RaiseException):
                store.add_observations([self.obs("pqminh4", draw="01400")], fid)
            cur.execute("ROLLBACK TO SAVEPOINT s")

    def test_conflict_rows_need_real_evidence(self):
        import psycopg
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "pqminh4", draw="01500")
        self.put(store, job, "pqminh4", draw="01501")
        store.reconcile_game("mega645")
        with self.conn.cursor() as cur:
            cur.execute("SELECT draw_id FROM draw_current WHERE draw_no = 1500")
            d1500 = cur.fetchone()[0]
            cur.execute("SELECT observation_ids FROM draw_current WHERE draw_no = 1501")
            other_ids = cur.fetchone()[0]
        sql = "INSERT INTO draw_conflicts (draw_id, observation_ids, description_vi) VALUES (%s, %s, 'x')"
        self.assert_raises_sql(psycopg.errors.CheckViolation, sql, (d1500, other_ids))
        self.assert_raises_sql(psycopg.errors.CheckViolation, sql, (d1500, []))

    def test_checkpoint_json_must_be_complete_and_present(self):
        import json
        store = self.store()
        cp = json.loads(json.dumps(store.checkpoint()))
        self.assertEqual(store.verify_checkpoint(cp), [])
        partial = {**cp, "tables": {k: v for k, v in cp["tables"].items() if k != "draw_versions"}}
        self.assertTrue(store.verify_checkpoint(partial))
        self.assertTrue(store.verify_checkpoint({"checkpoint_id": cp["checkpoint_id"], "tables": {}}))
        for junk in ({}, {"checkpoint_id": "x", "tables": cp["tables"]}, {"checkpoint_id": 1, "tables": []},
                     {"checkpoint_id": 1, "tables": {"draws": {"max_id": "a", "n": 1, "sha256": "z"}}},
                     {"checkpoint_id": str(cp["checkpoint_id"]), "tables": cp["tables"]},
                     {"checkpoint_id": 0, "tables": cp["tables"]},
                     {"checkpoint_id": cp["checkpoint_id"],
                      "tables": {k: {**v, "n": str(v["n"])} for k, v in cp["tables"].items()}}):
            self.assertEqual(store.verify_checkpoint(junk)[0][0], "*", junk)
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT s")
            cur.execute("ALTER TABLE chain_checkpoints DISABLE TRIGGER append_only_row")
            cur.execute("DELETE FROM chain_checkpoints WHERE id = %s", (cp["checkpoint_id"],))
            self.assertIn("chain_checkpoints", [t for t, _ in store.verify_checkpoint(cp)])
            cur.execute("ROLLBACK TO SAVEPOINT s")

    def test_repeated_runs_keep_single_pending(self):
        """Hai lần chạy liên tiếp sau một kỳ bị lỡ chỉ tạo MỘT PENDING (kỳ kế tiếp), không tạo thêm."""
        from vlab import ingest
        store = self.store()
        job = store.new_job_id()
        self.put(store, job, "pqminh4", draw="00050", d="2016-09-14")
        self.put(store, job, "nhanaz", draw="00050", d="2016-09-14")

        class NoHttp:
            session = None

            def request(self, method, url, **kw):
                from vlab.sources.http import FetchError, FetchRecord
                now = datetime.now(timezone.utc)
                raise FetchError("offline", FetchRecord(url=url, started_at=now, finished_at=now, error_text="offline"))

        for _ in range(2):
            ingest.ingest_game(store, NoHttp(), "mega645", store.new_job_id(), client=ingest.OfficialClient(NoHttp()))
        with self.conn.cursor() as cur:
            cur.execute("SELECT draw_no FROM draw_current WHERE game='mega645' AND status='PENDING'")
            pend = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT max(draw_no) FROM draw_current WHERE game='mega645' AND main_numbers IS NOT NULL")
            last = cur.fetchone()[0]
        self.assertEqual(pend, [last + 1])

    def test_spoofing_and_schedule_rules(self):
        import psycopg
        from vlab.sources.http import FetchRecord
        store = self.store()
        with self.conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE vlab_app")
        # Thành công mà không có băm dữ liệu thô → bị từ chối.
        now = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT s")
            with self.assertRaises(psycopg.errors.CheckViolation):
                store.record_fetch("pqminh4", "mega645", FetchRecord(url="https://x", started_at=now, finished_at=now,
                                                                     ok=True, http_status=200), store.new_job_id())
            cur.execute("ROLLBACK TO SAVEPOINT s")
            # PENDING không có ngày/giờ lịch → bị từ chối.
            cur.execute("INSERT INTO draws (game, draw_no) VALUES ('mega645', 7003) RETURNING id")
            d7003 = cur.fetchone()[0]
            cur.execute("SAVEPOINT s2")
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute("""INSERT INTO draw_versions (draw_id, version_no, status, reason_vi, created_by, row_hash, prev_hash)
                               VALUES (%s, 1, 'PENDING', 'x', 'job:x', '', '')""", (d7003,))
            cur.execute("ROLLBACK TO SAVEPOINT s2")
            # PENDING vào ngày không có quay (Thứ Hai 19/10/2026) → bị từ chối.
            cur.execute("SAVEPOINT s")
            with self.assertRaises(psycopg.errors.CheckViolation):
                store.add_pending("mega645", 7001, datetime(2026, 10, 19, 11, 0, tzinfo=timezone.utc))
            cur.execute("ROLLBACK TO SAVEPOINT s")
        # Tác vụ tự động không được giả làm người dùng.
        job = store.new_job_id()
        self.put(store, job, "pqminh4", draw="07002")
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM draw_observations WHERE draw_no = 7002")
            oid = cur.fetchone()[0]
            cur.execute("INSERT INTO draws (game, draw_no) VALUES ('mega645', 7002) RETURNING id")
            did = cur.fetchone()[0]
        self.assert_raises_sql(psycopg.errors.InsufficientPrivilege,
            """INSERT INTO draw_versions (draw_id, version_no, draw_date, draw_time, draw_time_source, main_numbers,
               status, observation_ids, reason_vi, created_by, row_hash, prev_hash)
               VALUES (%s, 1, '2026-10-16', '18:00', 'schedule', '{9,20,26,35,36,38}', 'NOT PROVEN', %s, 'x',
                       'user:paul', '', '')""", (did, [oid]))
