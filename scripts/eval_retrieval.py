#!/usr/bin/env python3
"""Evaluate retrieval quality for the OnCall Agent interview benchmark.

Default provider is a deterministic local lexical baseline over ``aiops-docs``.
It does not require Milvus, DashScope, or network access, so it can be used in
interviews to show the evaluation contract even when infra is unavailable.

Use ``--mode dense`` only when Milvus and the app dependencies are ready.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterable

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")
_HYBRID_LOCAL_INDEX_READY = False


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    expected_sources: tuple[str, ...]
    expected_answer_points: tuple[str, ...]
    tags: tuple[str, ...]
    difficulty: str


@dataclass(frozen=True)
class RetrievedDoc:
    source: str
    score: float
    preview: str = ""


def _as_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list[str]")
    return tuple(value)


def load_golden_cases(path: Path) -> list[GoldenCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("golden cases file must contain a JSON list")

    cases: list[GoldenCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"case #{index} must be an object")
        case_id = item.get("id")
        question = item.get("question")
        difficulty = item.get("difficulty", "unknown")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"case #{index} missing id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"case {case_id} missing question")
        if not isinstance(difficulty, str):
            raise ValueError(f"case {case_id} difficulty must be str")

        expected_sources = _as_tuple(item.get("expected_sources"), f"case {case_id}.expected_sources")
        expected_answer_points = _as_tuple(
            item.get("expected_answer_points"), f"case {case_id}.expected_answer_points"
        )
        tags = _as_tuple(item.get("tags", []), f"case {case_id}.tags")
        if not expected_sources:
            raise ValueError(f"case {case_id} needs at least one expected source")
        if not expected_answer_points:
            raise ValueError(f"case {case_id} needs at least one expected answer point")

        seen_ids.add(case_id)
        cases.append(
            GoldenCase(
                id=case_id,
                question=question,
                expected_sources=expected_sources,
                expected_answer_points=expected_answer_points,
                tags=tags,
                difficulty=difficulty,
            )
        )
    return cases


def tokenize(text: str) -> set[str]:
    """Tokenize Chinese/English text with chars, words, and Chinese bigrams."""
    text = text.lower()
    tokens = {m.group(0) for m in WORD_RE.finditer(text)}
    chinese_chars = "".join(CHINESE_RE.findall(text))
    tokens.update(chinese_chars[i : i + 2] for i in range(max(0, len(chinese_chars) - 1)))
    return {token for token in tokens if token.strip()}


def load_local_docs(docs_dir: Path) -> dict[str, str]:
    if not docs_dir.exists():
        raise FileNotFoundError(f"docs dir not found: {docs_dir}")
    docs = {
        path.name: path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(docs_dir.glob("*.md"))
    }
    if not docs:
        raise ValueError(f"no markdown docs found in {docs_dir}")
    return docs


def retrieve_local(question: str, docs: dict[str, str], limit: int) -> list[RetrievedDoc]:
    q_tokens = tokenize(question)
    scored: list[RetrievedDoc] = []
    for source, content in docs.items():
        doc_tokens = tokenize(source.replace("_", " ") + "\n" + content)
        overlap = q_tokens & doc_tokens
        # IDF-lite: common short overlaps still count, but exact filename words help.
        score = sum(1.0 + math.log1p(len(token)) for token in overlap)
        preview = content.strip().replace("\n", " ")[:160]
        scored.append(RetrievedDoc(source=source, score=score, preview=preview))
    scored.sort(key=lambda item: (-item.score, item.source))
    return scored[:limit]


def retrieve_dense_app(question: str, limit: int) -> list[RetrievedDoc]:
    """Use the project's real vector retriever. Requires app infra to be initialized."""
    from app.config import config
    from app.services.vector_store_manager import vector_store_manager

    vector_store = vector_store_manager.get_vector_store()
    retriever = vector_store.as_retriever(search_kwargs={"k": limit or config.rag_top_k})
    docs = retriever.invoke(question)
    out: list[RetrievedDoc] = []
    for doc in docs:
        metadata = getattr(doc, "metadata", {}) or {}
        source = metadata.get("_file_name") or Path(str(metadata.get("_source", "unknown"))).name
        preview = str(getattr(doc, "page_content", "")).replace("\n", " ")[:160]
        # LangChain Milvus retriever does not always expose score here.
        out.append(RetrievedDoc(source=source, score=0.0, preview=preview))
    return out


