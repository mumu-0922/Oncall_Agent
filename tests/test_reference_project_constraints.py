import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "reference-projects"
MANIFEST = REFERENCE_DIR / "manifest.json"
PLAN = ROOT / "docs" / "open_source_reference_plan.md"


def test_reference_workspace_is_gitignored_and_documented():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "reference-projects/*" in gitignore
    assert "!reference-projects/manifest.json" in gitignore
    assert (REFERENCE_DIR / "README.md").exists()
    assert MANIFEST.exists()


def test_reference_manifest_lists_required_learning_targets():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    projects = {project["name"]: project for project in manifest["projects"]}

    for name in {"holmesgpt", "k8sgpt", "keep", "ragflow", "langfuse", "promptfoo", "ragas"}:
        assert name in projects
        assert projects[name]["repo"].startswith("https://github.com/")
        assert projects[name]["learn"]
        assert projects[name]["target_modules"]


def test_reference_plan_declares_no_vendor_copy_and_evidence_first_constraints():
    plan = PLAN.read_text(encoding="utf-8")
    required_phrases = [
        "reference-projects/",
        "禁止提交第三方源码",
        "主工程不得从 `reference-projects/` import",
        "Evidence Package",
        "报告只能从证据包生成",
        "没证据就写“证据不足”",
        "不让 LLM 在工具失败时补故事",
    ]
    for phrase in required_phrases:
        assert phrase in plan


def test_main_project_does_not_import_reference_workspace():
    scanned_roots = [ROOT / "app", ROOT / "mcp_servers", ROOT / "scripts", ROOT / "tests"]
    offenders = []
    for scanned_root in scanned_roots:
        for path in scanned_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "reference-projects" in text or "reference_projects" in text:
                rel = path.relative_to(ROOT)
                if rel != Path("tests/test_reference_project_constraints.py"):
                    offenders.append(str(rel))
    assert offenders == []
