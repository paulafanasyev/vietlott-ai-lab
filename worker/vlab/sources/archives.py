"""Kho lưu trữ công khai trên GitHub (MIT). Mỗi kho có định dạng riêng; mọi dòng đều được kiểm tra."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Callable

from ..games import ValidationError, get_game
from ..models import Observation, dedupe_batch


def decode(raw: bytes) -> str:
    """UTF-8, bỏ BOM nếu có. Byte sai mã hóa làm hỏng cả tệp (không đoán)."""
    return raw.decode("utf-8-sig")


def _finish(out: list[Observation], rejected: list[str]) -> tuple[list[Observation], list[str]]:
    kept, dup = dedupe_batch(out)
    return kept, rejected + dup


def _pdf_list(value) -> list[str]:
    """None = không có bằng chứng; một chuỗi = một URL (định dạng pqminh); danh sách = nhiều URL (NhanAZ).
    Mọi kiểu khác là dữ liệu hỏng → loại dòng kèm lý do. Từng URL được Observation.build kiểm tra."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return list(value)
    raise ValidationError(f"Trường bằng chứng PDF sai kiểu: {value!r}")


@dataclass(frozen=True)
class Archive:
    code: str
    urls: dict[str, str]
    parser: Callable[[str, str, str, str], tuple[list[Observation], list[str]]]


def _row_error(rejected: list[str], line_no: int, exc: Exception) -> None:
    rejected.append(f"dòng {line_no}: {exc}")


def parse_pqminh(game: str, text: str, url: str, observed_at: str) -> tuple[list[Observation], list[str]]:
    g = get_game(game)
    out, rejected = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get("game") != g.code:
                raise ValidationError(f"sai trò chơi {row.get('game')!r}")
            res = row["result"]
            if not isinstance(res, dict):
                raise ValidationError(f"result phải là đối tượng: {res!r}")
            bonus_list = res.get("bonus_numbers", [])
            if bonus_list is None:
                bonus_list = []
            if not isinstance(bonus_list, list):
                raise ValidationError(f"bonus_numbers phải là danh sách: {bonus_list!r}")
            if g.has_bonus and len(bonus_list) != 1:
                raise ValidationError(f"cần đúng 1 số đặc biệt, nhận {bonus_list}")
            if not g.has_bonus and bonus_list:
                raise ValidationError(f"Mega 6/45 không có số đặc biệt, nhận {bonus_list}")
            out.append(Observation.build(
                source="pqminh4", game=g.code, draw_no=row["draw_id"], draw_date=row["draw_date"],
                main_numbers=res["main_numbers"], bonus_number=bonus_list[0] if bonus_list else None,
                source_url=row.get("source_url") or url, observed_at=observed_at,
                draw_time=row.get("draw_time"), evidence_urls=_pdf_list(row.get("source_pdf_url")),
                extra={"prizes": row.get("prizes")},
            ))
        except Exception as exc:  # một dòng hỏng không được làm hỏng cả tệp
            _row_error(rejected, i, exc)
    return _finish(out, rejected)


def parse_vietvudanh(game: str, text: str, url: str, observed_at: str) -> tuple[list[Observation], list[str]]:
    g = get_game(game)
    out, rejected = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            nums = row["result"]
            if not isinstance(nums, list):
                raise ValidationError(f"result phải là danh sách: {nums!r}")
            expected = g.main_count + (1 if g.has_bonus else 0)
            if len(nums) != expected:
                # Không đoán số nào là "thừa": loại cả dòng và ghi lý do.
                raise ValidationError(f"cần {expected} số, nhận {len(nums)}: {nums}")
            out.append(Observation.build(
                source="vietvudanh", game=g.code, draw_no=row["id"], draw_date=row["date"],
                main_numbers=nums[: g.main_count], bonus_number=nums[g.main_count] if g.has_bonus else None,
                source_url=url, observed_at=observed_at,
            ))
        except Exception as exc:  # một dòng hỏng không được làm hỏng cả tệp
            _row_error(rejected, i, exc)
    return _finish(out, rejected)


def parse_nhanaz(game: str, text: str, url: str, observed_at: str) -> tuple[list[Observation], list[str]]:
    g = get_game(game)
    out, rejected = [], []
    reader = csv.DictReader(io.StringIO(text))
    for i, row in enumerate(reader, 2):
        try:
            if row.get("product") != g.code:
                raise ValidationError(f"sai trò chơi {row.get('product')!r}")
            if row.get("validation_status") != "valid":
                raise ValidationError(f"nguồn tự đánh dấu validation_status={row.get('validation_status')!r}")
            res = json.loads(row["result_json"])
            if not isinstance(res, dict):
                raise ValidationError(f"result_json phải là đối tượng: {res!r}")
            special = res.get("special_numbers", [])
            if special is None:
                special = []
            if not isinstance(special, list):
                raise ValidationError(f"special_numbers phải là danh sách: {special!r}")
            if g.has_bonus and len(special) != 1:
                raise ValidationError(f"cần đúng 1 số đặc biệt, nhận {special}")
            if not g.has_bonus and special:
                raise ValidationError(f"Mega 6/45 không có số đặc biệt, nhận {special}")
            attrs = json.loads(row.get("attributes_json") or "{}")
            if not isinstance(attrs, dict):
                attrs = {}
            pdfs = json.loads(row.get("official_pdf_urls_json") or "[]")
            out.append(Observation.build(
                source="nhanaz", game=g.code, draw_no=row["draw_id"], draw_date=row["draw_date"],
                main_numbers=res["numbers"], bonus_number=special[0] if special else None,
                source_url=row.get("source_url") or url, observed_at=observed_at,
                evidence_urls=_pdf_list(pdfs),
                extra={"data_source": attrs.get("data_source"), "draw_status": row.get("draw_status")},
            ))
        except Exception as exc:  # một dòng hỏng không được làm hỏng cả tệp
            _row_error(rejected, i, exc)
    return _finish(out, rejected)


RAW = "https://raw.githubusercontent.com"
ARCHIVES: dict[str, Archive] = {
    "pqminh4": Archive("pqminh4", {
        "mega645": f"{RAW}/pqminh-4/vietlott-data/main/data/canonical/mega645.jsonl",
        "power655": f"{RAW}/pqminh-4/vietlott-data/main/data/canonical/power655.jsonl",
    }, parse_pqminh),
    "vietvudanh": Archive("vietvudanh", {
        "mega645": f"{RAW}/vietvudanh/vietlott-data/main/data/power645.jsonl",
        "power655": f"{RAW}/vietvudanh/vietlott-data/main/data/power655.jsonl",
    }, parse_vietvudanh),
    "nhanaz": Archive("nhanaz", {
        "mega645": f"{RAW}/NhanAZ-Data/vietlott-research/main/datasets/draws/mega645/all.csv",
        "power655": f"{RAW}/NhanAZ-Data/vietlott-research/main/datasets/draws/power655/all.csv",
    }, parse_nhanaz),
}
