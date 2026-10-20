KEEP = {"test_checkpoint_json_must_be_complete_and_present", "test_repeated_runs_keep_single_pending", "test_spoofing_and_schedule_rules"}


def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if i.name in KEEP]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
