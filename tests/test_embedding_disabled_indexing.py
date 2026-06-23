from pathlib import Path

from app.services import vector_index_service as vector_index_module
from app.services.rag_document_store import RagDocumentStore
from app.services.vector_embedding_service import EmbeddingDisabledError


class DisabledVectorStoreManager:
    def __init__(self) -> None:
        self.add_attempts = 0

    def delete_by_source(self, file_path: str) -> int:
        return 0

    def add_documents(self, documents, ids=None):
        self.add_attempts += 1
        raise EmbeddingDisabledError("embedding disabled in test")


class DummyBM25:
    def __init__(self) -> None:
        self.rebuild_count = 0

    def rebuild_index(self):
        self.rebuild_count += 1


def test_index_single_file_succeeds_when_dense_embedding_disabled(monkeypatch, tmp_path: Path):
    store = RagDocumentStore(tmp_path / "rag")
    disabled_vector_store = DisabledVectorStoreManager()
    dummy_bm25 = DummyBM25()
    monkeypatch.setattr(vector_index_module, "rag_document_store", store)
    monkeypatch.setattr(vector_index_module, "vector_store_manager", disabled_vector_store)
    monkeypatch.setattr(vector_index_module, "bm25_retrieval_service", dummy_bm25)

    vector_index_module.vector_index_service.index_single_file("aiops-docs/cpu_high_usage.md")

    assert store.list_parents()
    assert store.list_children()
    assert disabled_vector_store.add_attempts == 1
    assert dummy_bm25.rebuild_count == 1
