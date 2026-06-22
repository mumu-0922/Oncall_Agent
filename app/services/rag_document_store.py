"""Local JSONL document store for parent-child RAG."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from loguru import logger

from app.config import config
from app.models.rag import ChildDocument, ParentDocument


class RagDocumentStore:
    """Persist parent and child chunks for BM25 and parent expansion."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir or config.rag_docstore_dir)
        self.parents_path = self.root_dir / "parents.jsonl"
        self.children_path = self.root_dir / "children.jsonl"
        self.manifest_path = self.root_dir / "index_manifest.json"

    def ensure_dir(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def upsert_documents(
        self,
        parents: Iterable[ParentDocument],
        children: Iterable[ChildDocument],
    ) -> None:
        """Upsert parent/child chunks by id."""
        self.ensure_dir()
        parent_map = {item.parent_id: item for item in self.list_parents()}
        child_map = {item.child_id: item for item in self.list_children()}

        for parent in parents:
            parent_map[parent.parent_id] = parent
        for child in children:
            child_map[child.child_id] = child

        self._write_jsonl(self.parents_path, parent_map.values())
        self._write_jsonl(self.children_path, child_map.values())
        self._write_manifest(parent_map.values(), child_map.values())
        logger.info("Docstore upsert 完成 parents={}, children={}", len(parent_map), len(child_map))

    def delete_by_source(self, source: str) -> int:
        """Delete all chunks for a source path."""
        self.ensure_dir()
        parents = [item for item in self.list_parents() if item.source != source]
        children = [item for item in self.list_children() if item.source != source]
        old_count = len(self.list_parents()) + len(self.list_children())
        new_count = len(parents) + len(children)
        self._write_jsonl(self.parents_path, parents)
        self._write_jsonl(self.children_path, children)
        self._write_manifest(parents, children)
        deleted = old_count - new_count
        logger.info("Docstore 删除 source={} deleted={}", source, deleted)
        return deleted

    def get_parent(self, parent_id: str) -> ParentDocument | None:
        for parent in self.list_parents():
            if parent.parent_id == parent_id:
                return parent
        return None

    def get_child(self, child_id: str) -> ChildDocument | None:
        for child in self.list_children():
            if child.child_id == child_id:
                return child
        return None

    def list_parents(self) -> list[ParentDocument]:
        return [ParentDocument.model_validate(item) for item in self._read_jsonl(self.parents_path)]

    def list_children(self) -> list[ChildDocument]:
        return [ChildDocument.model_validate(item) for item in self._read_jsonl(self.children_path)]

    def _read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def _write_jsonl(self, path: Path, rows: Iterable[ParentDocument | ChildDocument]) -> None:
        self.ensure_dir()
        with path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(row.model_dump_json() + "\n")

    def _write_manifest(
        self,
        parents: Iterable[ParentDocument],
        children: Iterable[ChildDocument],
    ) -> None:
        from datetime import datetime

        parent_list = list(parents)
        child_list = list(children)
        sources: dict[str, dict] = {}
        for parent in parent_list:
            sources.setdefault(
                parent.source,
                {
                    "source": parent.source,
                    "file_name": parent.file_name,
                    "parent_count": 0,
                    "child_count": 0,
                },
            )["parent_count"] += 1
        for child in child_list:
            sources.setdefault(
                child.source,
                {
                    "source": child.source,
                    "file_name": child.file_name,
                    "parent_count": 0,
                    "child_count": 0,
                },
            )["child_count"] += 1

        manifest = {
            "version": "1",
            "updated_at": datetime.now().isoformat(),
            "parent_count": len(parent_list),
            "child_count": len(child_list),
            "documents": sorted(sources.values(), key=lambda item: item["source"]),
        }
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


rag_document_store = RagDocumentStore()
