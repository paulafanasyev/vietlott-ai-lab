import unittest

from vlab.games import MEGA645, POWER655, ValidationError, normalize_draw_no, validate_numbers


class GamesTest(unittest.TestCase):
    def test_mega_valid_sorted_no_bonus(self):
        self.assertEqual(validate_numbers(MEGA645, [38, 9, 20, 26, 35, 36], None), ((9, 20, 26, 35, 36, 38), None))

    def test_mega_rejects_bonus(self):
        with self.assertRaises(ValidationError):
            validate_numbers(MEGA645, [1, 2, 3, 4, 5, 6], 7)

    def test_mega_rejects_out_of_range_and_duplicates(self):
        for bad in ([0, 1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 46], [1, 1, 2, 3, 4, 5], [1, 2, 3, 4, 5]):
            with self.assertRaises(ValidationError, msg=bad):
                validate_numbers(MEGA645, bad, None)

    def test_power_requires_distinct_bonus(self):
        self.assertEqual(validate_numbers(POWER655, [7, 23, 25, 42, 45, 51], 4)[1], 4)
        with self.assertRaises(ValidationError):
            validate_numbers(POWER655, [7, 23, 25, 42, 45, 51], None)
        with self.assertRaises(ValidationError):
            validate_numbers(POWER655, [7, 23, 25, 42, 45, 51], 51)
        with self.assertRaises(ValidationError):
            validate_numbers(POWER655, [7, 23, 25, 42, 45, 56], 4)

    def test_rejects_bools(self):
        with self.assertRaises(ValidationError):
            validate_numbers(MEGA645, [True, 2, 3, 4, 5, 6], None)

    def test_draw_no(self):
        self.assertEqual(normalize_draw_no("1576"), "01576")
        self.assertEqual(normalize_draw_no(" #00012 "), "00012")
        for bad in ("", "0", "abc", "123456", "-1"):
            with self.assertRaises(ValidationError, msg=bad):
                normalize_draw_no(bad)
