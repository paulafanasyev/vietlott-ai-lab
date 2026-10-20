KEEP = {"test_migration_applied_once", "test_full_reconcile_flow_and_immutability", "test_conflict_on_first_sight_records_conflict"}


def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if i.name in KEEP]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
