-- Vietlott AI Lab — lược đồ cơ sở dữ liệu, phiên bản 1.
--
-- Nguyên tắc:
-- * Lịch sử chỉ được ghi thêm. UPDATE / DELETE / TRUNCATE trên mọi bảng lịch sử bị trigger chặn;
--   vai trò ứng dụng vlab_app chỉ có SELECT/INSERT.
-- * Chuỗi băm phát hiện chỉnh sửa trái phép của người có quyền chủ sở hữu (vượt qua trigger).
--   Chuỗi băm KHÔNG ngăn được chủ sở hữu; nó giúp phát hiện. Bản sao lưu bên ngoài là lớp bảo vệ thứ hai.
-- * Thời điểm ghi (created_at) do CSDL đặt bằng clock_timestamp(), không tin giá trị từ ứng dụng.
-- * Kết quả đã VERIFIED không thể bị tác vụ tự động thay bằng bộ số khác: chỉ có thể chuyển sang
--   CONFLICT (bị loại khỏi tính toán). Đổi bộ số đã xác nhận cần vai trò vlab_admin (người duyệt).
-- * Mọi hàm đặt search_path cố định (pg_temp ở cuối) để bảng tạm không thể che bảng thật.
--
-- Toàn bộ tệp chạy trong một giao dịch do trình migrate quản lý (có khóa tư vấn chống chạy song song).

SET search_path = public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vlab_app') THEN CREATE ROLE vlab_app NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vlab_web') THEN CREATE ROLE vlab_web NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vlab_admin') THEN CREATE ROLE vlab_admin NOLOGIN; END IF;
END;
$$;

-- ---------------------------------------------------------------- tiện ích bất biến
CREATE OR REPLACE FUNCTION vlab_forbid_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'Bảng % chỉ cho phép ghi thêm: thao tác % bị cấm', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

-- created_at luôn là thời điểm thật của CSDL.
CREATE OR REPLACE FUNCTION vlab_force_now() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;

-- Chuỗi băm: row_hash = sha256(prev_hash || nội dung dòng dạng jsonb, giờ UTC).
-- Tên trigger 'zz_hash_chain' bảo đảm chạy SAU mọi trigger BEFORE INSERT khác (thứ tự theo tên).
CREATE OR REPLACE FUNCTION vlab_hash_chain() RETURNS trigger
LANGUAGE plpgsql SET TimeZone = 'UTC' SET search_path = public, pg_temp AS $$
DECLARE
    last_hash text;
    payload   jsonb;
BEGIN
    -- Khóa theo bảng tới hết giao dịch, rồi cấp lại id: thứ tự id luôn trùng thứ tự chuỗi,
    -- kể cả khi hai giao dịch chèn song song (id mặc định được lấy trước khi chờ khóa).
    PERFORM pg_advisory_xact_lock(hashtext('vlab_chain:' || TG_RELID::text));
    NEW.id := nextval(pg_get_serial_sequence(TG_RELID::regclass::text, 'id'));
    EXECUTE format('SELECT row_hash FROM %s ORDER BY id DESC LIMIT 1', TG_RELID::regclass) INTO last_hash;
    NEW.prev_hash := COALESCE(last_hash, repeat('0', 64));
    payload := to_jsonb(NEW) - 'row_hash' - 'prev_hash';
    NEW.row_hash := encode(digest(NEW.prev_hash || payload::text, 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION vlab_make_append_only(tbl regclass) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    EXECUTE format('CREATE TRIGGER append_only_row BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION vlab_forbid_change()', tbl);
    EXECUTE format('CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON %s FOR EACH STATEMENT EXECUTE FUNCTION vlab_forbid_change()', tbl);
END;
$$;

CREATE OR REPLACE FUNCTION vlab_make_chained(tbl regclass) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    EXECUTE format('CREATE TRIGGER zz_hash_chain BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION vlab_hash_chain()', tbl);
END;
$$;

CREATE OR REPLACE FUNCTION vlab_make_timestamped(tbl regclass) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    EXECUTE format('CREATE TRIGGER a_force_now BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION vlab_force_now()', tbl);
END;
$$;

-- ---------------------------------------------------------------- danh mục (chỉ chủ sở hữu ghi)
CREATE TABLE games (
    code          text PRIMARY KEY CHECK (code IN ('mega645', 'power655')),
    name_vi       text NOT NULL,
    main_count    smallint NOT NULL CHECK (main_count = 6),
    max_number    smallint NOT NULL CHECK (max_number IN (45, 55)),
    has_bonus     boolean NOT NULL,
    draw_isodows  smallint[] NOT NULL,           -- 1 = Thứ Hai … 7 = Chủ Nhật
    draw_time_ict time NOT NULL,
    rules_url     text NOT NULL,
    schedule_url  text NOT NULL
);
INSERT INTO games VALUES
    ('mega645',  'Mega 6/45',  6, 45, false, '{3,5,7}', '18:00',
     'https://vietlott.vn/vi/choi/mega-6-45/gioi-thieu-san-pham-6-45',
     'https://vietlott.vn/vi/choi/mega-6-45/gioi-thieu-san-pham-6-45'),
    ('power655', 'Power 6/55', 6, 55, true,  '{2,4,6}', '18:00',
     'https://vietlott.vn/vi/choi/power-6-55/gioi-thieu-san-pham-power-655',
     'https://vietlott.vn/vi/choi/power-6-55/gioi-thieu-san-pham-power-655');

-- provenance_group: các nguồn cùng nhóm chỉ được tính là MỘT xác nhận độc lập.
CREATE TABLE sources (
    code             text PRIMARY KEY,
    name             text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('official', 'archive', 'secondary')),
    provenance_group text NOT NULL,
    base_url         text NOT NULL,
    notes_vi         text NOT NULL DEFAULT ''
);
INSERT INTO sources VALUES
    ('vietlott_ajax', 'Vietlott (AjaxPro, danh sách kết quả)', 'official', 'vietlott', 'https://vietlott.vn/',
     'Nguồn chính thức. Trang danh sách có thể trả về bản lưu cũ hoặc chặn IP nước ngoài; luôn đối chiếu với kho lưu trữ.'),
    ('pqminh4', 'pqminh-4/vietlott-data', 'archive', 'crawl:pqminh4', 'https://github.com/pqminh-4/vietlott-data',
     'Kho thu thập độc lập từ HTML/AjaxPro/PDF của Vietlott; có liên kết PDF chính thức cho từng kỳ.'),
    ('vietvudanh', 'vietvudanh/vietlott-data', 'archive', 'crawl:vietvudanh', 'https://github.com/vietvudanh/vietlott-data',
     'Kho thu thập độc lập từ AjaxPro của Vietlott. Thiếu Mega 6/45 kỳ 00001–00197.'),
    ('nhanaz', 'NhanAZ-Data/vietlott-research', 'archive', 'crawl:nhanaz', 'https://github.com/NhanAZ-Data/vietlott-research',
     'Kho thu thập độc lập; các kỳ mới có thể lấy từ Minh Ngọc (official_verification_status=pending).');

-- ---------------------------------------------------------------- kiểm tra bộ số dùng chung
CREATE OR REPLACE FUNCTION vlab_check_numbers(p_game text, p_main smallint[], p_bonus smallint, p_need_bonus boolean)
RETURNS void LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    g games%ROWTYPE;
BEGIN
    SELECT * INTO g FROM games WHERE code = p_game;
    IF NOT FOUND THEN RAISE EXCEPTION 'Trò chơi không tồn tại: %', p_game; END IF;
    IF p_main IS NULL THEN
        IF p_bonus IS NOT NULL THEN RAISE EXCEPTION 'Có số đặc biệt nhưng không có số chính'; END IF;
        RETURN;
    END IF;
    IF cardinality(p_main) <> g.main_count OR array_position(p_main, NULL) IS NOT NULL THEN
        RAISE EXCEPTION '%: cần % số chính, nhận %', g.name_vi, g.main_count, p_main;
    END IF;
    IF (SELECT count(DISTINCT x) FROM unnest(p_main) x) <> g.main_count THEN
        RAISE EXCEPTION '%: số chính bị trùng %', g.name_vi, p_main;
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(p_main) x WHERE x < 1 OR x > g.max_number) THEN
        RAISE EXCEPTION '%: số ngoài khoảng 1..%', g.name_vi, g.max_number;
    END IF;
    IF NOT g.has_bonus AND p_bonus IS NOT NULL THEN
        RAISE EXCEPTION '% không có số đặc biệt', g.name_vi;
    END IF;
    IF g.has_bonus AND p_need_bonus AND p_bonus IS NULL THEN
        RAISE EXCEPTION '%: thiếu số đặc biệt', g.name_vi;
    END IF;
    IF p_bonus IS NOT NULL AND (p_bonus < 1 OR p_bonus > g.max_number OR p_bonus = ANY (p_main)) THEN
        RAISE EXCEPTION '%: số đặc biệt % không hợp lệ', g.name_vi, p_bonus;
    END IF;
