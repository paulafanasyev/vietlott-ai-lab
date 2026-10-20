KEEP = {"test_pending_only_next_draw", "test_app_role_can_run_whole_stage1_flow", "test_checkpoint_detects_tail_delete_and_edit"}


def pytest_collection_modifyitems(config, items):
    keep = [i for i in items if i.name in KEEP]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep
