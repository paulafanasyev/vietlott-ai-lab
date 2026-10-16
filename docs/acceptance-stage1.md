# Приёмка этапа 1 — доказательства

Правило: пункт считается выполненным только при наличии доказательства (тест, лог запуска, файл).
«Код написан» — не доказательство работы на реальных данных.

## Что проверено сейчас

| Требование | Где реализовано | Доказательство | Статус |
|---|---|---|---|
| Правила игр (6/45 без доп. числа, 6/55 + доп. число), строгие числа и даты | `worker/vlab/games.py`, `models.py`, функция `vlab_check_numbers` | `tests/test_games.py`, `tests/test_parsers.py` | юнит-тесты проходят |
| Парсеры официального списка и трёх архивов, отбраковка с причиной, дубли | `worker/vlab/sources/*` | `tests/test_parsers.py` (BOM, мусорные строки, float/bool, двойной номер, неоднозначный номер тиража) | юнит-тесты проходят |
| Безопасный HTTP: белый список, проверка каждого редиректа, лимит 25 МБ, Retry-After ≤ 300 с, журнал попыток | `worker/vlab/sources/http.py` | `tests/test_http.py` | юнит-тесты проходят |
| Статусы VERIFIED / CROSS-CHECKED / NOT PROVEN / CONFLICT / PENDING, группы происхождения | `worker/vlab/reconcile.py` | `tests/test_reconcile.py` | юнит-тесты проходят |
| Расписание по времени Вьетнама; PENDING только следующему тиражу и только на день/час по расписанию | `worker/vlab/schedule.py`, `ingest.py`, триггер `check_version` | `tests/test_schedule.py`, `test_ingest.py::test_naive_datetimes_rejected`, `test_db.py::test_pending_only_next_draw`, `test_repeated_runs_keep_single_pending`, `test_spoofing_and_schedule_rules` | юнит: да; БД: PENDING (CI) |
| Неизменяемая история, версии с причиной, конфликты исключаются из расчётов; каждое наблюдение привязано к успешной загрузке того же источника и той же игры с хешем; версии и конфликты ссылаются только на реальные наблюдения этого тиража | `db/migrations/001_init.sql` | `tests/test_db.py` (в т.ч. `test_observation_must_match_fetch_game`, `test_conflict_rows_need_real_evidence`) | PENDING — нужен запуск CI с PostgreSQL |
| VERIFIED нельзя тихо заменить (только CONFLICT; смена — роль `vlab_admin`) | триггер `vlab_check_draw_version`, `store.reconcile_game` | `test_db.py::test_verified_change_blocked_for_app_role`, `test_verified_time_change_becomes_conflict` | PENDING (CI) |
| Минимальные права ролей (воркер не может писать контрольные точки напрямую, вызывать служебные функции, выдавать себя за пользователя) | раздел «quyền» миграции | `test_db.py::test_app_role_can_run_whole_stage1_flow`, `test_spoofing_and_schedule_rules` | PENDING (CI) |
| Обнаружение подмены (цепочка хешей + контрольные точки всех таблиц, в т.ч. удаление последней строки) | `vlab_verify_chain`, `vlab_make_checkpoint`, `vlab_verify_checkpoint` | `test_db.py::test_tampering_detected_by_hash_chain`, `test_checkpoint_detects_tail_delete_and_edit` | PENDING (CI) |
| Журнал попыток доступа переживает откат задачи | `Store` (отдельное autocommit-соединение) | `test_ingest.py::test_audit_must_be_autocommit`, `test_db.py::test_ingest_end_to_end_with_fixtures` | частично; PENDING (CI) |
| Экспорт CSV/JSON: тиражи, история версий, наблюдения с доказательствами, конфликты | `store.export_rows`, `python -m vlab export` | `test_db.py::test_settings_history_and_export`; артефакт `ci-evidence` | PENDING (CI) |
| Журнал ИИ/задач и история настроек | `ai_journal` (+ триггер на каждую версию), `settings_history` | `test_db.py` | PENDING (CI) |
| Бэкап в приватный GitHub + автоматическая проверка восстановления | `docs/workflows/backup.yml`, `record-backup` | job `backup-restore-smoke` в CI; первый ночной запуск backup | PENDING |
| Автоматический сбор по расписанию с журналом задач | `docs/workflows/ingest.yml`, `job_runs`, `reports` | первые запуски ingest | PENDING |
| Реальные источники (vietlott.vn, три архива) | `ingest.py` | лог первого ingest: сколько VERIFIED/CROSS-CHECKED, есть ли блокировка IP | PENDING |
| Пользовательский текст на вьетнамском | отчёты, причины, ошибки, CLI | `test_ingest.py::test_summary_is_vietnamese_and_flags_stale_official` | для этапа 1 да; интерфейса ещё нет |
| Секреты не в коде | только переменные окружения / GitHub Secrets | просмотр кода | да |

Последний локальный прогон (песочница без PostgreSQL и интернета):
`python -m unittest discover -s tests` → 93 теста, 72 прошли, 21 пропущен (все пропуски — тесты PostgreSQL). Дата прогона: 17.10.2026.

## Что сделать Павлу, чтобы закрыть PENDING

1. Скопировать `docs/workflows/ci.yml`, `ingest.yml`, `backup.yml` в `.github/workflows/`.
2. Создать базу Neon, добавить секреты `DATABASE_URL`, `DATABASE_URL_WORKER`, `BACKUP_REPO`, `BACKUP_REPO_TOKEN`.
3. Запустить по очереди: ci → ingest → backup. Ссылки на запуски вписать в таблицу ниже.

| Доказательство | Ссылка на запуск | Результат |
|---|---|---|
| CI: все тесты на PostgreSQL | — | PENDING |
| CI: дамп → восстановление → сверка | — | PENDING |
| Первый ingest на Neon | — | PENDING |
| `python -m vlab verify` после ingest | — | PENDING |
| Первый ночной backup + restore | — | PENDING |

## Осознанные решения

- `fetch_attempts.job_run_id` без внешнего ключа: попытки пишутся отдельным autocommit-соединением сразу,
  а строка `job_runs` — в конце задачи (таблицы только дописываются, поэтому «открыть, потом закрыть» задачу
  одной строкой нельзя). Связь проверяется экспортом и тестами, а не FK.
- PENDING ставится только следующему номеру после последнего тиража с результатом: номера Vietlott растут
  только при реальном тираже (праздники, Тет), поэтому номера дальше угадывать нельзя.
- Сбор запускается ежедневно, а не только в игровые дни: ловит поздние публикации и исправления архивов.
- Суперпользователь PostgreSQL технически может обойти любые триггеры; от этого защищают цепочки хешей,
  контрольные точки всех таблиц и их копии в приватном репозитории бэкапов.
