"""HTTP thận trọng: chỉ tên miền được phép, tự xử lý chuyển hướng, giới hạn kích thước, thử lại có giới hạn,
ghi lại từng lần thử."""

from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

ALLOWED_HOSTS = frozenset({
    "vietlott.vn", "www.vietlott.vn", "media.vietlott.vn",
    "raw.githubusercontent.com",
})
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
MAX_BYTES = 25 * 1024 * 1024
MAX_RETRY_AFTER = 300.0
USER_AGENT = "VietlottAILab/0.1 (research, low-rate)"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FetchRecord:
    """Một lần truy cập nguồn — được lưu vào fetch_attempts dù thành công hay thất bại."""
    url: str
    started_at: datetime
    finished_at: datetime | None = None
    http_status: int | None = None
    ok: bool = False
    error_text: str | None = None
    raw_sha256: str | None = None
    final_url: str | None = None
    attempts: list[dict] = field(default_factory=list)
    body: bytes = field(default=b"", repr=False)

    @property
    def size(self) -> int | None:
        return len(self.body) if (self.ok or self.body) else None


class FetchError(RuntimeError):
    def __init__(self, message: str, record: FetchRecord):
        super().__init__(message)
        self.record = record


class _Fatal(Exception):
    """Lỗi không nên thử lại (vi phạm an toàn, quá lớn)."""


def is_allowed(url: str) -> bool:
    p = urlparse(url)
    try:
        port = p.port
    except ValueError:
        return False
    return (p.scheme == "https" and p.hostname in ALLOWED_HOSTS and port in (None, 443)
            and not p.username and not p.password)


def _retry_after(value: str | None) -> float:
    """Giây chờ theo Retry-After; giá trị hỏng/NaN/vô cực → 60 giây; luôn trong khoảng 0..300."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 60.0
    if not math.isfinite(v):
        return 60.0
    return max(0.0, min(v, MAX_RETRY_AFTER))


def _read_limited(resp, limit: int) -> bytes:
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _Fatal(f"Phản hồi quá lớn: {declared} byte (giới hạn {limit})")
    chunks, total = [], 0
    for chunk in resp.iter_content(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise _Fatal(f"Phản hồi vượt giới hạn {limit} byte")
        chunks.append(chunk)
    return b"".join(chunks)


class Http:
    def __init__(self, session: requests.Session | None = None, *, timeout: float = 30.0,
                 retries: int = 3, min_interval: float = 2.0, max_bytes: int = MAX_BYTES, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self.max_bytes = max_bytes
        self._sleep = sleep
        self._next_at = 0.0

    def _throttle(self) -> None:
        wait = self._next_at - time.monotonic()
        if wait > 0:
            self._sleep(wait)

    def _one(self, method: str, url: str, kwargs: dict, log: list) -> tuple[int, bytes, str]:
        """Một lần thử, tự đi theo chuyển hướng (mỗi bước đều kiểm tra danh sách cho phép)."""
        current, m, kw = url, method, dict(kwargs)
        for hop in range(MAX_REDIRECTS + 1):
            self._throttle()
            entry = {"url": current, "method": m, "at": utc_now().isoformat()}
            log.append(entry)
            try:
                resp = self.session.request(m, current, timeout=self.timeout, allow_redirects=False,
                                            stream=True, **kw)
            finally:
                self._next_at = time.monotonic() + self.min_interval
            try:
                entry["status"] = resp.status_code
                if resp.status_code in REDIRECT_STATUSES:
                    target = urljoin(current, resp.headers.get("Location", ""))
                    entry["redirect"] = target
                    if not is_allowed(target):
                        raise _Fatal(f"Bị chuyển hướng ra ngoài danh sách cho phép: {target}")
                    if resp.status_code == 303 or (resp.status_code in (301, 302) and m == "POST"):
                        m, kw = "GET", {k: v for k, v in kw.items() if k not in ("data", "json")}
                    current = target
                    continue
                if resp.status_code == 429:
                    self._next_at = time.monotonic() + _retry_after(resp.headers.get("Retry-After"))
                body = _read_limited(resp, self.max_bytes) if 200 <= resp.status_code < 300 else b""
                entry["bytes"] = len(body)
                return resp.status_code, body, current
            finally:
                close = getattr(resp, "close", None)
                if close:
                    close()
        raise _Fatal(f"Quá {MAX_REDIRECTS} lần chuyển hướng")

    def request(self, method: str, url: str, **kwargs) -> FetchRecord:
        rec = FetchRecord(url=url, started_at=utc_now())
        if not is_allowed(url):
            rec.finished_at = utc_now()
            rec.error_text = f"URL không nằm trong danh sách cho phép: {url}"
            raise FetchError(rec.error_text, rec)
        last_error = "không rõ"
        for attempt in range(self.retries + 1):
            try:
                status, body, final = self._one(method, url, kwargs, rec.attempts)
            except _Fatal as exc:
                last_error = str(exc)
                rec.attempts[-1]["error"] = last_error
                break
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                rec.attempts[-1]["error"] = last_error[:300]
            else:
                rec.http_status = status
                if 200 <= status < 300:
                    rec.body, rec.final_url, rec.ok = body, final, True
                    rec.raw_sha256 = hashlib.sha256(body).hexdigest()
                    rec.finished_at = utc_now()
                    return rec
                last_error = f"HTTP {status}"
                if status not in RETRY_STATUSES:
                    break
            if attempt < self.retries:
                self._sleep(1.5 * (2 ** attempt) + random.uniform(0, 0.5))
        rec.finished_at = utc_now()
        rec.error_text = last_error
        raise FetchError(f"Không lấy được {url}: {last_error}", rec)
