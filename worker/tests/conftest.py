KEEP = {"test_resolution_must_match_conflict_draw", "test_verified_time_change_becomes_conflict", "test_database_rejects_invalid_numbers"}


def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if i.name in KEEP]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
