import unittest

from vlab.ingest import GameSummary, SourceResult, run, summary_vi
from vlab.store import Store


class IngestUnit(unittest.TestCase):
    def test_official_pages_bounds(self):
        for bad in (0, -1, 21):
            with self.assertRaises(ValueError):
                run(object(), object(), official_pages=bad)

    def test_audit_must_be_autocommit(self):
        class C:
            autocommit = False
        with self.assertRaises(ValueError):
            Store(object(), C())
        class A:
            autocommit = True
        Store(object(), A())

    def test_all_ok_requires_every_source_parsed(self):
        s = GameSummary("mega645", sources={"vietlott_ajax": SourceResult(True, 0), "pqminh4": SourceResult(True, 5)})
        self.assertFalse(s.all_ok)
        s.sources["vietlott_ajax"].parsed = 3
        self.assertTrue(s.all_ok)
        self.assertFalse(GameSummary("mega645").all_ok)

    def test_official_pdf_fallback_recovers_only_the_official_transport_failure(self):
        s = GameSummary("mega645", sources={
            "vietlott_ajax": SourceResult(reachable=True, parsed=1, pages_failed=1, fallback_used=True),
            "pqminh4": SourceResult(reachable=True, parsed=10),
            "nhanaz": SourceResult(reachable=True, parsed=9),
        })
        self.assertTrue(s.all_ok)
        s.sources["pqminh4"].pages_failed = 1
        self.assertFalse(s.all_ok)
        s.sources["pqminh4"].pages_failed = 0
        s.sources["vietlott_ajax"].fallback_used = False
        self.assertFalse(s.all_ok)

    def test_summary_is_vietnamese_and_flags_stale_official(self):
        s = GameSummary("power655", sources={"vietlott_ajax": SourceResult(True, 8, 0)},
                        official_latest=1405, archive_latest=1411, status_counts={"VERIFIED": 10})
        text = summary_vi([s])
        self.assertIn("Power 6/55", text)
        self.assertIn("dữ liệu cũ", text)
        self.assertIn("01405", text)


class FakeClient:
    """Trang 0 và trang 1 chồng lên nhau; kỳ 01576 có hai kết quả khác nhau giữa hai trang."""
    def __init__(self, pages):
        self.pages = pages

    def fetch_list(self, game, page):
        from datetime import datetime, timezone
        from vlab.sources.http import FetchRecord
        now = datetime.now(timezone.utc)
        rec = FetchRecord(url="https://vietlott.vn/ajax", started_at=now, finished_at=now, ok=True,
                          http_status=200, raw_sha256="a" * 64, body=b"x")
        return [rec], self.pages[page], []


class FakeStore:
    def __init__(self):
        self.added = []
        self.n = 0

    def record_fetch(self, *a, **k):
        self.n += 1
        return self.n

    def add_observations(self, obs, fid):
        self.added.append((fid, [o.draw_no for o in obs]))
        return len(obs)


class OfficialPDFFallback(unittest.TestCase):
    def test_existing_official_observation_confirms_pdf_fallback_without_refetch(self):
        from datetime import date
        from vlab.ingest import _official_pdf_fallback
        from vlab.models import Observation

        pdf = "https://media.vietlott.vn/main/10.2026/system/game.pdf.result/01580_mega_a5_01580.pdf"
        archive_obs = Observation.build(
            source="pqminh4", game="mega645", draw_no="01580", draw_date=date(2026, 10, 25),
            main_numbers=[4, 13, 22, 25, 40, 41], bonus_number=None,
            source_url="https://raw.githubusercontent.com/pqminh-4/vietlott-data/main/data/canonical/mega645.jsonl",
            evidence_urls=[pdf], observed_at=AT,
        )
        official_obs = Observation.build(
            source="vietlott_ajax", game="mega645", draw_no="01580", draw_date=date(2026, 10, 25),
            main_numbers=[4, 13, 22, 25, 40, 41], bonus_number=None,
            source_url=pdf, evidence_urls=[pdf], observed_at=AT,
        )

        class S:
            def latest_observations(self, game):
                self.assert_game = game
                return {1580: [(1, archive_obs), (2, official_obs)]}

        class NeverFetch:
            def request(self, *args, **kwargs):
                raise AssertionError("stored official evidence should avoid repeated PDF download")

        summary = GameSummary("mega645")
        _official_pdf_fallback(S(), NeverFetch(), "mega645", 7, summary)
        result = summary.sources["vietlott_ajax"]
        self.assertTrue(result.reachable)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.parsed, 1)
        self.assertIn("bản ghi chính thức", result.note)


