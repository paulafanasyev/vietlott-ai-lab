"""Safe preflight checks for the least-privilege worker database connection."""

from __future__ import annotations

import os
import sys


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("PREFLIGHT FAILED: DATABASE_URL is empty. Configure the required GitHub Actions secret.", file=sys.stderr)
        return 2

    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT current_user,
                           pg_has_role(current_user, 'vlab_app', 'member'),
                           to_regclass('public.schema_migrations') IS NOT NULL,
                           to_regclass('public.job_runs') IS NOT NULL
                    """
                )
                role, is_app_member, has_migrations, has_job_runs = cur.fetchone()
                if not is_app_member:
                    print(
                        f"PREFLIGHT FAILED: connected as role {role!r}, which is not a member of vlab_app. "
                        "Create the dedicated worker login with IN ROLE vlab_app and update DATABASE_URL_WORKER.",
                        file=sys.stderr,
                    )
                    return 3
                if not has_migrations or not has_job_runs:
                    print(
                        "PREFLIGHT FAILED: expected database schema is missing. "
                        "Run the owner migration step successfully before worker tasks.",
                        file=sys.stderr,
                    )
                    return 4
                cur.execute(
                    "SELECT has_table_privilege(current_user, 'public.job_runs', 'INSERT'), "
                    "has_sequence_privilege(current_user, 'public.job_runs_id_seq', 'USAGE')"
                )
                can_insert, can_use_sequence = cur.fetchone()
                if not can_insert or not can_use_sequence:
                    print(
                        "PREFLIGHT FAILED: worker role lacks INSERT on public.job_runs or USAGE on "
                        "public.job_runs_id_seq. Re-run the database migration as the owner.",
                        file=sys.stderr,
                    )
                    return 5
                print(f"PREFLIGHT OK: connected as {role}; vlab_app membership and required schema privileges verified.")
                return 0
    except Exception as exc:
        # Do not print the connection URL or exception parameters that might contain credentials.
        print(
            f"PREFLIGHT FAILED: {type(exc).__name__}: database connection/check failed. "
            "Confirm DATABASE_URL_WORKER is set to the dedicated worker connection string and that Neon is reachable.",
            file=sys.stderr,
        )
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
