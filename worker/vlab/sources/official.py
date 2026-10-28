"""Nguồn chính thức: danh sách kết quả của vietlott.vn qua điểm cuối AjaxPro mà chính trang web dùng.

Điểm cuối và cấu trúc yêu cầu được tham khảo từ dự án mã nguồn mở vietvudanh/vietlott-data (MIT)
và pqminh-4/vietlott-data (MIT); mã ở đây được viết lại độc lập.

Lưu ý thực tế: trang danh sách đôi khi trả về bản lưu cũ, và máy chủ có thể chặn IP ngoài Việt Nam.
Vì vậy kết quả từ đây không bao giờ tự đủ để VERIFIED — luôn đối chiếu với kho lưu trữ.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..games import ValidationError, get_game
from ..models import Observation, dedupe_batch
from urllib.parse import urlparse
from .http import FetchError, FetchRecord, Http

SOURCE_CODE = "vietlott_ajax"
BASE = "https://vietlott.vn"
ENDPOINTS = {
    "mega645": (f"{BASE}/ajaxpro/Vietlott.PlugIn.WebParts.Game645CompareWebPart,Vietlott.PlugIn.WebParts.ashx",
                "8290fce2", 6, "winning-number-645"),
    "power655": (f"{BASE}/ajaxpro/Vietlott.PlugIn.WebParts.Game655CompareWebPart,Vietlott.PlugIn.WebParts.ashx",
                 "23bbd667", 5, "winning-number-655"),
}
DETAIL_URL = {
    "mega645": BASE + "/vi/trung-thuong/ket-qua-trung-thuong/645?id={draw_no}&nocatche=1",
    "power655": BASE + "/vi/trung-thuong/ket-qua-trung-thuong/655?id={draw_no}&nocatche=1",
}
_COOKIE_RE = re.compile(r'document\.cookie\s*=\s*["\']([^"\']+)')


def _render_info() -> dict:
    return {
        "SiteId": "main.frontend.vi", "SiteAlias": "main.vi", "UserSessionId": "", "SiteLang": "vi",
        "IsPageDesign": False, "ExtraParam1": "", "ExtraParam2": "", "ExtraParam3": "", "SiteURL": "",
        "WebPage": None, "SiteName": "Vietlott", "OrgPageAlias": None, "PageAlias": None,
        "RefKey": None, "FullPageAlias": None, "System": 1,
    }


def build_list_body(game: str, page_index: int) -> dict:
    _, key, rows, _ = ENDPOINTS[game]
    return {
        "ORenderInfo": _render_info(), "Key": key, "GameDrawId": "",
        "ArrayNumbers": [["" for _ in range(18)] for _ in range(rows)],
        "CheckMulti": False, "PageIndex": page_index,
    }


class OfficialClient:
    def __init__(self, http: Http):
        self.http = http
        self._cookie_ready = False

    def _bootstrap_cookie(self) -> FetchRecord | None:
        """Trang AjaxPro đặt cookie bằng JavaScript; nếu có thì sao chép lại cookie đó."""
        if self._cookie_ready:
            return None
        rec = self.http.request("GET", f"{BASE}/ajaxpro/")
        match = _COOKIE_RE.search(rec.body.decode("utf-8", "replace"))
        if match:
            pair = match.group(1).split(";", 1)[0]
            if "=" in pair:
                name, value = pair.split("=", 1)
                self.http.session.cookies.set(name.strip(), value.strip(), domain=".vietlott.vn", path="/")
        self._cookie_ready = True
        return rec

    def fetch_list(self, game: str, page_index: int = 0) -> tuple[list[FetchRecord], list[Observation], list[str]]:
        """Trả về (các lần truy cập, quan sát hợp lệ, các dòng bị loại kèm lý do)."""
        records: list[FetchRecord] = []
        try:
            boot = self._bootstrap_cookie()
            if boot:
                records.append(boot)
        except FetchError as exc:
            records.append(exc.record)  # vẫn thử gọi danh sách
        url, _, _, page = ENDPOINTS[game]
        body = json.dumps(build_list_body(game, page_index), ensure_ascii=False, separators=(",", ":"))
        try:
            rec = self.http.request("POST", url, data=body.encode("utf-8"), headers={
                "Content-Type": "text/plain; charset=utf-8",
                "X-AjaxPro-Method": "ServerSideDrawResult",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE,
                "Referer": f"{BASE}/vi/trung-thuong/ket-qua-trung-thuong/{page}",
            })
        except FetchError as exc:
            records.append(exc.record)
            return records, [], []
        records.append(rec)
        try:
            html = extract_html_content(rec.body)
        except ValueError as exc:
            rec.ok = False
            rec.error_text = str(exc)
            return records, [], []
        try:
            if not tds_ok(html):
                raise ValueError("HtmlContent không có bảng kết quả")
            obs, rejected = parse_list_html(game, html, source_url=url, observed_at=rec.finished_at.isoformat())
        except Exception as exc:
            rec.ok = False
            rec.error_text = f"Không phân tích được trang chính thức: {type(exc).__name__}: {exc}"
            return records, [], []
        return records, obs, rejected


def extract_html_content(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Phản hồi AjaxPro không phải JSON (có thể bị chặn hoặc trang lỗi)") from exc
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError(f"AjaxPro báo lỗi: {str(payload)[:200]}")
    value = payload.get("value")
    if not isinstance(value, dict) or value.get("Error"):
        raise ValueError("AjaxPro trả về kết quả lỗi hoặc rỗng")
    html = value.get("HtmlContent")
    if not isinstance(html, str) or "<" not in html:
        raise ValueError("AjaxPro không có HtmlContent")
    return html


def parse_list_html(game: str, html: str, *, source_url: str, observed_at: str) -> tuple[list[Observation], list[str]]:
    g = get_game(game)
    soup = BeautifulSoup(html, "lxml")
    observations: list[Observation] = []
    rejected: list[str] = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue  # dòng tiêu đề
        date_text = tds[0].get_text(" ", strip=True)
        raw_id = tds[1].get_text(" ", strip=True)
        m = re.fullmatch(r"#?([0-9]{1,5})", raw_id)  # cùng quy tắc với normalize_draw_no: chỉ chữ số ASCII
        id_text = m.group(1) if m else ""
        nums = [int(s.get_text(strip=True)) for s in tds[2].find_all("span") if s.get_text(strip=True).isdigit()]
        try:
            d = datetime.strptime(date_text, "%d/%m/%Y").date()
            expected = g.main_count + (1 if g.has_bonus else 0)
            if len(nums) != expected:
                raise ValidationError(f"cần {expected} số, nhận {len(nums)}: {nums}")
            main, bonus = nums[: g.main_count], (nums[g.main_count] if g.has_bonus else None)
            observations.append(Observation.build(
                source=SOURCE_CODE, game=g.code, draw_no=id_text, draw_date=d,
                main_numbers=main, bonus_number=bonus,
                source_url=DETAIL_URL[g.code].format(draw_no=id_text.zfill(5)) if id_text else source_url,
                observed_at=observed_at,
            ))
        except (ValueError, ValidationError) as exc:
            rejected.append(f"kỳ {id_text or raw_id!r} ({date_text}): {exc}")
    kept, dup = dedupe_batch(observations)
    return kept, rejected + dup



# Official result PDFs are hosted on the publisher's static media domain. The AjaxPro
# endpoint may return HTTP 403 from cloud regions, while the result PDFs are public.
# The URL is strictly constrained because it originates in a third-party archive record.
_OFFICIAL_PDF_PATH = re.compile(
    r"^/main/\\d{1,2}\\.\\d{4}/system/game\\.pdf\\.result/"
    r"(\\d{5})_(mega|power)_a[45]_(\\d{5})\\.pdf$"
)
_OFFICIAL_PDF_NUMBERS = re.compile(
    r"(?<!\\d)(\\d{2})\\s+(\\d{2})\\s+(\\d{2})\\s+"
    r"(\\d{2})\\s+(\\d{2})\\s+(\\d{2})(?:\\s*\\|\\s*(\\d{2}))?"
)
_OFFICIAL_PDF_DRAW = re.compile(
    r"K\\s*ỳ\\s*quay\\s*thưởng\\s*:\\s*(\\d{5})\\s*\\|\\s*"
    r"Ngày\\s*quay\\s*thưởng\\s*:\\s*(\\d{2}/\\d{2}/\\d{4})",
    re.IGNORECASE,
)


def validate_official_pdf_url(game: str, draw_no: str, url: str) -> bool:
    """Allow only a real official result PDF for the requested game/draw."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    match = _OFFICIAL_PDF_PATH.fullmatch(parsed.path)
    if (parsed.scheme != "https" or parsed.hostname != "media.vietlott.vn"
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not match):
        return False
    expected_product = "mega" if game == "mega645" else "power" if game == "power655" else None
    return bool(expected_product and match.group(1) == str(draw_no).zfill(5)
                and match.group(2) == expected_product and match.group(3) == str(draw_no).zfill(5))