END;
$$;

-- ---------------------------------------------------------------- vận hành (cần trước các bảng khác)
CREATE TABLE job_runs (
    id            bigserial PRIMARY KEY,
    job_name      text NOT NULL,
    trigger_kind  text NOT NULL CHECK (trigger_kind IN ('schedule', 'manual', 'ci')),
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz NOT NULL,
    status        text NOT NULL CHECK (status IN ('success', 'partial', 'failed', 'skipped')),
    summary_vi    text NOT NULL,
    details       jsonb NOT NULL DEFAULT '{}',
    runner        text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (finished_at >= started_at)
);

-- ---------------------------------------------------------------- kỳ quay
-- Mỗi lần truy cập nguồn (kể cả thất bại) được ghi bằng kết nối riêng, tự cam kết: lỗi của tác vụ
-- không thể xóa dấu vết truy cập. job_run_id chỉ là tham chiếu (job_runs được ghi khi tác vụ kết thúc).
CREATE TABLE fetch_attempts (
    id            bigserial PRIMARY KEY,
    source_code   text NOT NULL REFERENCES sources(code),
    game          text REFERENCES games(code),
    url           text NOT NULL,
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz NOT NULL,
    http_status   integer,
    ok            boolean NOT NULL,
    error_text    text,
    attempts_log  jsonb NOT NULL DEFAULT '[]',
    raw_sha256    text CHECK (raw_sha256 IS NULL OR raw_sha256 ~ '^[0-9a-f]{64}$'),
    bytes         integer,
    rows_parsed   integer NOT NULL DEFAULT 0,
    rows_rejected integer NOT NULL DEFAULT 0,
    rejected_log  jsonb NOT NULL DEFAULT '[]',
    job_run_id    bigint NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (finished_at >= started_at),
    CHECK (ok OR error_text IS NOT NULL),
    CHECK (NOT ok OR (raw_sha256 IS NOT NULL AND http_status BETWEEN 200 AND 299)),  -- thành công phải có bằng chứng
    CHECK (url ~ '^https?://[^/\s]+')
);
CREATE INDEX fetch_attempts_job ON fetch_attempts (job_run_id);

CREATE TABLE draw_observations (
    id               bigserial PRIMARY KEY,
    source_code      text NOT NULL REFERENCES sources(code),
    fetch_attempt_id bigint NOT NULL REFERENCES fetch_attempts(id),
    game             text NOT NULL REFERENCES games(code),
    draw_no          integer NOT NULL CHECK (draw_no > 0),
    draw_date        date NOT NULL,
    draw_time        time,                 -- giờ quay nếu nguồn công bố; NULL nếu nguồn không nêu
    main_numbers     smallint[] NOT NULL,
    bonus_number     smallint,
    source_url       text NOT NULL CHECK (source_url ~ '^https?://'),
    evidence_urls    text[] NOT NULL DEFAULT '{}',   -- ví dụ: PDF kết quả chính thức
    observed_at      timestamptz NOT NULL,
    content_sha256   text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (fetch_attempt_id, draw_no)
);
-- Một nguồn có thể tự sửa dữ liệu: quan sát mới chỉ được ghi khi khác quan sát mới nhất của chính nguồn đó.
CREATE INDEX draw_observations_latest ON draw_observations (game, draw_no, source_code, id DESC);

