import unittest
from pathlib import Path

from vlab.sources.archives import parse_nhanaz, parse_pqminh, parse_vietvudanh
from vlab.sources.official import build_list_body, extract_html_content, parse_list_html

FIX = Path(__file__).parent / "fixtures"
AT = "2026-10-16T12:00:00+00:00"


class ArchiveParsers(unittest.TestCase):
    def test_pqminh(self):
        obs, rejected = parse_pqminh("power655", (FIX / "pqminh_power655.jsonl").read_text(), "https://raw.githubusercontent.com/x", AT)
        self.assertEqual([o.draw_no for o in obs], ["00001", "00002"])
        self.assertEqual(obs[0].main_numbers, (5, 10, 14, 23, 24, 38))
        self.assertEqual(obs[0].bonus_number, 35)
        self.assertIn("655?id=00001", obs[0].source_url)
        self.assertEqual(len(rejected), 2)  # thiếu số đặc biệt; số 56 ngoài khoảng

    def test_pqminh_wrong_game_rejected(self):
        obs, rejected = parse_pqminh("mega645", (FIX / "pqminh_power655.jsonl").read_text(), "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(obs, [])
        self.assertEqual(len(rejected), 4)

    def test_vietvudanh_mega_rejects_seventh_number(self):
        obs, rejected = parse_vietvudanh("mega645", (FIX / "vietvudanh_power645.jsonl").read_text(), "https://raw.githubusercontent.com/x", AT)
        self.assertEqual([o.draw_no for o in obs], ["00198", "00199"])
        self.assertTrue(all(o.bonus_number is None for o in obs))
        self.assertEqual(len(rejected), 1)
        self.assertIn("cần 6 số", rejected[0])

    def test_nhanaz(self):
        obs, rejected = parse_nhanaz("mega645", (FIX / "nhanaz_mega645.csv").read_text(), "https://raw.githubusercontent.com/x", AT)
        self.assertEqual([o.draw_no for o in obs], ["00001"])
        self.assertEqual(obs[0].extra["data_source"], "official_vietlott")
        self.assertEqual(len(rejected), 2)  # tự đánh dấu invalid; Mega có số đặc biệt


class OfficialParser(unittest.TestCase):
    def test_list_body_shapes(self):
        self.assertEqual(len(build_list_body("mega645", 0)["ArrayNumbers"]), 6)
        self.assertEqual(len(build_list_body("power655", 2)["ArrayNumbers"]), 5)
        self.assertEqual(build_list_body("power655", 2)["PageIndex"], 2)

    def test_parse_list(self):
        html = extract_html_content((FIX / "official_655.json").read_bytes())
        obs, rejected = parse_list_html("power655", html, source_url="https://vietlott.vn/ajax", observed_at=AT)
        self.assertEqual([o.draw_no for o in obs], ["01411", "01410"])
        self.assertEqual(obs[0].main_numbers, (7, 23, 25, 42, 45, 51))
        self.assertEqual(obs[0].bonus_number, 4)
        self.assertEqual(obs[1].bonus_number, 26)
        self.assertEqual(str(obs[0].draw_date), "2026-10-15")
        self.assertIn("655?id=01411", obs[0].source_url)
        self.assertEqual(len(rejected), 1)  # dòng thiếu số đặc biệt

    def test_blocked_page_is_error_not_data(self):
        for raw in (b"<html>Access denied</html>", b'{"error":{"Message":"x"}}', b'{"value":{"Error":true}}',
                    b'{"value":{"HtmlContent":""}}'):
            with self.assertRaises(ValueError, msg=raw):
                extract_html_content(raw)


class ArchiveHardening(unittest.TestCase):
    def test_bom_stripped(self):
        from vlab.sources.archives import decode
        raw = "\ufeff".encode() + (FIX / "nhanaz_mega645.csv").read_bytes()
        obs, _ = parse_nhanaz("mega645", decode(raw), "https://raw.githubusercontent.com/x", AT)
        self.assertEqual([o.draw_no for o in obs], ["00001"])

    def test_bad_encoding_fails_whole_file(self):
        from vlab.sources.archives import decode
        with self.assertRaises(UnicodeDecodeError):
            decode(b"\xff\xfe\x00bad")

    def test_pqminh_evidence_and_time(self):
        obs, _ = parse_pqminh("power655", (FIX / "pqminh_power655.jsonl").read_text(), "https://raw.githubusercontent.com/x", AT)
        self.assertTrue(obs[0].evidence_urls[0].endswith(".pdf"))
        self.assertEqual(obs[0].draw_time.isoformat(), "18:00:00")

    def test_float_and_bool_numbers_rejected(self):
        line = '{"date":"2017-10-25","id":"00198","result":[12.7,17,23,25,34,38]}\n' \
               '{"date":"2017-10-27","id":"00199","result":[true,10,13,21,22,38]}\n' \
               '{"date":"2017-10-29","id":"00200","result":"1,2,3,4,5,6"}\n'
        obs, rejected = parse_vietvudanh("mega645", line, "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(obs, [])
        self.assertEqual(len(rejected), 3)

    def test_garbage_row_does_not_crash(self):
        text = '[1,2,3]\nnull\n"x"\n' + (FIX / "vietvudanh_power645.jsonl").read_text()
        obs, rejected = parse_vietvudanh("mega645", text, "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(len(obs), 2)
        self.assertEqual(len(rejected), 4)

    def test_same_draw_twice_with_different_numbers_dropped(self):
        text = ('{"date":"2017-10-25","id":"00198","result":[12,17,23,25,34,38]}\n'
                '{"date":"2017-10-25","id":"00198","result":[12,17,23,25,34,39]}\n'
                '{"date":"2017-10-27","id":"00199","result":[4,10,13,21,22,38]}\n'
                '{"date":"2017-10-27","id":"00199","result":[4,10,13,21,22,38]}\n')
        obs, rejected = parse_vietvudanh("mega645", text, "https://raw.githubusercontent.com/x", AT)
        self.assertEqual([o.draw_no for o in obs], ["00199"])  # bản trùng y hệt giữ một lần
        self.assertEqual(len(rejected), 1)
        self.assertIn("00198", rejected[0])


class OfficialHardening(unittest.TestCase):
    def html(self, rows):
        body = "".join(f"<tr><td>{d}</td><td>{i}</td><td>{''.join(f'<span>{n}</span>' for n in nums)}</td></tr>"
                       for d, i, nums in rows)
        return f"<table><tr><th>Ngày</th><th>Kỳ</th><th>Bộ số</th></tr>{body}</table>"

    def test_ambiguous_draw_id_rejected_not_guessed(self):
        html = self.html([("16/10/2026", "1O576", [9, 20, 26, 35, 36, 38]),
                          ("16/10/2026", "#01576", [9, 20, 26, 35, 36, 38])])
        obs, rejected = parse_list_html("mega645", html, source_url="https://vietlott.vn/a", observed_at=AT)
        self.assertEqual([o.draw_no for o in obs], ["01576"])
        self.assertEqual(len(rejected), 1)
        self.assertIn("1O576", rejected[0])

    def test_same_draw_two_results_dropped(self):
        html = self.html([("16/10/2026", "01576", [9, 20, 26, 35, 36, 38]),
                          ("16/10/2026", "01576", [9, 20, 26, 35, 36, 39])])
        obs, rejected = parse_list_html("mega645", html, source_url="https://vietlott.vn/a", observed_at=AT)
        self.assertEqual(obs, [])
        self.assertEqual(len(rejected), 1)

    def test_bad_date_rejected(self):
        html = self.html([("31/02/2026", "01576", [9, 20, 26, 35, 36, 38])])
        obs, rejected = parse_list_html("mega645", html, source_url="https://vietlott.vn/a", observed_at=AT)
        self.assertEqual(obs, [])
        self.assertEqual(len(rejected), 1)


class ModelStrictness(unittest.TestCase):
    def test_garbage_after_date_rejected(self):
        from vlab.games import ValidationError
        from vlab.models import Observation
        kw = dict(source="s", game="mega645", draw_no="1", main_numbers=[1, 2, 3, 4, 5, 6], bonus_number=None,
                  source_url="https://x", observed_at=AT)
        for bad in ("2026-10-16garbage", "16/10/2026", "2026-13-01", ""):
            with self.assertRaises(ValidationError, msg=bad):
                Observation.build(draw_date=bad, **kw)
        self.assertEqual(str(Observation.build(draw_date="2026-10-16T18:00:00+07:00", **kw).draw_date), "2026-10-16")
        with self.assertRaises(ValidationError):
            Observation.build(draw_date="2026-10-16", draw_time="25:00", **kw)


class TypeAndTimeStrictness(unittest.TestCase):
    def test_bonus_must_be_list(self):
        base = '{"draw_date":"2017-08-01","draw_id":"00001","game":"power655","result":{"main_numbers":[5,10,14,23,24,38],"bonus_numbers":%s}}'
        for bad in ('"7"', '{"a":7}', '7', 'true'):
            obs, rejected = parse_pqminh("power655", base % bad, "https://raw.githubusercontent.com/x", AT)
            self.assertEqual(obs, [], bad)
            self.assertEqual(len(rejected), 1, bad)
        obs, _ = parse_pqminh("power655", base % "[7]", "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(obs[0].bonus_number, 7)

    def test_nhanaz_special_must_be_list(self):
        head = (FIX / "nhanaz_mega645.csv").read_text().splitlines()[0]
        row = 'mega645,00009,2016-08-07,confirmed,"{""numbers"":[3,5,17,22,30,31],""special_numbers"":""""}","{}","[]",https://vietlott.vn/x,complete,valid,[],x'
        obs, rejected = parse_nhanaz("mega645", head + "\n" + row + "\n", "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(obs, [])  # chuỗi (kể cả rỗng) không phải danh sách → loại
        ok_row = row.replace('""special_numbers"":""""', '""special_numbers"":[]')
        obs, _ = parse_nhanaz("mega645", head + "\n" + ok_row + "\n", "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(len(obs), 1)
        row2 = row.replace('""special_numbers"":""""', '""special_numbers"":""7""')
        obs, rejected = parse_nhanaz("mega645", head + "\n" + row2 + "\n", "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(obs, [])

    def test_dedupe_considers_draw_time(self):
        line = '{"draw_date":"2017-08-01","draw_id":"00001","draw_time":"%s","game":"power655","result":{"main_numbers":[5,10,14,23,24,38],"bonus_numbers":[35]}}'
        obs, rejected = parse_pqminh("power655", line % "18:00:00" + "\n" + line % "18:30:00", "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(obs, [])
        self.assertEqual(len(rejected), 1)

    def test_timezone_aware_time_rejected(self):
        line = '{"draw_date":"2017-08-01","draw_id":"00001","draw_time":"18:00:00+07:00","game":"power655","result":{"main_numbers":[5,10,14,23,24,38],"bonus_numbers":[35]}}'
        obs, rejected = parse_pqminh("power655", line, "https://raw.githubusercontent.com/x", AT)
        self.assertEqual(obs, [])
        self.assertIn("múi giờ", rejected[0])
