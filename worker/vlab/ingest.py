"""Tác vụ thu thập: nguồn chính thức + kho lưu trữ → quan sát → đối chiếu → phiên bản kết quả."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from .games import GAMES, get_game
from .reconcile import CONFLICT
from .schedule import scheduled_after
from .sources.archives import ARCHIVES, decode
from .sources.http import FetchError, FetchRecord, Http, utc_now
from .sources.official import (BASE as OFFICIAL_BASE, SOURCE_CODE as OFFICIAL, OfficialClient,
                               parse_official_pdf, validate_official_pdf_url)
from .store import Store

LOCK = "ingest"


@dataclass
class SourceResult:
    reachable: bool = False   # HTTP thành công
    parsed: int = 0           # số dòng hợp lệ
    new: int = 0              # số quan sát mới được ghi
    rejected: int = 0
    pages_failed: int = 0     # số trang/tệp không lấy hoặc không phân tích được
    note: str = ""
    fallback_used: bool = False  # chính thức qua PDF khi AjaxPro bị chặn


@dataclass
class GameSummary:
    game: str
    sources: dict[str, SourceResult] = field(default_factory=dict)
    official_latest: int | None = None
    archive_latest: int | None = None
    changes: int = 0
    conflicts: int = 0
    pending_added: list[int] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)

    @property
    def all_ok(self) -> bool:
        if not self.sources:
            return False
        return all(
            r.reachable and r.parsed > 0
            and (r.pages_failed == 0 or (code == OFFICIAL and r.fallback_used))
            for code, r in self.sources.items()
        )


def _official(store: Store, client: OfficialClient, game: str, job_id: int, pages: int, s: GameSummary) -> None:
    """Đọc mọi trang trước, khử trùng lặp TRÊN TOÀN BỘ các trang (cùng kỳ khác kết quả → loại cả nhóm), rồi ghi."""
    from .models import dedupe_batch
    r = s.sources.setdefault(OFFICIAL, SourceResult())
    collected: list[tuple[int, object]] = []
    for page in range(pages):
        try:
            records, obs, rejected = client.fetch_list(game, page)
        except Exception as exc:  # lỗi bất ngờ của một trang không được xóa công việc của các nguồn khác
            now = utc_now()
            rec = FetchRecord(url=f"{OFFICIAL_BASE}/ajaxpro/", started_at=now, finished_at=now,
                              error_text=f"Trang {page}: lỗi không lường trước: {type(exc).__name__}: {exc}"[:500])
            store.record_fetch(OFFICIAL, game, rec, job_id)
            r.pages_failed += 1
            r.note = rec.error_text[:200]
            continue
        if not records:
            r.pages_failed += 1
            r.note = f"Trang {page}: không có lần truy cập nào được trả về"
            continue
        for i, rec in enumerate(records):
            last = i == len(records) - 1
            fid = store.record_fetch(OFFICIAL, game, rec, job_id, parsed=len(obs) if last else 0,
                                     rejected=rejected if last else None)
            if not rec.ok:
                # Mọi lần truy cập thất bại (kể cả bước lấy cookie) đều làm lần chạy thành 'partial'.
                r.pages_failed += 1
                r.note = (rec.error_text or "")[:200]
            if last:
                r.reachable = r.reachable or (rec.http_status is not None and 200 <= rec.http_status < 300)
                if rec.ok:
                    collected.extend((fid, o) for o in obs)
        r.rejected += len(rejected)
    kept, dup = dedupe_batch([o for _, o in collected])
    if dup:
        r.rejected += len(dup)
        r.note = (r.note + "; " if r.note else "") + "; ".join(dup)[:200]
    # dedupe_batch giữ bản xuất hiện đầu tiên của mỗi kỳ (bản trùng y hệt ở trang khác bị bỏ).
    keep = {id(o) for o in kept}
    by_fid: dict[int, list] = {}
    for fid, o in collected:
        if id(o) in keep:
            by_fid.setdefault(fid, []).append(o)
    for fid, obs in by_fid.items():
        r.parsed += len(obs)
        r.new += store.add_observations(obs, fid)
    if kept:
        s.official_latest = max(int(o.draw_no) for o in kept)


def _archive(store: Store, http: Http, code: str, game: str, job_id: int, s: GameSummary) -> None:
    archive = ARCHIVES[code]
    r = s.sources.setdefault(code, SourceResult())
    url = archive.urls[game]
    try:
        rec = http.request("GET", url)
    except FetchError as exc:
        store.record_fetch(code, game, exc.record, job_id)
        r.note = (exc.record.error_text or "")[:200]
        r.pages_failed += 1
        return
    r.reachable = True
    try:
        obs, rejected = archive.parser(game, decode(rec.body), rec.final_url or url, rec.finished_at.isoformat())
    except Exception as exc:  # tệp hỏng toàn bộ (sai mã hóa…): ghi lại, không ghi quan sát
        rec.ok, rec.error_text = False, f"Không phân tích được dữ liệu: {type(exc).__name__}: {exc}"
        store.record_fetch(code, game, rec, job_id)
        r.note = rec.error_text[:200]
        r.pages_failed += 1
        return
    fid = store.record_fetch(code, game, rec, job_id, parsed=len(obs), rejected=rejected)
    r.parsed, r.rejected = len(obs), len(rejected)
    r.new = store.add_observations(obs, fid)
    if obs:
        s.archive_latest = max(s.archive_latest or 0, max(int(o.draw_no) for o in obs))


def _official_pdf_fallback(store: Store, http: Http, game: str, job_id: int, s: GameSummary) -> None:
    """Use the archive's official-PDF evidence link when the live official Ajax endpoint is blocked.

    The PDF host, path, product and draw number are validated before any request. The parsed
    official result is stored under the already-registered official source identity, so reconciliation
    can elevate a matching archive result to VERIFIED without weakening history or status rules.
    """
    r = s.sources.setdefault(OFFICIAL, SourceResult())
    latest = store.latest_observations(game)
    candidates = []
    for draw_no, pairs in latest.items():
        for oid, obs in pairs:
            if obs.source == "pqminh4":
                candidates.append((draw_no, oid, obs, pairs))
    if not candidates:
        return
    draw_no, _, archive_obs, pairs = max(candidates, key=lambda row: row[0])
    pdf_urls = [u for u in archive_obs.evidence_urls
                if validate_official_pdf_url(game, f"{draw_no:05d}", u)]
    if not pdf_urls:
        return

    # Fetch the current official PDF on every run. This keeps a successful official-source
    # attempt in the audit log even when the resulting observation is identical to an older one.
    failures = []
    for pdf_url in pdf_urls[:2]:
        try:
            rec = http.request(
                "GET", pdf_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
                    "Referer": "https://www.vietlott.vn/",
                },
            )
        except FetchError as exc:
            store.record_fetch(OFFICIAL, game, exc.record, job_id)
            r.pages_failed += 1
            failures.append(exc.record.error_text or "không lấy được PDF")
            continue
        try:
            obs = parse_official_pdf(game, f"{draw_no:05d}", pdf_url, rec.body,
                                     rec.finished_at.isoformat() if rec.finished_at else utc_now().isoformat())
        except Exception as exc:
            rec.ok = False
            rec.error_text = f"PDF chính thức không hợp lệ: {type(exc).__name__}: {exc}"[:500]
            store.record_fetch(OFFICIAL, game, rec, job_id, parsed=0,
                               rejected=[f"PDF kỳ {draw_no:05d}: {type(exc).__name__}: {exc}"])
            r.pages_failed += 1
            failures.append(str(exc)[:180])
            continue
        fid = store.record_fetch(OFFICIAL, game, rec, job_id, parsed=1)
        r.new += store.add_observations([obs], fid)
        r.reachable = True
        r.parsed += 1
        r.fallback_used = True
        if obs.result_signature() != archive_obs.result_signature():
            r.note = (f"AjaxPro hiện trả HTTP 403; PDF chính thức kỳ {draw_no:05d} được đọc nhưng "
                      "khác dữ liệu kho lưu trữ — đã chuyển sang đối chiếu CONFLICT.")
        else:
            r.note = (f"AjaxPro hiện trả HTTP 403; PDF kết quả chính thức kỳ {draw_no:05d} "
                      "đã được tải và kiểm tra thành công.")
        return
    if failures:
        r.note = ("AjaxPro HTTP 403; PDF chính thức chưa đọc được: " + "; ".join(failures))[:300]


def ingest_game(store: Store, http: Http, game: str, job_id: int, *, official_pages: int = 1,
                client: OfficialClient | None = None) -> GameSummary:
    g = get_game(game)
    s = GameSummary(game)
    _official(store, client or OfficialClient(http), game, job_id, official_pages, s)
    for code in ARCHIVES:
        _archive(store, http, code, game, job_id, s)
    _official_pdf_fallback(store, http, game, job_id, s)

    changes = store.reconcile_game(game)
    s.changes = len(changes)
    s.conflicts = sum(1 for c in changes if c.new_status == CONFLICT)

    # Chỉ kỳ KẾ TIẾP kỳ cuối đã biết mới được đánh dấu PENDING: ngày nghỉ lễ không làm lệch số kỳ.
    current = store.current_versions(game)
    known = {n: v for n, v in current.items() if v.main_numbers is not None}
    if known:
        last_no = max(known)
        due = scheduled_after(g, known[last_no].draw_date, utc_now())
        if due and store.add_pending(game, last_no + 1, due[0]):
            s.pending_added.append(last_no + 1)
    for v in store.current_versions(game).values():
        s.status_counts[v.status] = s.status_counts.get(v.status, 0) + 1
    return s


def summary_vi(summaries: list[GameSummary]) -> str:
    lines = ["# Báo cáo thu thập dữ liệu", ""]
    for s in summaries:
        lines.append(f"## {GAMES[s.game].name_vi}")
        for src, r in s.sources.items():
            state = ("truy cập được qua PDF chính thức" if src == OFFICIAL and r.fallback_used else
                     "truy cập được" if r.reachable else "KHÔNG truy cập được")
            lines.append(f"- Nguồn `{src}`: {state}; dòng hợp lệ: {r.parsed}; quan sát mới: {r.new}; "
                         f"dòng bị loại: {r.rejected}" + (f"; ghi chú: {r.note}" if r.note else ""))
        if s.official_latest and s.archive_latest and s.official_latest < s.archive_latest:
            lines.append(f"- Cảnh báo: nguồn chính thức trả về dữ liệu cũ (kỳ mới nhất {s.official_latest:05d},"
                         f" kho lưu trữ đã có {s.archive_latest:05d}).")
        lines.append(f"- Phiên bản kết quả mới: {s.changes}; xung đột mới: {s.conflicts}")
        if s.pending_added:
            lines.append(f"- Kỳ đang chờ kết quả: {', '.join(f'{n:05d}' for n in s.pending_added)}")
        counts = ", ".join(f"{k}: {v}" for k, v in sorted(s.status_counts.items()))
        lines.append(f"- Trạng thái toàn bộ kỳ quay: {counts}")
        lines.append("")
    return "\n".join(lines)


def run(conn, audit, *, trigger: str = "manual", games: list[str] | None = None, official_pages: int = 1,
        http: Http | None = None) -> int:
    if not 1 <= official_pages <= 20:
        raise ValueError("official_pages phải trong khoảng 1..20")
    store = Store(conn, audit)
    runner = os.environ.get("GITHUB_RUN_ID") or os.environ.get("VERCEL_DEPLOYMENT_ID") or "local"
    started = utc_now()
    if not store.try_lock(LOCK):
        # Không ghi job_runs: lần chạy đang giữ khóa đã được cấp số id từ trước và chỉ ghi dòng của mình lúc kết thúc;
        # ghi thêm một dòng có id lớn hơn sẽ làm điểm kiểm tra coi dòng đó là “chèn vào quá khứ”.
        print("Bỏ qua: một lần thu thập khác đang chạy.")
        return 0
    job_id = store.new_job_id()
    http = http or Http()
    summaries: list[GameSummary] = []
    try:
        try:
            for game in games or list(GAMES):
                summaries.append(ingest_game(store, http, game, job_id, official_pages=official_pages))
            text = summary_vi(summaries)
            status = "success" if all(s.all_ok for s in summaries) else "partial"
            details = {s.game: asdict(s) for s in summaries}
            store.journal("ingest", "data", {"job_run_id": job_id, **details})
            store.report("ingest", None, text, {"job_run_id": job_id})
            details["checkpoint_id"] = store.checkpoint()["checkpoint_id"]
            conn.commit()
        except Exception as exc:
            conn.rollback()
            text = (summary_vi(summaries) + f"\n\nLỗi: {type(exc).__name__}: {exc}\n"
                    "Mọi thay đổi của lần chạy này đã được hủy; nhật ký truy cập nguồn vẫn được giữ.")
            try:
                store.finish_job(job_id, "ingest", trigger, started, utc_now(), "failed", text,
                                 {"error": f"{type(exc).__name__}: {exc}"}, runner)
            except Exception as audit_exc:  # không che lỗi gốc, nhưng báo rõ
                print(f"Không ghi được job_runs: {audit_exc}")
            raise
        # Ghi kết thúc khi VẪN giữ khóa: lần chạy sau chỉ bắt đầu khi lần này đã ghi xong trạng thái.
        try:
            store.finish_job(job_id, "ingest", trigger, started, utc_now(), status, text, details, runner)
        except Exception as exc:
            # Dữ liệu đã được ghi và có điểm kiểm tra; chỉ thiếu dòng job_runs → báo lỗi rõ, thoát mã khác 0.
            print(text)
            print(f"CẢNH BÁO: dữ liệu đã lưu (điểm kiểm tra #{details.get('checkpoint_id')}) nhưng không ghi được "
                  f"job_runs: {type(exc).__name__}: {exc}")
            return 4
    finally:
        store.unlock(LOCK)
    print(text)
    return 0
