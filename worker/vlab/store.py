"""Ghi dữ liệu vào PostgreSQL. Chỉ INSERT — không bao giờ UPDATE hay DELETE lịch sử.

Hai kết nối:
* `conn`  — giao dịch của tác vụ (quan sát, phiên bản, nhật ký); rollback nếu tác vụ lỗi.
* `audit` — tự cam kết, chỉ dùng cho fetch_attempts: dấu vết truy cập nguồn còn lại kể cả khi tác vụ lỗi.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time

from .games import get_game
from .models import Observation
from .reconcile import CONFLICT, PENDING, USABLE, Decision, SourceInfo, decide
from .sources.http import FetchRecord

MAX_LOG_ITEMS = 200


@dataclass(frozen=True)
class Current:
    draw_id: int
    version_id: int
    version_no: int
    status: str
    draw_date: date | None
    draw_time: time | None
    draw_time_source: str | None
    main_numbers: tuple[int, ...] | None
    bonus_number: int | None

    def content(self) -> tuple:
        return (self.status, self.draw_date, self.draw_time, self.draw_time_source,
                self.main_numbers, self.bonus_number)


@dataclass
class VersionChange:
    game: str
    draw_no: int
    old_status: str | None
    new_status: str
    reason_vi: str


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    def __init__(self, conn, audit=None):
        if audit is not None and audit is not conn and not getattr(audit, "autocommit", False):
            raise ValueError("Kết nối audit phải ở chế độ autocommit")
        self.conn = conn
        self.audit = audit or conn

    # ------------------------------------------------------------------ cơ bản
    def new_job_id(self) -> int:
        with self.audit.cursor() as cur:
            cur.execute("SELECT nextval('job_runs_id_seq')")
            return cur.fetchone()[0]

    def try_lock(self, name: str) -> bool:
        """Khóa tư vấn cấp phiên trên kết nối tác vụ: hai lần chạy cùng lúc không thể chồng lên nhau."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (f"vlab:{name}",))
            ok = cur.fetchone()[0]
        self.conn.commit()
        return ok

    def unlock(self, name: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"vlab:{name}",))
        self.conn.commit()

    def source_info(self) -> dict[str, SourceInfo]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT code, kind, provenance_group FROM sources")
            return {code: SourceInfo(kind, group) for code, kind, group in cur.fetchall()}

    def record_fetch(self, source: str, game: str | None, rec: FetchRecord, job_id: int,
                     parsed: int = 0, rejected: list[str] | None = None) -> int:
        rejected = rejected or []
        with self.audit.cursor() as cur:
            cur.execute(
                """INSERT INTO fetch_attempts (source_code, game, url, started_at, finished_at, http_status, ok,
                       error_text, attempts_log, raw_sha256, bytes, rows_parsed, rows_rejected, rejected_log,
                       job_run_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (source, game, rec.url, rec.started_at, rec.finished_at or rec.started_at, rec.http_status,
                 rec.ok, None if rec.ok else (rec.error_text or "lỗi không rõ"),
                 _j(rec.attempts[:MAX_LOG_ITEMS]), rec.raw_sha256, rec.size,
                 parsed, len(rejected), _j(rejected[:MAX_LOG_ITEMS]), job_id),
            )
            fid = cur.fetchone()[0]
        if self.audit is not self.conn:
            self.audit.commit()
        return fid

    def add_observations(self, observations: list[Observation], fetch_id: int) -> int:
        """Ghi các quan sát khác với quan sát mới nhất của cùng nguồn. Trả về số dòng mới."""
        if not observations:
            return 0
        source, game = observations[0].source, observations[0].game
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (draw_no) draw_no, content_sha256 FROM draw_observations
                   WHERE source_code = %s AND game = %s ORDER BY draw_no, id DESC""",
                (source, game),
            )
            latest = dict(cur.fetchall())
            rows = []
            for o in observations:
                if o.source != source or o.game != game:
                    raise ValueError("add_observations: mỗi lần gọi chỉ một nguồn và một trò chơi")
                h = o.content_hash()
                if latest.get(int(o.draw_no)) == h:
                    continue
                latest[int(o.draw_no)] = h
                rows.append((o.source, fetch_id, o.game, int(o.draw_no), o.draw_date, o.draw_time,
                             list(o.main_numbers), o.bonus_number, o.source_url, list(o.evidence_urls),
                             o.observed_at, h))
            if rows:
                cur.executemany(
                    """INSERT INTO draw_observations (source_code, fetch_attempt_id, game, draw_no, draw_date,
                           draw_time, main_numbers, bonus_number, source_url, evidence_urls, observed_at,
                           content_sha256)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    rows,
                )
            return len(rows)

    # ------------------------------------------------------------------ đối chiếu
    def latest_observations(self, game: str) -> dict[int, list[tuple[int, Observation]]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (draw_no, source_code) id, source_code, draw_no, draw_date, draw_time,
                          main_numbers, bonus_number, source_url, evidence_urls, observed_at
                   FROM draw_observations WHERE game = %s
                   ORDER BY draw_no, source_code, id DESC""",
                (game,),
            )
            out: dict[int, list[tuple[int, Observation]]] = defaultdict(list)
            for oid, src, dno, ddate, dtime, main, bonus, url, ev, at in cur.fetchall():
                out[dno].append((oid, Observation.build(
                    source=src, game=game, draw_no=dno, draw_date=ddate, draw_time=dtime, main_numbers=main,
                    bonus_number=bonus, source_url=url, evidence_urls=ev or (), observed_at=at.isoformat())))
            return out

    def current_versions(self, game: str) -> dict[int, Current]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT draw_no, draw_id, version_id, version_no, status, draw_date, draw_time, draw_time_source,
                          main_numbers, bonus_number
                   FROM draw_current WHERE game = %s""",
                (game,),
            )
            return {r[0]: Current(r[1], r[2], r[3], r[4], r[5], r[6], r[7],
                                  tuple(r[8]) if r[8] is not None else None, r[9]) for r in cur.fetchall()}

    def last_verified(self, game: str) -> dict[int, tuple]:
        """Bản VERIFIED gần nhất của từng kỳ (ngày, giờ, số chính, số đặc biệt)."""
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (d.draw_no) d.draw_no, v.draw_date, v.draw_time, v.main_numbers, v.bonus_number
                   FROM draws d JOIN draw_versions v ON v.draw_id = d.id
                   WHERE d.game = %s AND v.status = 'VERIFIED' ORDER BY d.draw_no, v.version_no DESC""",
                (game,),
            )
            return {r[0]: (r[1], r[2], tuple(r[3]), r[4]) for r in cur.fetchall()}

    def _ensure_draw(self, cur, game: str, draw_no: int) -> int:
        cur.execute("SELECT id FROM draws WHERE game = %s AND draw_no = %s", (game, draw_no))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("INSERT INTO draws (game, draw_no) VALUES (%s,%s) RETURNING id", (game, draw_no))
        return cur.fetchone()[0]

    def _add_version(self, cur, game: str, draw_no: int, current: Current | None, content: tuple, *,
                     observation_ids: list[int], reason_vi: str, created_by: str) -> int:
        status, d, t, t_src, main, bonus = content
        draw_id = current.draw_id if current else self._ensure_draw(cur, game, draw_no)
        cur.execute(
            """INSERT INTO draw_versions (draw_id, version_no, draw_date, draw_time, draw_time_source, main_numbers,
                   bonus_number, status, observation_ids, reason_vi, previous_version_id, created_by,
                   row_hash, prev_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'','') RETURNING id""",
            (draw_id, current.version_no + 1 if current else 1, d, t, t_src,
             list(main) if main is not None else None, bonus, status, sorted(observation_ids), reason_vi,
             current.version_id if current else None, created_by),
        )
        return cur.fetchone()[0]

    def reconcile_game(self, game: str, created_by: str = "job:ingest") -> list[VersionChange]:
        g = get_game(game)
        infos = self.source_info()
        observed = self.latest_observations(game)
        current = self.current_versions(game)
        verified = self.last_verified(game)
        changes: list[VersionChange] = []
        with self.conn.cursor() as cur:
            for draw_no in sorted(observed):
                pairs = observed[draw_no]
                ids = sorted(oid for oid, _ in pairs)
                decision: Decision = decide([o for _, o in pairs], infos, scheduled_time=g.draw_time_local)
                status, reason, acc = decision.status, decision.reason_vi, decision.observation
                # Tác vụ tự động không bao giờ thay bộ số đã VERIFIED: nếu nguồn nay nói khác, chỉ đánh dấu CONFLICT.
                old_ok = verified.get(draw_no)
                if acc is not None and old_ok and old_ok != (acc.draw_date, decision.draw_time, acc.main_numbers,
                                                             acc.bonus_number):
                    status, acc = CONFLICT, None
                    reason = ("Các nguồn hiện công bố kết quả khác với bản đã VERIFIED trước đó; "
                              "cần người duyệt xác nhận — " + decision.reason_vi)
                if acc is None:
                    content = (status, None, None, None, None, None)
                else:
                    content = (status, acc.draw_date, decision.draw_time, decision.draw_time_source,
                               acc.main_numbers, acc.bonus_number)
                cur_v = current.get(draw_no)
                if status == CONFLICT:
                    draw_id = cur_v.draw_id if cur_v else self._ensure_draw(cur, game, draw_no)
                    cur.execute(
                        """INSERT INTO draw_conflicts (draw_id, observation_ids, description_vi)
                           VALUES (%s,%s,%s) ON CONFLICT (draw_id, observation_ids) DO NOTHING""",
                        (draw_id, ids, reason),
                    )
                if cur_v and cur_v.content() == content:
                    continue
                if cur_v:
                    reason = f"Thay đổi từ {cur_v.status} sang {status}: {reason}"
                    if cur_v.status in USABLE and status in USABLE and cur_v.content()[1:] != content[1:]:
                        reason = "Tự sửa theo nguồn (bộ số chưa từng VERIFIED). " + reason
                self._add_version(cur, game, draw_no, cur_v, content, observation_ids=ids,
                                  reason_vi=reason, created_by=created_by)
                changes.append(VersionChange(game, draw_no, cur_v.status if cur_v else None, status, reason))
        return changes

    def add_pending(self, game: str, draw_no: int, scheduled_at: datetime) -> bool:
        """Kỳ KẾ TIẾP theo lịch chưa có dữ liệu. Ngày/giờ là ngày theo lịch (draw_time_source='schedule').
        Chỉ đánh dấu một kỳ: số kỳ chỉ tăng khi thực sự có quay, nên không thể đoán số của các kỳ sau
        (nghỉ Tết hay hoãn quay sẽ làm lệch). Khi kỳ này có kết quả, lần chạy sau đánh dấu kỳ tiếp theo."""
        if draw_no in self.current_versions(game):
            return False
        from .schedule import VN_TZ, require_aware
        local = require_aware(scheduled_at).astimezone(VN_TZ)
        with self.conn.cursor() as cur:
            self._add_version(cur, game, draw_no, None, (PENDING, local.date(), local.time(), "schedule", None, None),
                              observation_ids=[],
                              reason_vi=f"Theo lịch quay lúc {scheduled_at.isoformat()} nhưng chưa nguồn nào "
                                        "công bố kết quả (có thể do nghỉ lễ)",
                              created_by="job:schedule")
        return True

    # ------------------------------------------------------------------ cài đặt, nhật ký
    def set_setting(self, key: str, value, changed_by: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM settings_current WHERE key = %s", (key,))
            row = cur.fetchone()
            old = row[0] if row else None
            if old == value:
                return
            cur.execute(
                "INSERT INTO settings_history (key, old_value, new_value, changed_by) VALUES (%s,%s,%s,%s)",
                (key, _j(old) if row else None, _j(value), changed_by),
            )
            cur.execute(
                """INSERT INTO ai_journal (actor, action, change_kind, details, row_hash, prev_hash)
                   VALUES (%s,'setting','params',%s,'','')""",
                ("user" if changed_by.startswith("user:") else "job",
                 _j({"key": key, "old": old, "new": value, "by": changed_by})),
            )

    def journal(self, action: str, change_kind: str, details: dict, actor: str = "job") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_journal (actor, action, change_kind, details, row_hash, prev_hash)
                   VALUES (%s,%s,%s,%s,'','')""",
                (actor, action, change_kind, _j(details)),
            )

    def report(self, kind: str, game: str | None, content_vi: str, data_refs: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO reports (kind, game, content_vi, data_refs, row_hash, prev_hash)
                   VALUES (%s,%s,%s,%s,'','')""",
                (kind, game, content_vi, _j(data_refs)),
            )

    def finish_job(self, job_id: int, name: str, trigger: str, started: datetime, finished: datetime,
                   status: str, summary_vi: str, details: dict, runner: str) -> None:
        with self.audit.cursor() as cur:
            cur.execute(
                """INSERT INTO job_runs (id, job_name, trigger_kind, started_at, finished_at, status, summary_vi,
                       details, runner) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (job_id, name, trigger, started, finished, status, summary_vi, _j(details), runner),
            )
        if self.audit is not self.conn:
            self.audit.commit()

    # ------------------------------------------------------------------ xuất dữ liệu
    EXPORTS: dict[str, tuple[tuple[str, ...], str]] = {
        # Kết quả hiện hành của từng kỳ.
        "draws": (("game", "draw_no", "draw_date", "draw_time", "draw_time_source", "main_numbers",
                   "bonus_number", "status", "version_no", "reason_vi", "observation_ids"),
                  "FROM draw_current WHERE (%(game)s::text IS NULL OR game = %(game)s) ORDER BY game, draw_no"),
        # Toàn bộ lịch sử phiên bản (kể cả bản đã bị thay thế).
        "history": (("game", "draw_no", "version_no", "status", "draw_date", "draw_time", "draw_time_source",
                     "main_numbers", "bonus_number", "reason_vi", "observation_ids", "created_by",
                     "created_db_user", "created_at", "row_hash"),
                    """FROM (SELECT d.game, d.draw_no, v.* FROM draw_versions v JOIN draws d ON d.id = v.draw_id) x
                       WHERE (%(game)s::text IS NULL OR game = %(game)s) ORDER BY game, draw_no, version_no"""),
        # Từng quan sát kèm bằng chứng truy cập: URL, thời điểm, mã HTTP, băm dữ liệu thô.
        "observations": (("observation_id", "source_code", "game", "draw_no", "draw_date", "draw_time",
                          "main_numbers", "bonus_number", "source_url", "evidence_urls", "observed_at",
                          "content_sha256", "fetch_attempt_id", "fetch_url", "fetch_started_at",
                          "fetch_finished_at", "http_status", "raw_sha256"),
                         """FROM (SELECT o.id AS observation_id, o.source_code, o.game, o.draw_no, o.draw_date,
                                         o.draw_time, o.main_numbers, o.bonus_number, o.source_url, o.evidence_urls,
                                         o.observed_at, o.content_sha256, o.fetch_attempt_id, f.url AS fetch_url,
                                         f.started_at AS fetch_started_at, f.finished_at AS fetch_finished_at,
                                         f.http_status, f.raw_sha256
                                  FROM draw_observations o JOIN fetch_attempts f ON f.id = o.fetch_attempt_id) x
                            WHERE (%(game)s::text IS NULL OR game = %(game)s) ORDER BY game, draw_no, observation_id"""),
        "conflicts": (("conflict_id", "game", "draw_no", "observation_ids", "description_vi", "created_at",
                       "resolved_version_id", "resolved_by", "resolution_note_vi"),
                      """FROM (SELECT c.id AS conflict_id, d.game, d.draw_no, c.observation_ids, c.description_vi,
                                      c.created_at, r.resolved_version_id, r.resolved_by, r.note_vi AS resolution_note_vi
                               FROM draw_conflicts c JOIN draws d ON d.id = c.draw_id
                               LEFT JOIN draw_conflict_resolutions r ON r.conflict_id = c.id) x
                         WHERE (%(game)s::text IS NULL OR game = %(game)s) ORDER BY game, draw_no, conflict_id"""),
    }

    def export_rows(self, kind: str = "draws", game: str | None = None) -> tuple[tuple[str, ...], list[dict]]:
        cols, tail = self.EXPORTS[kind]
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(cols)} {tail}", {"game": game})
            return cols, [dict(zip(cols, r)) for r in cur.fetchall()]

    @staticmethod
    def _cell(key: str, value):
        if value is None:
            return ""
        if key == "draw_no":
            return f"{value:05d}"
        if key == "main_numbers":
            return " ".join(f"{n:02d}" for n in value)
        if isinstance(value, (list, tuple)):
            return " ".join(str(v) for v in value)
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        return value

    @classmethod
    def to_csv(cls, cols: tuple[str, ...], rows: list[dict]) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([cls._cell(k, r[k]) for k in cols])
        return buf.getvalue()

    @staticmethod
    def to_json(cols: tuple[str, ...], rows: list[dict]) -> str:
        def conv(k, v):
            if k == "draw_no" and v is not None:
                return f"{v:05d}"
            if isinstance(v, tuple):
                return list(v)
            return v
        return json.dumps([{k: conv(k, r[k]) for k in cols} for r in rows], ensure_ascii=False, default=str,
                          indent=1)

    # ------------------------------------------------------------------ toàn vẹn
    def checkpoint(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT vlab_make_checkpoint()")
            return cur.fetchone()[0]

    def latest_checkpoint(self) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, tables FROM chain_checkpoints ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            return {"checkpoint_id": row[0], "tables": row[1]} if row else None

    def verify_checkpoint(self, cp: dict) -> list[tuple[str, str]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT table_name, problem FROM vlab_verify_checkpoint(%s::jsonb)", (_j(cp),))
            return cur.fetchall()

    def record_backup(self, location: str, size_bytes: int, sha256: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO backups (location, size_bytes, sha256) VALUES (%s,%s,%s) RETURNING id",
                        (location, size_bytes, sha256))
            return cur.fetchone()[0]

    def record_restore_test(self, backup_id: int, ok: bool, details_vi: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO backup_restore_tests (backup_id, ok, details_vi) VALUES (%s,%s,%s) RETURNING id",
                        (backup_id, ok, details_vi))
            return cur.fetchone()[0]

    def verify_chains(self) -> dict[str, list]:
        tables = ["draw_versions", "method_versions", "prediction_runs", "tickets", "hypotheses",
                  "experiments", "reports", "ai_journal", "audit_log", "chain_checkpoints"]
        out = {}
        with self.conn.cursor() as cur:
            for t in tables:
                cur.execute("SELECT bad_id, problem FROM vlab_verify_chain(%s::regclass)", (t,))
                out[t] = cur.fetchall()
        return out
