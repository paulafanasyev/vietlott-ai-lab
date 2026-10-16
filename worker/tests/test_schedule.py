import unittest
from datetime import date, datetime, timezone

from vlab.games import MEGA645, POWER655
from vlab.schedule import VN_TZ, next_draw_after, scheduled_after


class ScheduleTest(unittest.TestCase):
    def test_next_mega_after_friday_draw(self):
        # Thứ Sáu 16/10/2026 18:00 → kỳ tiếp theo Chủ Nhật 18/10/2026 18:00 (giờ VN)
        friday = datetime(2026, 10, 16, 18, 0, tzinfo=VN_TZ)
        self.assertEqual(next_draw_after(MEGA645, friday), datetime(2026, 10, 18, 18, 0, tzinfo=VN_TZ))

    def test_next_power_from_utc(self):
        # 16/10/2026 23:00 UTC = 17/10 06:00 VN, Thứ Bảy → kỳ Power 17/10 18:00
        moment = datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(next_draw_after(POWER655, moment), datetime(2026, 10, 17, 18, 0, tzinfo=VN_TZ))

    def test_scheduled_after_respects_publish_grace(self):
        last = date(2026, 10, 14)  # Thứ Tư, kỳ Mega
        before = datetime(2026, 10, 16, 19, 30, tzinfo=VN_TZ)
        after = datetime(2026, 10, 16, 20, 30, tzinfo=VN_TZ)
        self.assertEqual(scheduled_after(MEGA645, last, before), [])
        self.assertEqual(scheduled_after(MEGA645, last, after), [datetime(2026, 10, 16, 18, 0, tzinfo=VN_TZ)])
