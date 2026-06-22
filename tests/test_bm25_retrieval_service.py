from app.models.rag import ChildDocument
from app.services.bm25_retrieval_service import BM25RetrievalService, tokenize_for_bm25


def make_child(child_id: str, file_name: str, content: str) -> ChildDocument:
    return ChildDocument(
        child_id=child_id,
        parent_id=f"p-{child_id}",
        source=f"/tmp/{file_name}",
        file_name=file_name,
        title_path=[file_name],
        content=content,
        content_hash=child_id,
        chunk_index=0,
        metadata={"child_id": child_id, "parent_id": f"p-{child_id}"},
    )


def test_tokenize_keeps_english_and_chinese_bigrams():
    tokens = tokenize_for_bm25("HighCPUUsage 磁盘 使用率 data-sync-service")

    assert "highcpuusage" in tokens
    assert "data" in tokens or "data-sync-service" in tokens
    assert "磁盘" in tokens
    assert "使用" in tokens


def test_bm25_search_hits_keyword_document():
    service = BM25RetrievalService()
    children = [
        make_child("cpu", "cpu_high_usage.md", "HighCPUUsage CPU 使用率过高 排查高 CPU 进程"),
        make_child("disk", "disk_high_usage.md", "DiskFull 磁盘 使用率过高 清理日志文件"),
        make_child("memory", "memory_high_usage.md", "OOM 内存泄漏 堆内存持续上涨"),
    ]
    service.rebuild_index(children)

    cpu_results = service.search("CPU 使用率 高", k=1)
    disk_results = service.search("磁盘 full", k=1)
    memory_results = service.search("OOM 内存", k=1)

    assert cpu_results[0].file_name == "cpu_high_usage.md"
    assert disk_results[0].file_name == "disk_high_usage.md"
    assert memory_results[0].file_name == "memory_high_usage.md"
    assert cpu_results[0].retrieval_channels == ["bm25"]
