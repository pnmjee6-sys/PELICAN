from __future__ import annotations

import pytest

from memory_manager import MemoryManager


class FakeMemory:
    def __init__(self, owner="alice"):
        self.owner = owner
        self.updated = False
        self.deleted = False

    def get(self, memory_id):
        return {"id": memory_id, "user_id": self.owner, "memory": "fact"}

    def update(self, memory_id, text):
        self.updated = True
        return {"message": "ok"}

    def delete(self, memory_id):
        self.deleted = True
        return {"message": "ok"}


def manager_with_fake() -> MemoryManager:
    manager = object.__new__(MemoryManager)
    manager.memory = FakeMemory()
    return manager


def test_cross_user_update_is_blocked_before_mem0_call():
    manager = manager_with_fake()
    with pytest.raises(PermissionError):
        manager.update("m1", "bob", text="changed")
    assert not manager.memory.updated


def test_owner_can_delete():
    manager = manager_with_fake()
    manager.delete("m1", "alice")
    assert manager.memory.deleted
