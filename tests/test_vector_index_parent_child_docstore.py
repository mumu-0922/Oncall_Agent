import json
from pathlib import Path

from app.services import vector_index_service as vector_index_module
from app.services.rag_document_store import RagDocumentStore


class DummyVectorStoreManager:
    def __init__(self) -> None:
        self.added_count = 0
        self.last_ids: list[str] | None = None

    def delete_by_source(self, file_path: str) -> int:
        return 0

    def add_documents(self, documents, ids=None):
        assert ids is not None
        assert len(documents) == len(ids)
        self.added_count = len(documents)
        self.last_ids = ids
        return ids


def test_index_single_file_generates_parent_child_docstore(monkeypatch, tmp_path: Path):
    store = RagDocumentStore(tmp_path / "rag")
    dummy_vector_store = DummyVectorStoreManager()
    monkeypatch.setattr(vector_index_module, "rag_document_store", store)
    monkeypatch.setattr(vector_index_module, "vector_store_manager", dummy_vector_store)

    vector_index_module.vector_index_service.index_single_file("aiops-docs/cpu_high_usage.md")

    parents = store.list_parents()
    children = store.list_children()
    assert parents
    assert children
    assert store.parents_path.exists()
    assert store.children_path.exists()
    assert store.manifest_path.exists()
    assert dummy_vector_store.added_count == len(children)
    assert dummy_vector_store.last_ids == [child.child_id for child in children]

    manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    assert manifest["parent_count"] == len(parents)
    assert manifest["child_count"] == len(children)
    assert manifest["documents"][0]["file_name"] == "cpu_high_usage.md"