CREATE OR REPLACE FUNCTION vlab_check_observation() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM vlab_check_numbers(NEW.game, NEW.main_numbers, NEW.bonus_number, true);
    IF NOT EXISTS (SELECT 1 FROM fetch_attempts f WHERE f.id = NEW.fetch_attempt_id
                   AND f.source_code = NEW.source_code AND f.game = NEW.game AND f.ok
                   AND f.raw_sha256 IS NOT NULL) THEN
        RAISE EXCEPTION 'Quan sát phải gắn với một lần truy cập thành công của chính nguồn % cho trò chơi %',
            NEW.source_code, NEW.game;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_numbers BEFORE INSERT ON draw_observations
    FOR EACH ROW EXECUTE FUNCTION vlab_check_observation();

CREATE TABLE draws (
    id          bigserial PRIMARY KEY,
    game        text NOT NULL REFERENCES games(code),
    draw_no     integer NOT NULL CHECK (draw_no > 0),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (game, draw_no)
);

CREATE TABLE draw_versions (
    id                  bigserial PRIMARY KEY,
    draw_id             bigint NOT NULL REFERENCES draws(id),
    version_no          integer NOT NULL CHECK (version_no >= 1),
    draw_date           date,
    draw_time           time,
    draw_time_source    text CHECK (draw_time_source IN ('source', 'schedule')),
    main_numbers        smallint[],
    bonus_number        smallint,
    status              text NOT NULL CHECK (status IN ('VERIFIED', 'CROSS-CHECKED', 'NOT PROVEN', 'CONFLICT', 'PENDING')),
    observation_ids     bigint[] NOT NULL DEFAULT '{}',
    reason_vi           text NOT NULL,
    previous_version_id bigint REFERENCES draw_versions(id),
    created_by          text NOT NULL,
    created_db_user     text NOT NULL DEFAULT current_user,
    created_at          timestamptz NOT NULL DEFAULT now(),
    row_hash            text NOT NULL,
    prev_hash           text NOT NULL,
    UNIQUE (draw_id, version_no),
    CHECK (status NOT IN ('VERIFIED', 'CROSS-CHECKED', 'NOT PROVEN')
           OR (main_numbers IS NOT NULL AND draw_date IS NOT NULL AND draw_time IS NOT NULL AND draw_time_source IS NOT NULL)),
    CHECK (status NOT IN ('CONFLICT', 'PENDING') OR main_numbers IS NULL),
    CHECK ((version_no = 1) = (previous_version_id IS NULL))
);

