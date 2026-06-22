from app.services.parent_child_splitter_service import ParentChildSplitterService


def test_split_markdown_generates_parent_and_children():
    content = """# CPU 使用率过高

## 排查步骤

第一步查看 CPU 指标。第二步查看错误日志。第三步查看历史工单。

## 处理方案

先扩容或限流，再定位高 CPU 进程。
"""
    service = ParentChildSplitterService()
    result = service.split(content, "/tmp/cpu_high_usage.md")

    assert result.parents
    assert result.children
    assert all(child.parent_id for child in result.children)
    assert {parent.parent_id for parent in result.parents} >= {
        child.parent_id for child in result.children
    }
    assert all(child.metadata["_file_name"] == "cpu_high_usage.md" for child in result.children)
    assert any("排查步骤" in child.title_path for child in result.children)


def test_split_empty_document_returns_empty_lists():
    service = ParentChildSplitterService()
    result = service.split("  ", "/tmp/empty.md")

    assert result.parents == []
    assert result.children == []


def test_split_ids_fit_milvus_primary_key_limit():
    content = "# " + ("超长标题" * 30) + "\n\n" + ("CPU 使用率过高，需要排查日志、监控、历史工单。" * 80)
    service = ParentChildSplitterService()
    result = service.split(content, "/tmp/" + ("very_long_file_name_" * 8) + "cpu_high_usage.md")

    assert result.parents
    assert result.children
    assert all(len(parent.parent_id) <= 100 for parent in result.parents)
    assert all(len(child.child_id) <= 100 for child in result.children)
    assert {parent.parent_id for parent in result.parents} >= {child.parent_id for child in result.children}
