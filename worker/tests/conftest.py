KEEP = {"test_ingest_end_to_end_with_fixtures", "test_observation_must_match_fetch_game", "test_conflict_rows_need_real_evidence"}


def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if i.name in KEEP]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
