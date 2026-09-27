from inspect_mem0 import inspect_installed


def test_installed_mem0_behavior_is_explicitly_audited():
    report = inspect_installed()
    assert report["mem0ai_version"] == "2.2.0"
    assert report["mongodb_filter_is_post_vector_search"] is True
    assert report["update_accepts_user_scope"] is False
    assert report["delete_accepts_user_scope"] is False
    assert report["history_accepts_user_scope"] is False
    assert report["history_uses_local_history_db"] is True


def test_mem0_history_storage_lifecycle_retention():
    from test_mem0_history_audit import demonstrate_mem0_history_lifecycle

    report = demonstrate_mem0_history_lifecycle()
    assert report["findings"]["deleted_text_persists_in_sqlite"] is True
    assert report["findings"]["history_row_count_after_deletion"] == 3
    # Check that deleted memory text is still present in historical rows
    old_memories = [r["old_memory"] for r in report["rows_after_delete"] if r["old_memory"]]
    assert "The project uses MongoDB." in old_memories
    assert "The project uses PostgreSQL." in old_memories
