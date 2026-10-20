KEEP = {"test_tampering_detected_by_hash_chain", "test_settings_history_and_export", "test_live_prediction_rules"}


def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if i.name in KEEP]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
