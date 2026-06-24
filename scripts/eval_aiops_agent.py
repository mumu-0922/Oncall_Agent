#!/usr/bin/env python3
"""Evaluate AIOps Agent evidence discipline with deterministic trace cases.

This benchmark is intentionally offline by default. It consumes golden cases
that look like AIOps runtime trace events and scores the agent contract:

- did expected tools get called?
- did tool results succeed or fail transparently?
- did the response cite actionable evidence instead of inventing root causes?
- were insufficient-evidence and timeout situations accounted for?

It mirrors ``scripts/eval_retrieval.py``: stable JSON in, stable JSON out,
no LLM/network requirement for the default path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from app.services.evidence_package_service import EvidencePackageService

TOOL_TIMEOUT_RE = re.compile(r"timeout|timed out|超时", re.I)
INSUFFICIENT_EVIDENCE_RE = re.compile(
    r"证据不足|insufficient evidence|拒绝生成未经证实|没有(?:任何)?工具证据|无(?:任何)?工具证据",
    re.I,
)
EVIDENCE_REF_RE = re.compile(r"\bE\d{3}-(?:alert|metric|log|runbook|tool_error|tool_result)\b")
ROOT_CAUSE_RE = re.compile(r"根因|导致|造成|由于|because|caused by", re.I)
NEGATION_RE = re.compile(r"不能|无法|拒绝|不足|未发现|不能直接|不可|无证据|没有证据|not enough|insufficient", re.I)


@dataclass(frozen=True)
class AIOpsEvalCase:
    id: str
    question: str
    expected_tools: tuple[str, ...]
    expected_evidence_kinds: tuple[str, ...]
    expected_outcome: str
    tool_events: tuple[dict[str, Any], ...]
    final_response: str
    forbidden_claims: tuple[str, ...]
    expected_error_substrings: tuple[str, ...]
    tags: tuple[str, ...]
    difficulty: str


def _as_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list[str]")
    return tuple(value)


def _as_event_tuple(value: object, field_name: str) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must be a list[object]")
    return tuple(value)


def load_aiops_cases(path: Path) -> list[AIOpsEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("AIOps eval cases file must contain a JSON list")

    cases: list[AIOpsEvalCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"case #{index} must be an object")
        case_id = item.get("id")
        question = item.get("question")
        final_response = item.get("final_response", "")
        expected_outcome = item.get("expected_outcome")
        difficulty = item.get("difficulty", "unknown")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"case #{index} missing id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"case {case_id} missing question")
        if not isinstance(final_response, str):
            raise ValueError(f"case {case_id}.final_response must be str")
        if not isinstance(expected_outcome, str) or not expected_outcome:
            raise ValueError(f"case {case_id}.expected_outcome must be str")
        if not isinstance(difficulty, str):
            raise ValueError(f"case {case_id}.difficulty must be str")

        seen_ids.add(case_id)
        cases.append(
            AIOpsEvalCase(
                id=case_id,
                question=question,
                expected_tools=_as_str_tuple(item.get("expected_tools", []), f"case {case_id}.expected_tools"),
                expected_evidence_kinds=_as_str_tuple(
                    item.get("expected_evidence_kinds", []),
                    f"case {case_id}.expected_evidence_kinds",
                ),
                expected_outcome=expected_outcome,
                tool_events=_as_event_tuple(item.get("tool_events", []), f"case {case_id}.tool_events"),
                final_response=final_response,
                forbidden_claims=_as_str_tuple(item.get("forbidden_claims", []), f"case {case_id}.forbidden_claims"),
                expected_error_substrings=_as_str_tuple(
                    item.get("expected_error_substrings", []),
                    f"case {case_id}.expected_error_substrings",
                ),
                tags=_as_str_tuple(item.get("tags", []), f"case {case_id}.tags"),
                difficulty=difficulty,
            )
        )
    return cases


def _json_ready_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        summary = item.get("summary")
        if isinstance(summary, (dict, list)):
            item["summary"] = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        normalized.append(item)
    return normalized


def _called_tools(events: Iterable[Mapping[str, Any]]) -> list[str]:
    tools: list[str] = []
    for event in events:
        if event.get("kind") != "tool_call":
            continue
        tool_name = event.get("tool")
        if isinstance(tool_name, str) and tool_name:
            tools.append(tool_name)
    return tools


def _tool_result_events(events: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [event for event in events if event.get("kind") == "tool_result"]


def _tool_result_success(event: Mapping[str, Any]) -> bool:
    if str(event.get("status") or "").lower() == "error":
        return False
    summary = event.get("summary")
    payload: Any = summary
    if isinstance(summary, str):
        try:
            payload = json.loads(summary)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict) and payload.get("error"):
        return False
    return True


def _all_event_text(events: Iterable[Mapping[str, Any]]) -> str:
    chunks: list[str] = []
    for event in events:
        try:
            chunks.append(json.dumps(event, ensure_ascii=False, default=str))
        except TypeError:
            chunks.append(str(event))
    return "\n".join(chunks)


def _has_tool_timeout(events: Iterable[Mapping[str, Any]]) -> bool:
    """Only failed tool results count as benchmark timeouts.

    Business logs may legitimately contain words like "gateway timeout"; those
    are evidence, not evaluator-level timeout failures.
    """
    for event in _tool_result_events(events):
        if _tool_result_success(event):
            continue
        try:
            text = json.dumps(event, ensure_ascii=False, default=str)
        except TypeError:
            text = str(event)
        if TOOL_TIMEOUT_RE.search(text):
            return True
    return False


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles if needle)


def expected_tool_hit_rate(called_tools: Iterable[str], expected_tools: Iterable[str]) -> float:
    expected = list(expected_tools)
    if not expected:
        return 1.0
    called = set(called_tools)
    return len([tool for tool in expected if tool in called]) / len(expected)


def tool_call_success_rate(events: Iterable[Mapping[str, Any]]) -> float:
    result_events = _tool_result_events(events)
    if not result_events:
        return 1.0
    success_count = sum(1 for event in result_events if _tool_result_success(event))
    return success_count / len(result_events)


def response_has_forbidden_claim(response: str, forbidden_claims: Iterable[str]) -> bool:
    return _contains_any(response, forbidden_claims)


def response_has_unsupported_root_cause(response: str, actionable_evidence_count: int) -> bool:
    """Heuristic guardrail: no actionable evidence means root-cause language must be negated."""
    if actionable_evidence_count > 0:
        return False
    sentences = re.split(r"[。！？!?\n]+", response)
    for sentence in sentences:
        if ROOT_CAUSE_RE.search(sentence) and not NEGATION_RE.search(sentence):
            return True
    return False


def evidence_coverage(response: str, evidence_count: int) -> float:
    if evidence_count <= 0:
        return 1.0 if INSUFFICIENT_EVIDENCE_RE.search(response) else 0.0
    refs = set(EVIDENCE_REF_RE.findall(response))
    return min(1.0, len(refs) / evidence_count)


def classify_case_outcome(
    *,
    expected_outcome: str,
    events: Iterable[Mapping[str, Any]],
    response: str,
    actionable_evidence_count: int,
    forbidden_claims: Iterable[str],
) -> dict[str, Any]:
    timeout = _has_tool_timeout(events)
    insufficient = actionable_evidence_count == 0 and bool(INSUFFICIENT_EVIDENCE_RE.search(response))
    tool_error = any(not _tool_result_success(event) for event in _tool_result_events(events))
    hallucination = response_has_forbidden_claim(response, forbidden_claims) or response_has_unsupported_root_cause(
        response,
        actionable_evidence_count,
    )

    if timeout:
        actual_outcome = "timeout"
    elif insufficient:
        actual_outcome = "insufficient_evidence"
    elif tool_error:
        actual_outcome = "tool_error"
    else:
        actual_outcome = "success"

    return {
        "actual_outcome": actual_outcome,
        "outcome_match": int(actual_outcome == expected_outcome),
        "timeout": int(timeout),
        "insufficient_evidence": int(insufficient),
        "tool_error": int(tool_error),
        "hallucination": int(hallucination),
    }


def evaluate_case(case: AIOpsEvalCase, evidence_service: EvidencePackageService) -> dict[str, Any]:
    started = perf_counter()
    events = _json_ready_events(case.tool_events)
    package = evidence_service.build_from_state(
        {
            "input": case.question,
            "tool_events": events,
            "past_steps": [],
            "response": case.final_response,
        }
    )
    latency_ms = round((perf_counter() - started) * 1000, 2)

    called_tools = _called_tools(events)
    expected_hit = expected_tool_hit_rate(called_tools, case.expected_tools)
    tool_success = tool_call_success_rate(events)
    coverage = evidence_coverage(case.final_response, package.evidence_count)
    outcome = classify_case_outcome(
        expected_outcome=case.expected_outcome,
        events=events,
        response=case.final_response,
        actionable_evidence_count=package.actionable_evidence_count,
        forbidden_claims=case.forbidden_claims,
    )
    expected_errors_present = (
        _contains_any(_all_event_text(events) + "\n" + case.final_response, case.expected_error_substrings)
        if case.expected_error_substrings
        else True
    )
    evidence_kinds = sorted(Counter(item.kind for item in package.all_items()))
    expected_kind_hit = expected_tool_hit_rate(evidence_kinds, case.expected_evidence_kinds)

    return {
        "id": case.id,
        "question": case.question,
        "difficulty": case.difficulty,
        "tags": list(case.tags),
        "expected_tools": list(case.expected_tools),
        "called_tools": called_tools,
        "expected_evidence_kinds": list(case.expected_evidence_kinds),
        "evidence_kinds": evidence_kinds,
        "expected_outcome": case.expected_outcome,
        "actual_outcome": outcome["actual_outcome"],
        "tool_call_success_rate": round(tool_success, 4),
        "expected_tool_hit_rate": round(expected_hit, 4),
        "expected_evidence_kind_hit_rate": round(expected_kind_hit, 4),
        "evidence_coverage": round(coverage, 4),
        "hallucination_blocked": int(not outcome["hallucination"]),
        "insufficient_evidence": outcome["insufficient_evidence"],
        "timeout": outcome["timeout"],
        "tool_error": outcome["tool_error"],
        "outcome_match": outcome["outcome_match"],
        "expected_error_substrings_present": int(expected_errors_present),
        "latency_ms": latency_ms,
        "evidence_package": {
            "incident_id": package.incident_id,
            "confidence": package.confidence,
            "evidence_count": package.evidence_count,
            "actionable_evidence_count": package.actionable_evidence_count,
            "limitations": package.limitations,
            "findings": [finding.model_dump(mode="json") for finding in package.findings],
        },
    }


def _avg(rows: list[dict[str, Any]], field: str) -> float:
    return round(sum(float(row[field]) for row in rows) / len(rows), 4) if rows else 0.0


def evaluate(cases: list[AIOpsEvalCase]) -> dict[str, Any]:
    evidence_service = EvidencePackageService()
    started = perf_counter()
    rows = [evaluate_case(case, evidence_service) for case in cases]
    summary = {
        "tool_call_success_rate": _avg(rows, "tool_call_success_rate"),
        "expected_tool_hit_rate": _avg(rows, "expected_tool_hit_rate"),
        "expected_evidence_kind_hit_rate": _avg(rows, "expected_evidence_kind_hit_rate"),
        "evidence_coverage": _avg(rows, "evidence_coverage"),
        "hallucination_block_rate": _avg(rows, "hallucination_blocked"),
        "insufficient_evidence_rate": _avg(rows, "insufficient_evidence"),
        "timeout_rate": _avg(rows, "timeout"),
        "outcome_match_rate": _avg(rows, "outcome_match"),
        "expected_error_message_rate": _avg(rows, "expected_error_substrings_present"),
        "avg_latency_ms": _avg(rows, "latency_ms"),
        "total_latency_ms": round((perf_counter() - started) * 1000, 2),
    }
    return {
        "mode": "offline_trace",
        "case_count": len(rows),
        "summary": summary,
        "cases": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(f"mode={report['mode']} cases={report['case_count']}")
    print(
        "summary: "
        f"tool_call_success_rate={summary['tool_call_success_rate']} "
        f"expected_tool_hit_rate={summary['expected_tool_hit_rate']} "
        f"evidence_coverage={summary['evidence_coverage']} "
        f"hallucination_block_rate={summary['hallucination_block_rate']} "
        f"insufficient_evidence_rate={summary['insufficient_evidence_rate']} "
        f"timeout_rate={summary['timeout_rate']} "
        f"avg_latency_ms={summary['avg_latency_ms']}"
    )
    print("\ncase results:")
    for row in report["cases"]:
        print(
            f"- {row['id']}: outcome={row['actual_outcome']} "
            f"tool_hit={row['expected_tool_hit_rate']} evidence={row['evidence_coverage']} "
            f"hallucination_blocked={row['hallucination_blocked']} tools={row['called_tools']}"
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate OnCall Agent AIOps golden cases")
    parser.add_argument("--cases", type=Path, default=Path("evals/aiops_cases.json"))
    parser.add_argument("--out", type=Path, default=Path("evals/reports/aiops_agent_eval.json"))
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    cases = load_aiops_cases(args.cases)
    if args.validate_only:
        print(f"valid aiops cases: {len(cases)}")
        return 0

    report = evaluate(cases)
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
