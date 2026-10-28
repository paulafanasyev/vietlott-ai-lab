import unittest

import requests

from vlab.sources.http import MAX_RETRY_AFTER, FetchError, Http, _retry_after, is_allowed


class FakeResp:
    def __init__(self, status, body=b"ok", headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.closed = False

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        self.closed = True


class FakeSession(requests.Session):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        assert kw["allow_redirects"] is False
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def http(s, **kw):
    return Http(s, sleep=lambda _: None, min_interval=0, **kw)


class HttpTest(unittest.TestCase):
    def test_allowlist(self):
        self.assertTrue(is_allowed("https://vietlott.vn/ajaxpro/"))
        self.assertTrue(is_allowed("https://media.vietlott.vn/main/10.2026/system/game.pdf.result/01580_mega_a5_01580.pdf"))
        self.assertTrue(is_allowed("https://raw.githubusercontent.com/a/b"))
        for bad in ("http://vietlott.vn/", "https://vietlott.vn.evil.com/", "https://evil.com/",
                    "https://user@vietlott.vn/", "https://u:p@vietlott.vn/", "https://vietlott.vn:8443/",
                    "https://vietlott.vn:99999/"):
            self.assertFalse(is_allowed(bad), bad)

    def test_retries_then_success_and_log(self):
        s = FakeSession([requests.ConnectionError("x"), FakeResp(503), FakeResp(200, b"data")])
        rec = http(s).request("GET", "https://vietlott.vn/x")
        self.assertTrue(rec.ok)
        self.assertEqual(rec.body, b"data")
        self.assertEqual(len(s.calls), 3)
        self.assertEqual(len(rec.raw_sha256), 64)
        self.assertEqual([a.get("status") for a in rec.attempts], [None, 503, 200])
        self.assertIn("ConnectionError", rec.attempts[0]["error"])

    def test_no_retry_on_403_and_error_recorded(self):
        s = FakeSession([FakeResp(403)])
        with self.assertRaises(FetchError) as ctx:
            http(s).request("GET", "https://vietlott.vn/x")
        self.assertEqual(len(s.calls), 1)
        rec = ctx.exception.record
        self.assertFalse(rec.ok)
        self.assertIn("403", rec.error_text)
        self.assertIsNotNone(rec.finished_at)
        self.assertIsNone(rec.size)

    def test_redirect_inside_allowlist_followed_and_checked(self):
        s = FakeSession([FakeResp(302, headers={"Location": "/y"}), FakeResp(200, b"ok")])
        rec = http(s).request("GET", "https://vietlott.vn/x")
        self.assertEqual(rec.final_url, "https://vietlott.vn/y")
        self.assertEqual([c[1] for c in s.calls], ["https://vietlott.vn/x", "https://vietlott.vn/y"])

    def test_redirect_outside_allowlist_never_requested(self):
        s = FakeSession([FakeResp(301, headers={"Location": "https://evil.com/"})])
        with self.assertRaises(FetchError) as ctx:
            http(s).request("GET", "https://vietlott.vn/x")
        self.assertEqual(len(s.calls), 1)  # không có yêu cầu nào tới evil.com, không thử lại
        self.assertIn("evil.com", ctx.exception.record.error_text)

    def test_redirect_loop_limited(self):
        s = FakeSession([FakeResp(302, headers={"Location": "/x"}) for _ in range(6)])
        with self.assertRaises(FetchError):
            http(s, retries=0).request("GET", "https://vietlott.vn/x")
        self.assertEqual(len(s.calls), 6)

    def test_post_303_becomes_get_without_body(self):
        s = FakeSession([FakeResp(303, headers={"Location": "/r"}), FakeResp(200)])
        http(s).request("POST", "https://vietlott.vn/x", data=b"{}")
        self.assertEqual(s.calls[1][0], "GET")
        self.assertNotIn("data", s.calls[1][2])

    def test_size_limit(self):
        s = FakeSession([FakeResp(200, b"x" * 100)])
        with self.assertRaises(FetchError) as ctx:
            http(s, max_bytes=50).request("GET", "https://vietlott.vn/x")
        self.assertEqual(len(s.calls), 1)
        self.assertIn("giới hạn", ctx.exception.record.error_text)
        s = FakeSession([FakeResp(200, b"x", headers={"Content-Length": "999"})])
        with self.assertRaises(FetchError):
            http(s, max_bytes=50).request("GET", "https://vietlott.vn/x")

    def test_retry_after_capped(self):
        self.assertEqual(_retry_after("100000"), MAX_RETRY_AFTER)
        self.assertEqual(_retry_after("5"), 5.0)
        self.assertEqual(_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), 60.0)
        self.assertEqual(_retry_after("-3"), 0.0)

    def test_disallowed_url_never_requested(self):
        s = FakeSession([])
        with self.assertRaises(FetchError):
            http(s).request("GET", "https://evil.com/")
        self.assertEqual(s.calls, [])