class OfficialAcrossPages(unittest.TestCase):
    def test_conflict_across_pages_dropped_and_duplicates_written_once(self):
        from vlab.ingest import _official
        from vlab.models import Observation

        def o(no, last):
            return Observation.build(source="vietlott_ajax", game="mega645", draw_no=no, draw_date="2026-10-16",
                                     main_numbers=[9, 20, 26, 35, 36, last], bonus_number=None,
                                     source_url="https://vietlott.vn/x", observed_at="2026-10-16T12:00:00+00:00")
        pages = [[o("01576", 38), o("01575", 40)], [o("01575", 40), o("01576", 39), o("01574", 41)]]
        st, s = FakeStore(), GameSummary("mega645")
        _official(st, FakeClient(pages), "mega645", 1, 2, s)
        written = [n for _, ns in st.added for n in ns]
        self.assertEqual(sorted(written), ["01574", "01575"])
        self.assertEqual(s.sources["vietlott_ajax"].rejected, 1)
        self.assertEqual(s.official_latest, 1575)


class StrictInputs(unittest.TestCase):
    def test_draw_no_ascii_only(self):
        from vlab.games import ValidationError, normalize_draw_no
        for bad in ("##1", "١٢", "1 2", True, 1.0, None, "000000"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                normalize_draw_no(bad)
        self.assertEqual(normalize_draw_no(1576), "01576")
        self.assertEqual(normalize_draw_no("#1576"), "01576")

    def test_datetime_not_accepted_as_date(self):
        from datetime import datetime, timezone
        from vlab.games import ValidationError
        from vlab.models import Observation
        with self.assertRaises(ValidationError):
            Observation.build(source="s", game="mega645", draw_no="1", draw_date=datetime(2026, 10, 16, tzinfo=timezone.utc),
                              main_numbers=[1, 2, 3, 4, 5, 6], bonus_number=None, source_url="https://x", observed_at="x")

    def test_naive_datetimes_rejected(self):
        from datetime import date, datetime
        from vlab.games import MEGA645
        from vlab.schedule import next_draw_after, scheduled_after
        with self.assertRaises(ValueError):
            next_draw_after(MEGA645, datetime(2026, 10, 16, 18, 0))
        with self.assertRaises(ValueError):
            scheduled_after(MEGA645, date(2026, 10, 14), datetime(2026, 10, 16, 20, 0))


class FailingPageClient(FakeClient):
    def fetch_list(self, game, page):
        recs, obs, rej = super().fetch_list(game, page)
        if page == 1:
            recs[0].ok, recs[0].http_status, recs[0].error_text = False, 403, "HTTP 403"
            return recs, [], []
        return recs, obs, rej


class PartialAndStrict(unittest.TestCase):
    def obs(self, no):
        from vlab.models import Observation
        return Observation.build(source="vietlott_ajax", game="mega645", draw_no=no, draw_date="2026-10-16",
                                 main_numbers=[9, 20, 26, 35, 36, 38], bonus_number=None,
                                 source_url="https://vietlott.vn/x", observed_at="2026-10-16T12:00:00+00:00")

    def test_one_failed_page_makes_run_partial(self):
        from vlab.ingest import _official
        s = GameSummary("mega645")
        _official(FakeStore(), FailingPageClient([[self.obs("01576")], []]), "mega645", 1, 2, s)
        r = s.sources["vietlott_ajax"]
        self.assertEqual((r.parsed, r.pages_failed), (1, 1))
        self.assertFalse(s.all_ok)

    def test_bad_inputs_rejected(self):
        from vlab.games import ValidationError
        from vlab.models import Observation
        base = dict(source="s", game="mega645", draw_no="1", draw_date="2026-10-16", main_numbers=[1, 2, 3, 4, 5, 6],
                    bonus_number=None, source_url="https://vietlott.vn/x", observed_at="2026-10-16T12:00:00+00:00")
        bad = [{"draw_date": 20261016}, {"draw_date": True}, {"source_url": "x"}, {"source_url": "ftp://x/y"},
               {"source_url": "javascript:alert(1)"}, {"source_url": "//x"}, {"source_url": "https://a b/"},
               {"evidence_urls": ["http://media.vietlott.vn/a.pdf"]}, {"observed_at": "x"},
               {"observed_at": "2026-10-16T12:00:00"}]
        for change in bad:
            with self.assertRaises(ValidationError, msg=change):
                Observation.build(**{**base, **change})
        Observation.build(**base)

    def test_finish_job_failure_returns_4_and_unlocks(self):
        import vlab.ingest as ing

        class S:
            unlocked = False

            def __init__(self, *a):
                pass

            def new_job_id(self):
                return 1

            def try_lock(self, name):
                return True

            def unlock(self, name):
                S.unlocked = True

            def journal(self, *a, **k):
                pass

            def report(self, *a, **k):
                pass

            def checkpoint(self):
                return {"checkpoint_id": 7}

            def finish_job(self, *a, **k):
                raise RuntimeError("mất kết nối")

        class Conn:
            def commit(self):
                pass

            def rollback(self):
                pass

        orig_store, orig_game = ing.Store, ing.ingest_game
        ing.Store, ing.ingest_game = S, lambda *a, **k: GameSummary("mega645")
        try:
            self.assertEqual(ing.run(Conn(), Conn(), games=["mega645"], http=object()), 4)
            self.assertTrue(S.unlocked)
        finally:
            ing.Store, ing.ingest_game = orig_store, orig_game


class OfficialFailureAccounting(unittest.TestCase):
    def test_bootstrap_failure_counts_and_exception_is_contained(self):
        from datetime import datetime, timezone
        from vlab.ingest import _official
        from vlab.sources.http import FetchRecord

        class C:
            def fetch_list(self, game, page):
                if page == 1:
                    raise AttributeError("phản hồi sai kiểu")
                now = datetime.now(timezone.utc)
                boot = FetchRecord(url="https://vietlott.vn/ajaxpro/", started_at=now, finished_at=now,
                                   http_status=503, error_text="HTTP 503")
                ok = FetchRecord(url="https://vietlott.vn/ajax", started_at=now, finished_at=now, ok=True,
                                 http_status=200, raw_sha256="a" * 64, body=b"x")
                return [boot, ok], [PartialAndStrict().obs("01576")], []

        st, s = FakeStore(), GameSummary("mega645")
        _official(st, C(), "mega645", 1, 2, s)
        r = s.sources["vietlott_ajax"]
        self.assertEqual(r.parsed, 1)
        self.assertEqual(r.pages_failed, 2)  # cookie thất bại + trang 1 lỗi bất ngờ
        self.assertEqual(st.n, 3)            # cả ba lần truy cập đều được ghi
        self.assertFalse(s.all_ok)

    def test_password_only_userinfo_rejected(self):
        from vlab.models import _valid_url
        for bad in ("https://:secret@vietlott.vn/x", "https://@vietlott.vn/x", "https://u@vietlott.vn/x"):
            self.assertFalse(_valid_url(bad), bad)
        self.assertTrue(_valid_url("https://vietlott.vn/x?id=1"))

    def test_official_id_ascii_only(self):
        from vlab.sources.official import parse_list_html
        html = ("<table><tr><td>16/10/2026</td><td>١٥٧٦</td><td>" + "".join(f"<span>{n}</span>" for n in (9, 20, 26, 35, 36, 38))
                + "</td></tr><tr><td>16/10/2026</td><td># 01576</td><td>"
                + "".join(f"<span>{n}</span>" for n in (9, 20, 26, 35, 36, 38)) + "</td></tr></table>")
        obs, rejected = parse_list_html("mega645", html, source_url="https://vietlott.vn/a",
                                        observed_at="2026-10-16T12:00:00+00:00")
        self.assertEqual(obs, [])
        self.assertEqual(len(rejected), 2)


class Round7(unittest.TestCase):
    def test_bad_ports_rejected(self):
        from vlab.models import _valid_url
        for bad in ("https://vietlott.vn:0/x", "https://vietlott.vn:65536/x", "https://vietlott.vn:abc/x",
                    "https://vietlott.vn:99999/x"):
            self.assertFalse(_valid_url(bad), bad)
        self.assertTrue(_valid_url("https://vietlott.vn:443/x"))

    def test_evidence_field_types(self):
        from vlab.sources.archives import parse_nhanaz, parse_pqminh
        line = ('{"draw_date":"2017-08-01","draw_id":"00001","game":"power655","source_pdf_url":%s,'
                '"result":{"main_numbers":[5,10,14,23,24,38],"bonus_numbers":[35]}}')
        for bad in ('{"a":1}', '5', 'true', '"ftp://x/a.pdf"', '"http://media.vietlott.vn/a.pdf"'):
            obs, rejected = parse_pqminh("power655", line % bad, "https://raw.githubusercontent.com/x", "2026-10-16T12:00:00+00:00")
            self.assertEqual(obs, [], bad)
            self.assertEqual(len(rejected), 1, bad)
        obs, _ = parse_pqminh("power655", line % "null", "https://raw.githubusercontent.com/x", "2026-10-16T12:00:00+00:00")
        self.assertEqual(obs[0].evidence_urls, ())

    def test_retry_after_non_finite(self):
        from vlab.sources.http import _retry_after
        for v in ("nan", "NaN", "inf", "+inf", "-inf"):
            self.assertEqual(_retry_after(v), 60.0, v)

    def test_observed_at_normalized_to_utc(self):
        from vlab.models import Observation
        o = Observation.build(source="s", game="mega645", draw_no="1", draw_date="2026-10-16",
                              main_numbers=[1, 2, 3, 4, 5, 6], bonus_number=None, source_url="https://vietlott.vn/x",
                              observed_at="2026-10-16T19:00:00+07:00")
        self.assertEqual(o.observed_at, "2026-10-16T12:00:00+00:00")

    def test_empty_records_counted_as_failure(self):
        from vlab.ingest import _official

        class C:
            def fetch_list(self, game, page):
                return [], [], []
        s = GameSummary("mega645")
        _official(FakeStore(), C(), "mega645", 1, 1, s)
        self.assertEqual(s.sources["vietlott_ajax"].pages_failed, 1)
        self.assertFalse(s.all_ok)
