KEEP = {"test_verified_change_blocked_for_app_role", "test_observation_requires_ok_fetch_of_same_source", "test_identical_observation_not_duplicated"}


def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if i.name in KEEP]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