def retrieve_hybrid_parent(question: str, limit: int, docs_dir: Path) -> list[RetrievedDoc]:
    """Use offline parent-child + BM25 path without requiring Milvus.

    Evaluation must be reproducible from ``docs_dir`` and must not depend on
    partial runtime uploads in ``data/rag``. Production BM25 still uses the
    docstore through ``bm25_retrieval_service``; this script builds an external
    in-memory index for the benchmark process.
    """
    global _HYBRID_LOCAL_INDEX_READY
    from app.services.bm25_retrieval_service import bm25_retrieval_service
    from app.services.parent_child_splitter_service import parent_child_splitter_service
    from app.services.rerank_service import rerank_service

    if not _HYBRID_LOCAL_INDEX_READY:
        docs = load_local_docs(docs_dir)
        all_children = []
        for file_name, content in docs.items():
            split = parent_child_splitter_service.split(content, str(docs_dir / file_name))
            all_children.extend(split.children)
        bm25_retrieval_service.rebuild_index(all_children)
        _HYBRID_LOCAL_INDEX_READY = True

    candidates = bm25_retrieval_service.search(question, k=limit)
    candidates = rerank_service.rerank(question, candidates)[:limit]
    return [
        RetrievedDoc(
            source=candidate.file_name,
            score=candidate.rerank_score or candidate.bm25_score or 0.0,
            preview=candidate.content[:160],
        )
        for candidate in candidates
    ]


def retrieval_metrics(retrieved_sources: Iterable[str], expected_sources: Iterable[str], k: int) -> dict[str, float | int]:
    retrieved = list(retrieved_sources)[:k]
    expected = set(expected_sources)
    if not expected:
        raise ValueError("expected_sources cannot be empty")

    hits = [source for source in retrieved if source in expected]
    first_hit_rank = next((idx + 1 for idx, source in enumerate(retrieved) if source in expected), 0)
    return {
        "hit": int(bool(hits)),
        "recall_at_k": len(set(hits)) / len(expected),
        "precision_at_k": len(hits) / k if k else 0.0,
        "mrr": 1.0 / first_hit_rank if first_hit_rank else 0.0,
        "first_hit_rank": first_hit_rank,
    }


def evaluate(cases: list[GoldenCase], mode: str, docs_dir: Path, k: int) -> dict[str, object]:
    docs = load_local_docs(docs_dir) if mode == "local" else {}
    rows: list[dict[str, object]] = []
    started = perf_counter()

    for case in cases:
        case_started = perf_counter()
        if mode == "local":
            retrieved_docs = retrieve_local(case.question, docs, limit=k)
        elif mode == "dense":
            retrieved_docs = retrieve_dense_app(case.question, limit=k)
        elif mode in {"hybrid", "hybrid_parent"}:
            retrieved_docs = retrieve_hybrid_parent(case.question, limit=k, docs_dir=docs_dir)
        else:
            raise ValueError(f"unsupported mode: {mode}")
        latency_ms = round((perf_counter() - case_started) * 1000, 2)

        sources = [doc.source for doc in retrieved_docs]
        metrics = retrieval_metrics(sources, case.expected_sources, k=k)
        rows.append(
            {
                "id": case.id,
                "question": case.question,
                "expected_sources": list(case.expected_sources),
                "retrieved_sources": sources,
                "latency_ms": latency_ms,
                **metrics,
            }
        )

    def avg(name: str) -> float:
        return round(sum(float(row[name]) for row in rows) / len(rows), 4) if rows else 0.0

    return {
        "mode": mode,
        "k": k,
        "case_count": len(rows),
        "summary": {
            "hit_rate": avg("hit"),
            "recall_at_k": avg("recall_at_k"),
            "precision_at_k": avg("precision_at_k"),
            "mrr": avg("mrr"),
            "total_latency_ms": round((perf_counter() - started) * 1000, 2),
        },
        "cases": rows,
    }


def print_report(report: dict[str, object]) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    print(f"mode={report['mode']} k={report['k']} cases={report['case_count']}")
    print(
        "summary: "
        f"hit_rate={summary['hit_rate']} "
        f"recall@k={summary['recall_at_k']} "
        f"precision@k={summary['precision_at_k']} "
        f"mrr={summary['mrr']} "
        f"latency_ms={summary['total_latency_ms']}"
    )
    print("\ncase results:")
    for row in report["cases"]:  # type: ignore[index]
        print(
            f"- {row['id']}: hit={row['hit']} rr={row['mrr']} "
            f"expected={row['expected_sources']} retrieved={row['retrieved_sources']}"
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate OnCall Agent retrieval golden cases")
    parser.add_argument("--golden", type=Path, default=Path("evals/golden_cases.json"))
    parser.add_argument("--docs-dir", type=Path, default=Path("aiops-docs"))
    parser.add_argument("--mode", choices=("local", "dense", "hybrid", "hybrid_parent"), default="local")
    parser.add_argument("--provider", choices=("local", "app"), default=None, help=argparse.SUPPRESS)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    cases = load_golden_cases(args.golden)
    if args.validate_only:
        print(f"valid golden cases: {len(cases)}")
        return 0

    mode = "dense" if args.provider == "app" else args.provider or args.mode
    report = evaluate(cases=cases, mode=mode, docs_dir=args.docs_dir, k=args.k)
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
