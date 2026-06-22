from pathlib import Path

from app.models.rag import ChildDocument, ParentDocument
from app.services.rag_document_store import RagDocumentStore


def test_docstore_upsert_get_and_delete_by_source(tmp_path: Path):
    store = RagDocumentStore(tmp_path)
    parent = ParentDocument(
        parent_id="p1",
        source="/tmp/a.md",
        file_name="a.md",
        title_path=["A"],
        content="parent content",
        content_hash="h1",
        metadata={"h1": "A"},
    )
    child = ChildDocument(
        child_id="c1",
        parent_id="p1",
        source="/tmp/a.md",
        file_name="a.md",
        title_path=["A"],
        content="child content",
        content_hash="h2",
        chunk_index=0,
        metadata={"parent_id": "p1", "child_id": "c1"},
    )

    store.upsert_documents([parent], [child])

    assert store.get_parent("p1") == parent
    assert store.get_child("c1") == child
    assert store.manifest_path.exists()

    deleted = store.delete_by_source("/tmp/a.md")

    assert deleted == 2
    assert store.list_parents() == []
    assert store.list_children() == []