def parse_official_pdf_text(game: str, draw_no: str, pdf_url: str, text: str,
                            observed_at: str) -> Observation:
    """Parse the current draw's result section from extracted text of an official Vietlott PDF."""
    if not validate_official_pdf_url(game, draw_no, pdf_url):
        raise ValueError("URL PDF chính thức không khớp trò chơi/kỳ quay")
    header = _OFFICIAL_PDF_DRAW.search(text)
    if not header:
        raise ValueError("PDF chính thức không có số kỳ và ngày quay hợp lệ")
    pdf_draw_no, date_text = header.groups()
    if pdf_draw_no != str(draw_no).zfill(5):
        raise ValueError(f"PDF kỳ {pdf_draw_no} không khớp kỳ yêu cầu {draw_no}")
    # The current result is the first six-number group after the current draw header.
    # Historical results further down are hyphen-separated and therefore do not match.
    result = _OFFICIAL_PDF_NUMBERS.search(text[header.end():header.end() + 1200])
    if not result:
        raise ValueError("PDF chính thức không có chuỗi 6 số kết quả")
    vals = [int(v) for v in result.groups() if v is not None]
    g = get_game(game)
    main, bonus = tuple(vals[:6]), vals[6] if len(vals) == 7 else None
    if g.has_bonus and bonus is None:
        raise ValueError("PDF Power 6/55 không có số đặc biệt")
    if not g.has_bonus and bonus is not None:
        raise ValueError("PDF Mega 6/45 không được có số đặc biệt")
    draw_date = datetime.strptime(date_text, "%d/%m/%Y").date()
    return Observation.build(
        source=SOURCE_CODE, game=game, draw_no=pdf_draw_no, draw_date=draw_date,
        main_numbers=main, bonus_number=bonus, source_url=pdf_url,
        observed_at=observed_at, evidence_urls=(pdf_url,),
        extra={"official_transport": "static_result_pdf"},
    )


def parse_official_pdf(game: str, draw_no: str, pdf_url: str, raw: bytes,
                       observed_at: str) -> Observation:
    """Read a small official PDF and validate its draw id, date, main numbers, and special number."""
    if not raw.startswith(b"%PDF-"):
        raise ValueError("Phản hồi nguồn PDF không bắt đầu bằng chữ ký PDF")
    try:
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw), strict=True)
        if not reader.pages or len(reader.pages) > 5:
            raise ValueError("PDF có số trang không hợp lệ")
        text = "\\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ValueError(f"Không đọc được PDF chính thức: {type(exc).__name__}: {exc}") from exc
    if not text.strip():
        raise ValueError("PDF chính thức không có văn bản có thể kiểm tra")
    return parse_official_pdf_text(game, draw_no, pdf_url, text, observed_at)


def tds_ok(html: str) -> bool:
    return "<table" in html.lower()