-- Mọi id trong observation_ids phải là quan sát có thật của ĐÚNG kỳ (cùng trò chơi, cùng số kỳ), không trùng.
CREATE OR REPLACE FUNCTION vlab_check_observation_ids(p_draw_id bigint, p_ids bigint[]) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    bad bigint;
BEGIN
    IF p_ids IS NULL OR array_position(p_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'observation_ids không được chứa NULL' USING ERRCODE = 'check_violation';
    END IF;
    IF cardinality(p_ids) <> (SELECT count(DISTINCT x) FROM unnest(p_ids) x) THEN
        RAISE EXCEPTION 'observation_ids bị trùng' USING ERRCODE = 'check_violation';
    END IF;
    SELECT x INTO bad FROM unnest(p_ids) x
     WHERE NOT EXISTS (SELECT 1 FROM draw_observations o JOIN draws d ON d.id = p_draw_id
                       WHERE o.id = x AND o.game = d.game AND o.draw_no = d.draw_no)
     LIMIT 1;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Quan sát % không thuộc kỳ này', bad USING ERRCODE = 'check_violation';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION vlab_check_draw_version() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    v_game text;
    prev   draw_versions%ROWTYPE;
    last_verified draw_versions%ROWTYPE;
BEGIN
    NEW.created_db_user := current_user;
    PERFORM pg_advisory_xact_lock(hashtext('draw_version:' || NEW.draw_id::text));
    SELECT game INTO v_game FROM draws WHERE id = NEW.draw_id;
    PERFORM vlab_check_numbers(v_game, NEW.main_numbers, NEW.bonus_number, NEW.main_numbers IS NOT NULL);
    PERFORM vlab_check_observation_ids(NEW.draw_id, NEW.observation_ids);
    IF NEW.created_by LIKE 'user:%' AND NOT pg_has_role(current_user, 'vlab_admin', 'MEMBER') THEN
        RAISE EXCEPTION 'Chỉ người duyệt (vlab_admin) được ghi created_by = user:…' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'PENDING' THEN  -- NULL ngày/giờ cũng bị từ chối (EXISTS trả về false)
        IF NOT EXISTS (SELECT 1 FROM games g WHERE g.code = v_game
                       AND extract(isodow FROM NEW.draw_date)::smallint = ANY (g.draw_isodows)
                       AND NEW.draw_time = g.draw_time_ict AND NEW.draw_time_source = 'schedule') THEN
            RAISE EXCEPTION 'PENDING phải mang ngày/giờ đúng lịch quay chính thức' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.status NOT IN ('PENDING') AND cardinality(NEW.observation_ids) = 0 THEN
        RAISE EXCEPTION 'Phiên bản % phải dựa trên ít nhất một quan sát', NEW.status USING ERRCODE = 'check_violation';
    END IF;
    -- Phiên bản mới phải nối tiếp đúng phiên bản mới nhất của cùng kỳ.
    SELECT * INTO prev FROM draw_versions WHERE draw_id = NEW.draw_id ORDER BY version_no DESC LIMIT 1;
    IF FOUND THEN
        IF NEW.previous_version_id IS DISTINCT FROM prev.id OR NEW.version_no <> prev.version_no + 1 THEN
            RAISE EXCEPTION 'Phiên bản mới phải nối tiếp phiên bản % của cùng kỳ', prev.version_no;
        END IF;
    ELSIF NEW.version_no <> 1 THEN
        RAISE EXCEPTION 'Phiên bản đầu tiên phải có số 1';
    END IF;
    -- Không ai ngoài vlab_admin được thay bộ số đã từng VERIFIED bằng bộ số khác.
    SELECT * INTO last_verified FROM draw_versions
     WHERE draw_id = NEW.draw_id AND status = 'VERIFIED' ORDER BY version_no DESC LIMIT 1;
    IF FOUND AND NEW.main_numbers IS NOT NULL
       AND (NEW.main_numbers IS DISTINCT FROM last_verified.main_numbers
            OR NEW.bonus_number IS DISTINCT FROM last_verified.bonus_number
            OR NEW.draw_date IS DISTINCT FROM last_verified.draw_date
            OR NEW.draw_time IS DISTINCT FROM last_verified.draw_time)
       AND NOT pg_has_role(current_user, 'vlab_admin', 'MEMBER') THEN
        RAISE EXCEPTION 'Kết quả đã VERIFIED chỉ có thể đổi bởi người duyệt (vlab_admin); tác vụ tự động chỉ được đánh dấu CONFLICT'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_version BEFORE INSERT ON draw_versions
    FOR EACH ROW EXECUTE FUNCTION vlab_check_draw_version();

CREATE TABLE draw_conflicts (
    id               bigserial PRIMARY KEY,
    draw_id          bigint NOT NULL REFERENCES draws(id),
    observation_ids  bigint[] NOT NULL,
    description_vi   text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (draw_id, observation_ids)
);
CREATE OR REPLACE FUNCTION vlab_check_conflict() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM vlab_check_observation_ids(NEW.draw_id, NEW.observation_ids);
    IF cardinality(NEW.observation_ids) < 1 THEN
        RAISE EXCEPTION 'Xung đột phải dẫn chứng quan sát' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_conflict BEFORE INSERT ON draw_conflicts
    FOR EACH ROW EXECUTE FUNCTION vlab_check_conflict();

-- Giải quyết xung đột: chỉ vlab_admin được ghi (vlab_app không có quyền INSERT).
CREATE TABLE draw_conflict_resolutions (
    id                  bigserial PRIMARY KEY,
    conflict_id         bigint NOT NULL REFERENCES draw_conflicts(id),
    resolved_version_id bigint NOT NULL REFERENCES draw_versions(id),
    resolved_by         text NOT NULL,
    note_vi             text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION vlab_check_resolution() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    IF (SELECT draw_id FROM draw_conflicts WHERE id = NEW.conflict_id)
       IS DISTINCT FROM (SELECT draw_id FROM draw_versions WHERE id = NEW.resolved_version_id) THEN
        RAISE EXCEPTION 'Phiên bản giải quyết phải thuộc cùng kỳ với xung đột' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_resolution BEFORE INSERT ON draw_conflict_resolutions
    FOR EACH ROW EXECUTE FUNCTION vlab_check_resolution();

CREATE VIEW draw_current AS
SELECT DISTINCT ON (d.id)
       d.id AS draw_id, d.game, d.draw_no, v.id AS version_id, v.version_no, v.draw_date, v.draw_time,
       v.draw_time_source,
       CASE WHEN v.draw_date IS NOT NULL AND v.draw_time IS NOT NULL
            THEN (v.draw_date + v.draw_time) AT TIME ZONE 'Asia/Ho_Chi_Minh' END AS draw_at,
       v.main_numbers, v.bonus_number, v.status, v.reason_vi, v.observation_ids,
       v.created_at AS version_created_at
FROM draws d
JOIN draw_versions v ON v.draw_id = d.id
ORDER BY d.id, v.version_no DESC;

CREATE VIEW draws_usable AS
SELECT * FROM draw_current WHERE status IN ('VERIFIED', 'CROSS-CHECKED');

-- ---------------------------------------------------------------- phương pháp và dự đoán
CREATE TABLE methods (
    id             bigserial PRIMARY KEY,
    code           text NOT NULL UNIQUE,
    name_vi        text NOT NULL,
    family         text NOT NULL CHECK (family IN ('random', 'statistics', 'timeseries', 'montecarlo', 'ml',
                                                   'ai_hypothesis', 'ensemble', 'coverage')),
    description_vi text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE method_versions (
    id             bigserial PRIMARY KEY,
    method_id      bigint NOT NULL REFERENCES methods(id),
    version        integer NOT NULL CHECK (version >= 1),
    params         jsonb NOT NULL DEFAULT '{}',
    code_git_sha   text NOT NULL,
    change_kind    text NOT NULL CHECK (change_kind IN ('initial', 'data', 'params', 'algorithm', 'knowledge')),
    change_note_vi text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    row_hash       text NOT NULL,
    prev_hash      text NOT NULL,
    UNIQUE (method_id, version)
);

CREATE TABLE backtests (
    id                bigserial PRIMARY KEY,
    method_version_id bigint NOT NULL REFERENCES method_versions(id),
    game              text NOT NULL REFERENCES games(code),
    split             text NOT NULL CHECK (split IN ('dev', 'validation', 'test')),
    from_draw_no      integer NOT NULL,
    to_draw_no        integer NOT NULL,
    tickets_per_draw  integer NOT NULL CHECK (tickets_per_draw >= 1),
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz,
    status            text NOT NULL CHECK (status IN ('finished', 'failed')),
    log_text          text NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (to_draw_no >= from_draw_no),
    UNIQUE (id, method_version_id, game, split)
);
-- Bộ kiểm thử cuối (test) chỉ được chạy MỘT lần cho mỗi phiên bản phương pháp và mỗi trò chơi.
CREATE UNIQUE INDEX backtests_test_once ON backtests (method_version_id, game) WHERE split = 'test';

CREATE TABLE prediction_runs (
    id                   bigserial PRIMARY KEY,
    method_version_id    bigint NOT NULL REFERENCES method_versions(id),
    game                 text NOT NULL REFERENCES games(code),
    target_draw_no       integer NOT NULL CHECK (target_draw_no > 0),
    purpose              text NOT NULL CHECK (purpose IN ('live', 'backtest')),
    split                text NOT NULL CHECK (split IN ('dev', 'validation', 'test', 'live')),
    data_cutoff_draw_no  integer NOT NULL CHECK (data_cutoff_draw_no >= 0),
    data_snapshot_sha256 text NOT NULL CHECK (data_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    random_seed          bigint NOT NULL,
    target_draw_at       timestamptz NOT NULL,
    backtest_id          bigint,
    created_at           timestamptz NOT NULL DEFAULT now(),
    row_hash             text NOT NULL,
    prev_hash            text NOT NULL,
    CHECK (data_cutoff_draw_no < target_draw_no),           -- chống rò rỉ tương lai
    CHECK ((purpose = 'live') = (split = 'live')),
    CHECK ((purpose = 'backtest') = (backtest_id IS NOT NULL)),
    -- Backtest phải cùng phương pháp, cùng trò chơi và cùng phần dữ liệu (split).
    FOREIGN KEY (backtest_id, method_version_id, game, split)
        REFERENCES backtests (id, method_version_id, game, split)
);
CREATE UNIQUE INDEX prediction_runs_live_once ON prediction_runs (method_version_id, game, target_draw_no)
    WHERE purpose = 'live';
CREATE UNIQUE INDEX prediction_runs_backtest_once ON prediction_runs (backtest_id, target_draw_no)
    WHERE purpose = 'backtest';

-- Dự đoán live: chỉ được tạo trước giờ quay theo lịch chính thức, khi kết quả chưa có.
CREATE OR REPLACE FUNCTION vlab_check_prediction_run() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    g     games%ROWTYPE;
    local timestamp;
BEGIN
    IF NEW.purpose = 'live' THEN
        SELECT * INTO g FROM games WHERE code = NEW.game;
        IF NEW.target_draw_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'Dự đoán live phải được tạo trước giờ quay (%)', NEW.target_draw_at
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.target_draw_at > clock_timestamp() + interval '8 days' THEN
            RAISE EXCEPTION 'Giờ quay mục tiêu quá xa' USING ERRCODE = 'check_violation';
        END IF;
        local := NEW.target_draw_at AT TIME ZONE 'Asia/Ho_Chi_Minh';
        IF NOT (extract(isodow FROM local)::smallint = ANY (g.draw_isodows)) OR local::time <> g.draw_time_ict THEN
            RAISE EXCEPTION 'Giờ quay mục tiêu % không khớp lịch chính thức của %', local, g.name_vi
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM draw_current WHERE game = NEW.game AND draw_no = NEW.target_draw_no
                   AND main_numbers IS NOT NULL) THEN
            RAISE EXCEPTION 'Kỳ % đã có kết quả — không thể tạo dự đoán live', NEW.target_draw_no
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_run BEFORE INSERT ON prediction_runs
    FOR EACH ROW EXECUTE FUNCTION vlab_check_prediction_run();

CREATE TABLE tickets (
    id             bigserial PRIMARY KEY,
    run_id         bigint NOT NULL REFERENCES prediction_runs(id),
    rank           integer NOT NULL CHECK (rank >= 1),
    main_numbers   smallint[] NOT NULL,
    explanation_vi text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    row_hash       text NOT NULL,
    prev_hash      text NOT NULL,
    UNIQUE (run_id, rank)
);
CREATE OR REPLACE FUNCTION vlab_check_ticket() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    r prediction_runs%ROWTYPE;
BEGIN
    SELECT * INTO r FROM prediction_runs WHERE id = NEW.run_id;
    -- Vé chỉ chứa 6 số chính; số đặc biệt Power 6/55 không được dự đoán, chỉ được đối chiếu.
    PERFORM vlab_check_numbers(r.game, NEW.main_numbers, NULL, false);
    IF NEW.main_numbers IS NULL THEN RAISE EXCEPTION 'Vé phải có 6 số chính'; END IF;
    -- Vé live phải được ghi trước giờ quay (cùng điều kiện với lượt dự đoán).
    IF r.purpose = 'live' AND clock_timestamp() >= r.target_draw_at THEN
        RAISE EXCEPTION 'Đã quá giờ quay — không thể thêm vé live' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_ticket BEFORE INSERT ON tickets
    FOR EACH ROW EXECUTE FUNCTION vlab_check_ticket();

CREATE TABLE ticket_results (
    id               bigserial PRIMARY KEY,
    ticket_id        bigint NOT NULL REFERENCES tickets(id),
    draw_version_id  bigint NOT NULL REFERENCES draw_versions(id),
    main_hits        smallint NOT NULL CHECK (main_hits BETWEEN 0 AND 6),
    bonus_hit        boolean,
    prize_tier       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticket_id, draw_version_id)
);

CREATE OR REPLACE FUNCTION vlab_check_ticket_result() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    r_game text; r_target integer; d_game text; d_no integer;
BEGIN
    SELECT r.game, r.target_draw_no INTO r_game, r_target
      FROM tickets t JOIN prediction_runs r ON r.id = t.run_id WHERE t.id = NEW.ticket_id;
    SELECT d.game, d.draw_no INTO d_game, d_no
      FROM draw_versions v JOIN draws d ON d.id = v.draw_id WHERE v.id = NEW.draw_version_id;
    IF r_game IS DISTINCT FROM d_game OR r_target IS DISTINCT FROM d_no THEN
        RAISE EXCEPTION 'Kết quả vé phải đối chiếu đúng kỳ mục tiêu của lượt dự đoán' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_ticket_result BEFORE INSERT ON ticket_results
    FOR EACH ROW EXECUTE FUNCTION vlab_check_ticket_result();

CREATE TABLE metrics (
    id                bigserial PRIMARY KEY,
    backtest_id       bigint REFERENCES backtests(id),
    method_version_id bigint NOT NULL REFERENCES method_versions(id),
    game              text NOT NULL REFERENCES games(code),
    split             text NOT NULL CHECK (split IN ('dev', 'validation', 'test', 'live')),
    window_label      text NOT NULL,
    n_draws           integer NOT NULL,
    n_tickets         integer NOT NULL,
    hits_hist         integer[] NOT NULL CHECK (cardinality(hits_hist) = 7),
    bonus_hits        integer,
    goal_any_3plus    integer NOT NULL,
    goal_any_4plus    integer NOT NULL,
    goal_any_5plus    integer NOT NULL,
    goal_any_6        integer NOT NULL,
    expected_random   jsonb NOT NULL,
    p_values          jsonb NOT NULL,
    p_values_adjusted jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prize_tables (
    id          bigserial PRIMARY KEY,
    game        text NOT NULL REFERENCES games(code),
    tier_code   text NOT NULL,
    rule_vi     text NOT NULL,
    amount_vnd  bigint,
    is_jackpot  boolean NOT NULL,
    valid_from  date NOT NULL,
    source_url  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- nghiên cứu và tri thức
CREATE TABLE hypotheses (
    id                bigserial PRIMARY KEY,
    title_vi          text NOT NULL,
    principle_vi      text NOT NULL,
    params            jsonb NOT NULL DEFAULT '{}',
    testable_claim_vi text NOT NULL,
    goal              text NOT NULL CHECK (goal IN ('3plus', '4plus', '5plus', 'jackpot')),
    created_by        text NOT NULL CHECK (created_by IN ('ai', 'user')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    row_hash          text NOT NULL,
    prev_hash         text NOT NULL
);
CREATE TABLE hypothesis_status_events (
    id            bigserial PRIMARY KEY,
    hypothesis_id bigint NOT NULL REFERENCES hypotheses(id),
    status        text NOT NULL CHECK (status IN ('proposed', 'testing', 'rejected', 'supported_on_validation', 'confirmed_on_test')),
    note_vi       text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE experiments (
    id                 bigserial PRIMARY KEY,
    hypothesis_id      bigint NOT NULL REFERENCES hypotheses(id),
    method_version_id  bigint NOT NULL REFERENCES method_versions(id),
    backtest_ids       bigint[] NOT NULL,
    result_summary_vi  text NOT NULL,
    overfitting_checks jsonb NOT NULL,
    conclusion_vi      text NOT NULL,
    succeeded          boolean NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    row_hash           text NOT NULL,
    prev_hash          text NOT NULL
);
-- Tài liệu tri thức là bản ghi bất biến; cập nhật = tài liệu mới với supersedes_id.
CREATE TABLE knowledge_docs (
    id              bigserial PRIMARY KEY,
    supersedes_id   bigint REFERENCES knowledge_docs(id),
    title           text NOT NULL,
    url             text,
    source_type     text NOT NULL CHECK (source_type IN ('official', 'paper', 'article', 'internal')),
    published_at    date,
    authority_score smallint NOT NULL CHECK (authority_score BETWEEN 0 AND 5),
    reproducible    boolean,
    summary_vi      text NOT NULL,
    content         text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    search_tsv      tsvector GENERATED ALWAYS AS
                    (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(summary_vi, '') || ' ' || coalesce(content, ''))) STORED
);
CREATE INDEX knowledge_docs_tsv ON knowledge_docs USING gin (search_tsv);

CREATE TABLE reports (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('post_draw', 'weekly', 'backtest', 'ingest')),
    game        text REFERENCES games(code),
    draw_no     integer,
    content_vi  text NOT NULL,
    data_refs   jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    row_hash    text NOT NULL,
    prev_hash   text NOT NULL
);

CREATE TABLE ai_journal (
    id           bigserial PRIMARY KEY,
    actor        text NOT NULL CHECK (actor IN ('job', 'ai', 'user')),
    action       text NOT NULL,
    change_kind  text NOT NULL CHECK (change_kind IN ('data', 'params', 'algorithm', 'knowledge', 'none')),
    details      jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    row_hash     text NOT NULL,
    prev_hash    text NOT NULL
);

-- Mọi thay đổi kết quả kỳ quay tự động được ghi vào nhật ký (không phụ thuộc mã ứng dụng).
CREATE OR REPLACE FUNCTION vlab_journal_version() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    d draws%ROWTYPE;
BEGIN
    SELECT * INTO d FROM draws WHERE id = NEW.draw_id;
    INSERT INTO ai_journal (actor, action, change_kind, details, row_hash, prev_hash)
    VALUES (CASE WHEN NEW.created_by LIKE 'user:%' THEN 'user' ELSE 'job' END, 'draw_version', 'data',  -- user:… đã được kiểm tra quyền ở check_version
            jsonb_build_object('game', d.game, 'draw_no', d.draw_no, 'version_no', NEW.version_no,
                               'status', NEW.status, 'version_id', NEW.id, 'reason_vi', NEW.reason_vi,
                               'db_user', NEW.created_db_user), '', '');
    RETURN NULL;
END;
$$;
CREATE TRIGGER journal_version AFTER INSERT ON draw_versions
    FOR EACH ROW EXECUTE FUNCTION vlab_journal_version();

CREATE TABLE settings_history (
    id          bigserial PRIMARY KEY,
    key         text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_.]*$'),
    old_value   jsonb,
    new_value   jsonb NOT NULL,
    changed_by  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE VIEW settings_current AS
SELECT DISTINCT ON (key) key, new_value AS value, changed_by, created_at AS changed_at
FROM settings_history ORDER BY key, id DESC;

CREATE TABLE audit_log (
    id         bigserial PRIMARY KEY,
    actor      text NOT NULL,
    action     text NOT NULL,
    details    jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    row_hash   text NOT NULL,
    prev_hash  text NOT NULL
);

CREATE TABLE backups (
    id                bigserial PRIMARY KEY,
    location          text NOT NULL,
    size_bytes        bigint NOT NULL CHECK (size_bytes > 0),
    sha256            text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE backup_restore_tests (
    id          bigserial PRIMARY KEY,
    backup_id   bigint NOT NULL REFERENCES backups(id),
    ok          boolean NOT NULL,
    details_vi  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Điểm kiểm tra: số dòng, id lớn nhất và băm toàn bộ nội dung của MỌI bảng lịch sử.
-- Phát hiện cả việc xóa dòng cuối (điều chuỗi băm không làm được). Bản sao JSON được lưu cùng bản sao lưu
-- ở kho GitHub riêng, nên kể cả chủ sở hữu CSDL cũng không thể sửa lịch sử mà không bị phát hiện.
CREATE TABLE chain_checkpoints (
    id          bigserial PRIMARY KEY,
    tables      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    row_hash    text NOT NULL,
    prev_hash   text NOT NULL
);

-- ---------------------------------------------------------------- áp dụng bất biến
SELECT vlab_make_append_only(t) FROM unnest(ARRAY[
    'job_runs', 'fetch_attempts', 'draw_observations', 'draws', 'draw_versions', 'draw_conflicts',
    'draw_conflict_resolutions', 'methods', 'method_versions', 'backtests', 'prediction_runs', 'tickets',
    'ticket_results', 'metrics', 'prize_tables', 'hypotheses', 'hypothesis_status_events', 'experiments',
    'knowledge_docs', 'reports', 'ai_journal', 'settings_history', 'audit_log', 'backups', 'backup_restore_tests',
    'chain_checkpoints'
]::regclass[]) AS t;

SELECT vlab_make_timestamped(t) FROM unnest(ARRAY[
    'job_runs', 'fetch_attempts', 'draw_observations', 'draws', 'draw_versions', 'draw_conflicts',
    'draw_conflict_resolutions', 'methods', 'method_versions', 'backtests', 'prediction_runs', 'tickets',
    'ticket_results', 'metrics', 'prize_tables', 'hypotheses', 'hypothesis_status_events', 'experiments',
    'knowledge_docs', 'reports', 'ai_journal', 'settings_history', 'audit_log', 'backups', 'backup_restore_tests',
    'chain_checkpoints'
]::regclass[]) AS t;

SELECT vlab_make_chained(t) FROM unnest(ARRAY[
    'draw_versions', 'method_versions', 'prediction_runs', 'tickets', 'hypotheses', 'experiments',
    'reports', 'ai_journal', 'audit_log', 'chain_checkpoints'
]::regclass[]) AS t;

CREATE OR REPLACE FUNCTION vlab_history_tables() RETURNS regclass[]
LANGUAGE sql IMMUTABLE SET search_path = public, pg_temp AS $$
    SELECT ARRAY[
        'job_runs', 'fetch_attempts', 'draw_observations', 'draws', 'draw_versions', 'draw_conflicts',
        'draw_conflict_resolutions', 'methods', 'method_versions', 'backtests', 'prediction_runs', 'tickets',
        'ticket_results', 'metrics', 'prize_tables', 'hypotheses', 'hypothesis_status_events', 'experiments',
        'knowledge_docs', 'reports', 'ai_journal', 'settings_history', 'audit_log', 'backups', 'backup_restore_tests'
    ]::regclass[]
$$;

-- Băm nội dung các dòng id <= upto (theo thứ tự id, giờ UTC).
-- SECURITY DEFINER để vai trò đọc/ghi hẹp vẫn kiểm tra được MỌI bảng lịch sử mà không cần quyền SELECT trên
-- chúng; chỉ trả về số dòng và băm (không lộ nội dung) và chỉ cho các bảng trong vlab_history_tables().
CREATE OR REPLACE FUNCTION vlab_table_digest(tbl regclass, upto bigint)
RETURNS TABLE (n bigint, sha256 text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET TimeZone = 'UTC' SET search_path = public, pg_temp AS $$
BEGIN
    IF NOT (tbl = ANY (vlab_history_tables())) THEN
        RAISE EXCEPTION 'Không phải bảng lịch sử: %', tbl USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY EXECUTE format(
        'SELECT count(*), encode(digest(coalesce(string_agg(to_jsonb(t)::text, E''\n'' ORDER BY id), ''''), ''sha256''), ''hex'')
           FROM %s t WHERE id <= $1', tbl) USING upto;
END;
$$;

-- SECURITY DEFINER: cần khóa SHARE (vlab_app không có quyền đó) để có ảnh chụp nhất quán. Không có tham số,
-- search_path cố định, chỉ ghi thêm vào chain_checkpoints → an toàn khi chạy với quyền chủ sở hữu.
CREATE OR REPLACE FUNCTION vlab_make_checkpoint() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET TimeZone = 'UTC' SET search_path = public, pg_temp AS $$
DECLARE
    t       regclass;
    max_id  bigint;
    d       record;
    result  jsonb := '{}';
    cp_id   bigint;
BEGIN
    FOREACH t IN ARRAY vlab_history_tables() LOOP
        EXECUTE format('LOCK TABLE %s IN SHARE MODE', t);
        EXECUTE format('SELECT coalesce(max(id), 0) FROM %s', t) INTO max_id;
        SELECT * INTO d FROM vlab_table_digest(t, max_id);
        result := result || jsonb_build_object(t::text, jsonb_build_object('max_id', max_id, 'n', d.n, 'sha256', d.sha256));
    END LOOP;
    INSERT INTO chain_checkpoints (tables, row_hash, prev_hash) VALUES (result, '', '') RETURNING id INTO cp_id;
    RETURN jsonb_build_object('checkpoint_id', cp_id, 'tables', result);
END;
$$;

-- So sánh CSDL hiện tại với một điểm kiểm tra (lấy từ bảng hoặc từ tệp JSON bên ngoài). Rỗng = khớp.
CREATE OR REPLACE FUNCTION vlab_verify_checkpoint(cp jsonb)
RETURNS TABLE (table_name text, problem text)
LANGUAGE plpgsql STABLE SET TimeZone = 'UTC' SET search_path = public, pg_temp AS $$
DECLARE
    k        text;
    v        jsonb;
    d        record;
    expected text[];
    given    text[];
    stored   jsonb;
BEGIN
    -- 0. Hình dạng tệp: lỗi định dạng trả về như một sai lệch, không làm lệnh kiểm tra bị hỏng.
    IF jsonb_typeof(cp) IS DISTINCT FROM 'object' OR jsonb_typeof(cp -> 'tables') IS DISTINCT FROM 'object'
       OR jsonb_typeof(cp -> 'checkpoint_id') IS DISTINCT FROM 'number'
       OR coalesce(cp ->> 'checkpoint_id', '') !~ '^[1-9][0-9]{0,17}$'
       OR EXISTS (SELECT 1 FROM jsonb_each(cp -> 'tables') e
                  WHERE jsonb_typeof(e.value) IS DISTINCT FROM 'object'
                     OR jsonb_typeof(e.value -> 'max_id') IS DISTINCT FROM 'number'
                     OR jsonb_typeof(e.value -> 'n') IS DISTINCT FROM 'number'
                     OR jsonb_typeof(e.value -> 'sha256') IS DISTINCT FROM 'string'
                     OR coalesce(e.value ->> 'max_id', '') !~ '^(0|[1-9][0-9]{0,17})$'
                     OR coalesce(e.value ->> 'n', '') !~ '^(0|[1-9][0-9]{0,17})$'
                     OR coalesce(e.value ->> 'sha256', '') !~ '^[0-9a-f]{64}$') THEN
        table_name := '*'; problem := 'tệp điểm kiểm tra sai định dạng'; RETURN NEXT; RETURN;
    END IF;
    -- 1. Tệp phải liệt kê ĐÚNG tập bảng lịch sử, không thiếu không thừa.
    SELECT array_agg(x::text ORDER BY x::text) INTO expected FROM unnest(vlab_history_tables()) x;
    SELECT array_agg(key ORDER BY key) INTO given FROM jsonb_object_keys(coalesce(cp -> 'tables', '{}')) key;
    IF given IS DISTINCT FROM expected THEN
        table_name := '*'; problem := 'điểm kiểm tra không liệt kê đúng tập bảng lịch sử'; RETURN NEXT; RETURN;
    END IF;
    -- 2. Điểm kiểm tra phải còn trong CSDL với nội dung y hệt (xóa/sửa chain_checkpoints bị phát hiện).
    SELECT tables INTO stored FROM chain_checkpoints WHERE id = (cp ->> 'checkpoint_id')::bigint;
    IF stored IS NULL THEN
        table_name := 'chain_checkpoints'; problem := 'không tìm thấy điểm kiểm tra trong CSDL'; RETURN NEXT;
    ELSIF stored <> cp -> 'tables' THEN
        table_name := 'chain_checkpoints'; problem := 'điểm kiểm tra trong CSDL khác với bản bên ngoài'; RETURN NEXT;
    END IF;
    -- 3. Từng bảng: số dòng và băm nội dung đến max_id phải khớp.
    FOR k, v IN SELECT * FROM jsonb_each(cp -> 'tables') LOOP
        SELECT * INTO d FROM vlab_table_digest(k::regclass, (v ->> 'max_id')::bigint);
        IF d.n <> (v ->> 'n')::bigint THEN
            table_name := k; problem := format('số dòng %s ≠ %s (dòng bị xóa hoặc chèn)', d.n, v ->> 'n'); RETURN NEXT;
        ELSIF d.sha256 <> v ->> 'sha256' THEN
            table_name := k; problem := 'nội dung đã bị thay đổi'; RETURN NEXT;
        END IF;
    END LOOP;
END;
$$;

-- Kiểm tra toàn vẹn chuỗi băm: trả về các dòng sai (rỗng = toàn vẹn).
-- SECURITY DEFINER với cùng giới hạn: chỉ các bảng có chuỗi băm; chỉ trả về id dòng sai.
CREATE OR REPLACE FUNCTION vlab_verify_chain(tbl regclass)
RETURNS TABLE (bad_id bigint, problem text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET TimeZone = 'UTC' SET search_path = public, pg_temp AS $$
DECLARE
    r        record;
    expected text;
    prev     text := repeat('0', 64);
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = tbl AND tgname = 'zz_hash_chain') THEN
        RAISE EXCEPTION 'Bảng % không có chuỗi băm', tbl USING ERRCODE = 'insufficient_privilege';
    END IF;
    FOR r IN EXECUTE format('SELECT id, row_hash, prev_hash, to_jsonb(t) - ''row_hash'' - ''prev_hash'' AS payload FROM %s t ORDER BY id', tbl) LOOP
        IF r.prev_hash <> prev THEN
            bad_id := r.id; problem := 'prev_hash không khớp dòng trước (dòng bị xóa hoặc chèn)'; RETURN NEXT;
        END IF;
        expected := encode(digest(r.prev_hash || r.payload::text, 'sha256'), 'hex');
        IF r.row_hash <> expected THEN
            bad_id := r.id; problem := 'row_hash sai (nội dung đã bị thay đổi)'; RETURN NEXT;
        END IF;
        prev := r.row_hash;
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------- quyền (tối thiểu)
-- vlab_web: chỉ đọc dữ liệu giai đoạn 1. vlab_app (tác vụ tự động): đọc + chỉ ghi thêm vào bảng giai đoạn 1.
-- vlab_admin (người duyệt): như vlab_app + giải quyết xung đột, cài đặt, nhật ký kiểm toán.
-- Bảng của giai đoạn sau (phương pháp, dự đoán, tri thức…) không cấp quyền nào ở đây — migration sau sẽ cấp.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO vlab_app, vlab_web, vlab_admin;
GRANT SELECT ON games, sources, job_runs, fetch_attempts, draw_observations, draws, draw_versions,
                draw_conflicts, draw_conflict_resolutions, draw_current, draws_usable, ai_journal, reports,
                chain_checkpoints, backups, backup_restore_tests, settings_history, settings_current
             TO vlab_app, vlab_web, vlab_admin;
GRANT SELECT ON audit_log TO vlab_admin;
-- chain_checkpoints: KHÔNG cấp INSERT; chỉ tạo qua vlab_make_checkpoint() (SECURITY DEFINER).
GRANT INSERT ON job_runs, fetch_attempts, draw_observations, draws, draw_versions, draw_conflicts,
                ai_journal, reports, backups, backup_restore_tests TO vlab_app;
GRANT USAGE ON SEQUENCE job_runs_id_seq, fetch_attempts_id_seq, draw_observations_id_seq, draws_id_seq,
                draw_versions_id_seq, draw_conflicts_id_seq, ai_journal_id_seq, reports_id_seq,
                backups_id_seq, backup_restore_tests_id_seq TO vlab_app;
GRANT vlab_app TO vlab_admin;
GRANT INSERT ON draw_conflict_resolutions, settings_history, audit_log TO vlab_admin;
GRANT USAGE ON SEQUENCE draw_conflict_resolutions_id_seq, settings_history_id_seq, audit_log_id_seq TO vlab_admin;

-- Hàm của dự án: thu hồi quyền gọi mặc định của PUBLIC (chỉ hàm vlab_* do chính vai trò migration sở hữu,
-- không đụng tới hàm của extension như pgcrypto — trên Neon chúng có thể thuộc vai trò khác).
-- Hàm trigger không cần EXECUTE lúc chạy; hàm được trigger gọi lại bằng PERFORM thì cần → cấp đích danh.
DO $$
DECLARE
    f regprocedure;
BEGIN
    FOR f IN SELECT p.oid::regprocedure FROM pg_proc p
             WHERE p.pronamespace = 'public'::regnamespace AND p.proname LIKE 'vlab\_%'
               AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
    END LOOP;
END;
$$;
GRANT EXECUTE ON FUNCTION vlab_check_numbers(text, smallint[], smallint, boolean),
    vlab_check_observation_ids(bigint, bigint[]), vlab_make_checkpoint() TO vlab_app;
GRANT EXECUTE ON FUNCTION vlab_history_tables(), vlab_table_digest(regclass, bigint),
    vlab_verify_checkpoint(jsonb), vlab_verify_chain(regclass) TO vlab_app, vlab_web;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

INSERT INTO schema_migrations (version) VALUES (1);
