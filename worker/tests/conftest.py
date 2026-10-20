def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if "test_db" not in i.nodeid]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
