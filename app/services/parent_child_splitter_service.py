"""Parent-child document splitting for hybrid RAG."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger

from app.config import config
from app.models.rag import ChildDocument, ParentDocument


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"\s+", "-", value.strip())
    normalized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", normalized)
    return normalized.strip("-") or "untitled"


@dataclass(frozen=True)
class SplitResult:
    parents: list[ParentDocument]
    children: list[ChildDocument]


class ParentChildSplitterService:
    """Split source text into parent context blocks and child retrieval blocks."""

    def __init__(self) -> None:
        self.parent_max_chars = config.rag_parent_max_chars
        self.child_chunk_size = config.rag_child_chunk_size
        self.child_chunk_overlap = config.rag_child_chunk_overlap
        self.markdown_parent_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#", "h1"), ("##", "h2")],
            strip_headers=False,
        )
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.child_chunk_size,
            chunk_overlap=self.child_chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "；", ";", "，", ",", " ", ""],
            is_separator_regex=False,
        )
        self.parent_overflow_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.parent_max_chars,
            chunk_overlap=min(200, self.child_chunk_overlap * 2),
            length_function=len,
            separators=["\n### ", "\n\n", "\n", "。", " ", ""],
            is_separator_regex=False,
        )
        logger.info(
            "ParentChildSplitter 初始化完成 parent_max_chars={}, child_size={}, overlap={}",
            self.parent_max_chars,
            self.child_chunk_size,
            self.child_chunk_overlap,
        )

    def split(self, content: str, file_path: str) -> SplitResult:
        """Split a document into parent and child documents."""
        if not content or not content.strip():
            logger.warning("文档内容为空，跳过 parent-child 切分: {}", file_path)
            return SplitResult(parents=[], children=[])

        path = Path(file_path)
        raw_parents = self._split_markdown_parents(content) if path.suffix == ".md" else self._split_text_parents(content)
        parents: list[ParentDocument] = []
        children: list[ChildDocument] = []
        source = path.as_posix()
        file_name = path.name

        for parent_index, raw_parent in enumerate(raw_parents):
            parent_content = raw_parent["content"].strip()
            if not parent_content:
                continue
            title_path = raw_parent.get("title_path", [])
            metadata = {
                "_source": source,
                "_file_name": file_name,
                "_extension": path.suffix,
                "title_path": title_path,
                **raw_parent.get("metadata", {}),
            }
            parent_id = self._build_parent_id(file_name, title_path, parent_index, parent_content)
            parent = ParentDocument(
                parent_id=parent_id,
                source=source,
                file_name=file_name,
                title_path=title_path,
                content=parent_content,
                content_hash=_hash_text(parent_content),
                metadata=metadata,
            )
            parents.append(parent)

            child_texts = self.child_splitter.split_text(parent_content)
            for child_index, child_content in enumerate(child_texts):
                child_content = child_content.strip()
                if not child_content:
                    continue
                child_id = self._build_child_id(parent_id, child_index, child_content)
                child_metadata = {
                    **metadata,
                    "parent_id": parent_id,
                    "child_id": child_id,
                    "chunk_index": child_index,
                    "content_hash": _hash_text(child_content),
                }
                children.append(
                    ChildDocument(
                        child_id=child_id,
                        parent_id=parent_id,
                        source=source,
                        file_name=file_name,
                        title_path=title_path,
                        content=child_content,
                        content_hash=child_metadata["content_hash"],
                        chunk_index=child_index,
                        metadata=child_metadata,
                    )
                )

        logger.info(
            "Parent-child 切分完成: {} -> parents={}, children={}",
            file_path,
            len(parents),
            len(children),
        )
        return SplitResult(parents=parents, children=children)

    def _split_markdown_parents(self, content: str) -> list[dict]:
        md_docs = self.markdown_parent_splitter.split_text(content)
        parents: list[dict] = []
        for doc in md_docs:
            metadata = dict(doc.metadata)
            title_path = [metadata[key] for key in ("h1", "h2") if metadata.get(key)]
            parts = self.parent_overflow_splitter.split_text(doc.page_content)
            for part_index, part in enumerate(parts):
                part_metadata = dict(metadata)
                if len(parts) > 1:
                    part_metadata["parent_part_index"] = part_index
                parents.append(
                    {
                        "content": part,
                        "title_path": title_path,
                        "metadata": part_metadata,
                    }
                )
        return parents

    def _split_text_parents(self, content: str) -> list[dict]:
        parts = self.parent_overflow_splitter.split_text(content)
        return [
            {
                "content": part,
                "title_path": [],
                "metadata": {"parent_part_index": index} if len(parts) > 1 else {},
            }
            for index, part in enumerate(parts)
        ]

    def _build_parent_id(
        self,
        file_name: str,
        title_path: list[str],
        parent_index: int,
        content: str,
    ) -> str:
        """Build a stable short id that fits Milvus varchar primary key limits."""
        title = "::".join(_slug(item) for item in title_path) or f"parent-{parent_index}"
        basis = f"{file_name}::{title}::{parent_index}::{content}"
        return f"p::{_hash_text(basis)[:16]}::{parent_index}::{_hash_text(content)[:12]}"

    def _build_child_id(self, parent_id: str, child_index: int, content: str) -> str:
        """Build a stable short child id for Milvus primary key and docstore join."""
        parent_fingerprint = _hash_text(parent_id)[:12]
        return f"c::{parent_fingerprint}::{child_index}::{_hash_text(content)[:12]}"


parent_child_splitter_service = ParentChildSplitterService()
