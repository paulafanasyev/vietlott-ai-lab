import random
import unittest

from vlab.backtest import goal_odds, match_probs, run_backtest
from vlab.methods import CODES, State, all_tickets


def fake_draws(n, max_number, seed=1):
    rng = random.Random(seed)
    return [(i + 1, tuple(sorted(rng.sample(range(1, max_number + 1), 6)))) for i in range(n)]


class Odds(unittest.TestCase):
    def test_known_odds(self):
        mega = goal_odds(45)
        self.assertEqual(mega["6"]["one_in"], 8145060)
        self.assertEqual(mega["5"]["one_in"], 34808)
        self.assertEqual(mega["3"]["one_in"], 45)
        self.assertEqual(goal_odds(55)["6"]["one_in"], 28989675)
        self.assertAlmostEqual(sum(match_probs(45).values()), 1.0, places=12)


class Methods(unittest.TestCase):
    def test_tickets_valid_and_deterministic(self):
        st = State(45)
        for _, d in fake_draws(120, 45):
            st.add(d)
        a, b = all_tickets(st, "x|121"), all_tickets(st, "x|121")
        self.assertEqual(a, b)
        self.assertEqual(set(a), set(CODES))
        for t in a.values():
            self.assertEqual(len(set(t)), 6)
            self.assertTrue(all(1 <= x <= 45 for x in t))

    def test_window_counts_consistent(self):
        st = State(55, window=10)
        draws = fake_draws(40, 55)
        for _, d in draws:
            st.add(d)
        expect = [0] * 56
        for _, d in draws[-10:]:
            for x in d:
                expect[x] += 1
        self.assertEqual(st.recent, expect)


class Backtest(unittest.TestCase):
    def test_no_lookahead(self):
        """Đổi kết quả kỳ t không được làm đổi vé dự đoán cho kỳ t."""
        draws = fake_draws(130, 45)
        st = State(45)
        for _, d in draws[:120]:
            st.add(d)
        before = all_tickets(st, "g|121")
        draws2 = draws[:120] + [(121, (1, 2, 3, 4, 5, 6))]
        st2 = State(45)
        for _, d in draws2[:120]:
            st2.add(d)
        self.assertEqual(before, all_tickets(st2, "g|121"))

    def test_random_data_not_significant(self):
        res = run_backtest("mega645", 45, fake_draws(1500, 45, seed=7))
        self.assertEqual(res["methods"][0]["code"], "M0")
        for m in res["methods"]:
            self.assertEqual(m["draws"], 1400)
            self.assertLess(abs(m["mean_hits"] - 0.8), 0.15)
        better = [m for m in res["methods"] if m["verdict_vi"].startswith("TỐT HƠN")]
        self.assertEqual(better, [])  # dữ liệu ngẫu nhiên: không phương pháp nào được \"thắng\"
        self.assertEqual(len(res["next_tickets"]), len(CODES))
        self.assertAlmostEqual(sum(res["learn_weights"].values()), 1.0, places=3)

    def test_detects_real_bias(self):
        """Nếu dữ liệu thiên lệch thật (số 1–6 hay về), M1 phải phát hiện được — kiểm định có độ nhạy."""
        rng = random.Random(3)
        draws = []
        for i in range(1500):
            s = set(rng.sample(range(1, 7), 3))
            while len(s) < 6:
                s.add(rng.randint(1, 45))
            draws.append((i + 1, tuple(sorted(s))))
        res = run_backtest("mega645", 45, draws)
        m1 = next(m for m in res["methods"] if m["code"] == "M1")
        self.assertTrue(m1["verdict_vi"].startswith("TỐT HƠN"), m1)
        # Tự học phải dồn trọng số vào phương pháp cơ sở thật sự tốt hơn và bản thân cũng tốt hơn ngẫu nhiên.
        m8 = next(m for m in res["methods"] if m["code"] == "M8")
        self.assertTrue(m8["verdict_vi"].startswith("TỐT HƠN"), m8)
        w = res["learn_weights"]
        self.assertGreater(w["M1"] + w["M2"] + w["M5"], 0.9, w)
        m9 = next(m for m in res["methods"] if m["code"] == "M9")  # học máy cũng phải học được độ lệch thật
        self.assertTrue(m9["verdict_vi"].startswith("TỐT HƠN"), m9)

    def test_new_methods_rules(self):
        st = State(45)
        for _, d in fake_draws(200, 45):
            st.add(d)
        tk = all_tickets(st, "x|201")
        self.assertGreaterEqual(sum(1 for x in tk["M11"] if x > 31), 4)
        others = {x for c, t in tk.items() if c != "M12" for x in t}
        self.assertGreaterEqual(len(set(tk["M12"]) - others), min(6, 45 - len(others)))
        t10 = tk["M10"]
        self.assertTrue(2 <= sum(x % 2 for x in t10) <= 4)
