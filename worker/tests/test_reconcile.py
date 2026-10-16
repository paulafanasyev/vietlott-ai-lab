import unittest

from vlab.models import Observation
from datetime import time

from vlab.reconcile import CONFLICT, CROSS_CHECKED, NOT_PROVEN, PENDING, VERIFIED, SourceInfo, decide

KINDS = {"vietlott_ajax": SourceInfo("official", "vietlott"), "pqminh4": SourceInfo("archive", "crawl:pqminh4"),
         "vietvudanh": SourceInfo("archive", "crawl:vietvudanh"), "nhanaz": SourceInfo("archive", "crawl:nhanaz"),
         "mirror": SourceInfo("archive", "crawl:pqminh4")}


def obs(source, main=(9, 20, 26, 35, 36, 38), d="2026-10-16", draw="01576", game="mega645", bonus=None, t=None):
    return Observation.build(source=source, game=game, draw_no=draw, draw_date=d, main_numbers=main, draw_time=t,
                             bonus_number=bonus, source_url="https://x", observed_at="2026-10-16T12:00:00+00:00")


class ReconcileTest(unittest.TestCase):
    def test_pending_when_empty(self):
        self.assertEqual(decide([], KINDS).status, PENDING)

    def test_single_source_not_proven(self):
        self.assertEqual(decide([obs("pqminh4")], KINDS).status, NOT_PROVEN)
        self.assertEqual(decide([obs("vietlott_ajax")], KINDS).status, NOT_PROVEN)

    def test_two_archives_cross_checked(self):
        self.assertEqual(decide([obs("pqminh4"), obs("nhanaz")], KINDS).status, CROSS_CHECKED)

    def test_official_plus_archive_verified(self):
        d = decide([obs("vietlott_ajax"), obs("pqminh4")], KINDS)
        self.assertEqual(d.status, VERIFIED)
        self.assertEqual(d.observation.source, "vietlott_ajax")

    def test_number_mismatch_conflict(self):
        d = decide([obs("vietlott_ajax"), obs("pqminh4", main=(9, 20, 26, 35, 36, 39))], KINDS)
        self.assertEqual(d.status, CONFLICT)
        self.assertIsNone(d.observation)
        self.assertIn("39", d.reason_vi)

    def test_date_mismatch_conflict(self):
        self.assertEqual(decide([obs("pqminh4"), obs("nhanaz", d="2026-10-15")], KINDS).status, CONFLICT)

    def test_power_bonus_mismatch_conflict(self):
        a = obs("pqminh4", game="power655", main=(7, 23, 25, 42, 45, 51), bonus=4, draw="01411", d="2026-10-15")
        b = obs("nhanaz", game="power655", main=(7, 23, 25, 42, 45, 51), bonus=5, draw="01411", d="2026-10-15")
        self.assertEqual(decide([a, b], KINDS).status, CONFLICT)

    def test_same_source_twice_counts_once(self):
        self.assertEqual(decide([obs("pqminh4"), obs("pqminh4")], KINDS).status, NOT_PROVEN)

    def test_mixed_draws_rejected(self):
        with self.assertRaises(ValueError):
            decide([obs("pqminh4"), obs("nhanaz", draw="01575")], KINDS)

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            decide([obs("random_blog")], KINDS)

    def test_same_provenance_group_counts_once(self):
        # Bản sao của pqminh4 không phải xác nhận độc lập.
        self.assertEqual(decide([obs("pqminh4"), obs("mirror")], KINDS).status, NOT_PROVEN)
        self.assertEqual(decide([obs("vietlott_ajax"), obs("mirror")], KINDS).status, VERIFIED)

    def test_draw_time_from_source_or_schedule(self):
        d = decide([obs("pqminh4", t="18:00:00"), obs("nhanaz")], KINDS, scheduled_time=time(18, 0))
        self.assertEqual((d.draw_time, d.draw_time_source), (time(18, 0), "source"))
        d = decide([obs("pqminh4"), obs("nhanaz")], KINDS, scheduled_time=time(18, 0))
        self.assertEqual((d.draw_time, d.draw_time_source), (time(18, 0), "schedule"))

    def test_draw_time_disagreement_is_conflict(self):
        d = decide([obs("pqminh4", t="18:00:00"), obs("nhanaz", t="18:30:00")], KINDS)
        self.assertEqual(d.status, CONFLICT)
