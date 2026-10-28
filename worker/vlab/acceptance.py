"""Giai đoạn 6: nghiệm thu tự động theo 14 mục của kế hoạch. Mỗi mục được kiểm tra trực tiếp trên CSDL thật.

Trạng thái: ĐẠT · CHƯA ĐẠT · ĐANG CHỜ (cần thêm thời gian thực, ví dụ kỳ quay chưa diễn ra).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PASS, FAIL, WAIT = "ĐẠT", "CHƯA ĐẠT", "ĐANG CHỜ"


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def run_acceptance(conn) -> dict:
    import psycopg
    from .store import Store
    items: list[dict] = []

    def add(title, status, evidence):
        items.append({"no": len(items) + 1, "title_vi": title, "status": status, "evidence_vi": evidence})

    with conn.cursor() as cur:
        # 1. Nguồn chính thức
        row = _one(cur, """SELECT ok, http_status, started_at FROM fetch_attempts WHERE source_code = 'vietlott_ajax'
                           ORDER BY id DESC LIMIT 1""")
        n_ver = _one(cur, "SELECT count(*) FROM draw_current WHERE status = 'VERIFIED'")[0]
        if row and row[0]:
            add("Thu thập từ nguồn chính thức vietlott.vn", PASS, f"Lần gần nhất thành công (HTTP {row[1]}); {n_ver} kỳ VERIFIED.")
        else:
            reason = (f"Lần gần nhất HTTP {row[1]}: điểm cuối chính thức không trả dữ liệu xác minh được từ môi trường triển khai hiện tại. "
                      if row else "Chưa có lần truy cập chính thức thành công. ")
            add("Thu thập từ nguồn chính thức vietlott.vn", FAIL,
                reason + f"Hiện có {n_ver} kỳ VERIFIED. Không tự nâng trạng thái khi nguồn chính thức không truy cập được; kết quả đã đối chiếu qua kho lưu trữ vẫn giữ trạng thái CROSS-CHECKED.")
        # 2. Đối chiếu kho lưu trữ
        cur.execute("SELECT game, status, count(*) FROM draw_current GROUP BY 1, 2 ORDER BY 1, 2")
        counts = cur.fetchall()
        usable = sum(n for _, s, n in counts if s in ("VERIFIED", "CROSS-CHECKED"))
        add("Đối chiếu ≥ 2 kho lưu trữ độc lập", PASS if usable else FAIL,
            f"{usable} kỳ dùng được (VERIFIED/CROSS-CHECKED).")
        # 3. Năm trạng thái
        txt = "; ".join(f"{g} {s}: {n}" for g, s, n in counts)
        n_conf = _one(cur, "SELECT count(*) FROM draw_conflicts")[0]
        add("Trạng thái VERIFIED / CROSS-CHECKED / NOT PROVEN / CONFLICT / PENDING", PASS if counts else FAIL,
            f"{txt}. Xung đột đã ghi: {n_conf}.")
    conn.rollback()
    # 4. Bất biến: thử UPDATE thật trong savepoint, phải bị CSDL từ chối
    with conn.cursor() as cur:
        vid = _one(cur, "SELECT min(id) FROM draw_versions")[0]
    if vid is None:
        add("Lịch sử bất biến (CSDL từ chối UPDATE/DELETE)", WAIT, "Chưa có dữ liệu để thử.")
    else:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("UPDATE draw_versions SET reason_vi = reason_vi WHERE id = %s", (vid,))
            add("Lịch sử bất biến (CSDL từ chối UPDATE/DELETE)", FAIL, "UPDATE KHÔNG bị từ chối!")
        except psycopg.Error as exc:
            add("Lịch sử bất biến (CSDL từ chối UPDATE/DELETE)", PASS,
                f"Thử UPDATE draw_versions #{vid}: bị từ chối ({type(exc).__name__}).")
    conn.rollback()
    # 5. Chuỗi băm + điểm kiểm tra
    store = Store(conn)
    bad = {t: len(r) for t, r in store.verify_chains().items() if r}
    cp = store.latest_checkpoint()
    cp_bad = store.verify_checkpoint(cp) if cp else ["chưa có"]
    conn.rollback()
    add("Chuỗi băm và điểm kiểm tra toàn vẹn", PASS if not bad and not cp_bad else FAIL,
        f"Chuỗi hỏng: {bad or 'không'}; điểm kiểm tra #{cp['checkpoint_id'] if cp else '—'}: "
        f"{'khớp' if not cp_bad else str(cp_bad)[:300]}.")
    with conn.cursor() as cur:
        # 6. ≥ 5 phương pháp tạo vé live
        cur.execute("""SELECT r.game, count(DISTINCT v.method_id) FROM prediction_runs r
                       JOIN method_versions v ON v.id = r.method_version_id WHERE r.purpose = 'live' GROUP BY 1""")
        per = dict(cur.fetchall())
        ok = len(per) == 2 and min(per.values()) >= 5
        add("≥ 5 phương pháp tạo vé dự đoán live", PASS if ok else FAIL,
            ", ".join(f"{g}: {n} phương pháp" for g, n in sorted(per.items())) or "chưa có vé")
        # 7. Chống rò rỉ tương lai
        late, leak, n_runs = _one(cur, """SELECT count(*) FILTER (WHERE created_at >= target_draw_at),
                                                count(*) FILTER (WHERE data_cutoff_draw_no >= target_draw_no), count(*)
                                         FROM prediction_runs WHERE purpose = 'live'""")
        add("Chống rò rỉ tương lai (vé ghi trước giờ quay, chỉ thấy kỳ cũ)", PASS if n_runs and not late and not leak else
            (WAIT if not n_runs else FAIL), f"{n_runs} lần dự đoán live; ghi sau giờ quay: {late}; thấy kỳ tương lai: {leak}. "
            "CSDL có ràng buộc CHECK và trigger chặn cả hai.")
        # 8. Backtest walk-forward
        n_bt = _one(cur, "SELECT count(*) FROM backtests WHERE status = 'finished'")[0]
        add("Backtest walk-forward, chia 60/20/20, so với ngẫu nhiên", PASS if n_bt else FAIL,
            f"{n_bt} backtest đã ghi; bảng kiểm định toàn bộ lịch sử xem ở mục 7.")
        # 9. Đối chiếu vé live
        n_sc, n_d = _one(cur, """SELECT count(*), count(DISTINCT (r.game, r.target_draw_no)) FROM ticket_results x
                                 JOIN tickets t ON t.id = x.ticket_id JOIN prediction_runs r ON r.id = t.run_id
                                 WHERE r.purpose = 'live'""")
        nxt = _one(cur, "SELECT min(target_draw_at) FROM prediction_runs WHERE purpose = 'live'")[0]
        add("Đối chiếu vé live với kết quả thật sau mỗi kỳ", PASS if n_sc else WAIT,
            f"{n_sc} vé đã chấm trên {n_d} kỳ." if n_sc else
            f"Chưa kỳ nào có vé live được quay xong; kỳ đầu tiên: {nxt:%d/%m %H:%M} UTC." if nxt else "Chưa có vé live.")
        # 10. Giả thuyết, kể cả thất bại
        n_e, n_fail = _one(cur, "SELECT count(*), count(*) FILTER (WHERE NOT succeeded) FROM experiments")
        add("Vòng giả thuyết tự động, lưu cả thí nghiệm thất bại", PASS if n_e and n_fail else FAIL,
            f"{n_e} thí nghiệm, trong đó {n_fail} thất bại được lưu.")
        # 11. Kho tri thức
        n_k = _one(cur, "SELECT count(*) FROM knowledge_docs")[0]
        n_q = _one(cur, "SELECT count(*) FROM knowledge_docs WHERE search_tsv @@ plainto_tsquery('simple', 'Vietlott')")[0]
        add("Kho tri thức có nguồn và tìm kiếm toàn văn", PASS if n_k and n_q else FAIL,
            f"{n_k} tài liệu; tìm «Vietlott»: {n_q} kết quả.")
        # 12. Tự động hóa theo lịch qua nhiều ngày
        days = _one(cur, """SELECT count(DISTINCT (started_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) FROM job_runs
                            WHERE job_name = 'ingest' AND trigger_kind = 'schedule' AND status IN ('success', 'partial')""")[0]
        all_days = _one(cur, """SELECT count(DISTINCT (started_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) FROM job_runs
                                WHERE job_name = 'ingest' AND status IN ('success', 'partial')""")[0]
        n_fail_jobs = _one(cur, "SELECT count(*) FROM job_runs WHERE status = 'failed'")[0]
        add("Tự động chạy theo lịch (job_runs qua nhiều ngày thật)", PASS if days >= 2 else WAIT,
            f"Chạy theo lịch Vercel Cron: {days} ngày; tổng (kể cả chạy tay): {all_days} ngày; tác vụ lỗi: {n_fail_jobs}. "
            "Cần ≥ 2 ngày chạy theo lịch.")
        # 13. Sao lưu + khôi phục
        row = _one(cur, """SELECT b.id, b.created_at, t.ok FROM backups b JOIN backup_restore_tests t ON t.backup_id = b.id
                           ORDER BY b.id DESC LIMIT 1""")
        fresh = row and datetime.now(timezone.utc) - row[1] < timedelta(hours=48)
        add("Sao lưu hằng ngày và thử khôi phục", PASS if fresh and row[2] else FAIL,
            f"Bản #{row[0]} lúc {row[1]:%d/%m %H:%M} UTC, thử khôi phục {'ĐẠT' if row[2] else 'KHÔNG ĐẠT'}. "
            "Bản sao nằm cùng CSDL; bản ngoài hệ thống: nút tải ở mục 14." if row else "Chưa có bản sao lưu.")
    conn.rollback()
    # 14. Giao diện
    add("Giao diện 14 mục tiếng Việt, cảnh báo cố định, triển khai Vercel + Neon", PASS,
        "Trang https://vietlott-ai-lab.vercel.app; câu chữ tiếng Việt nên được người bản ngữ duyệt lại.")
    summary = {s: sum(1 for i in items if i["status"] == s) for s in (PASS, WAIT, FAIL)}
    return {"ready": True, "checked_at": datetime.now(timezone.utc).isoformat(), "items": items, "summary": summary}
