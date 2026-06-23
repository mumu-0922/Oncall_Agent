"""腾讯云 CLS (Cloud Log Service) MCP Server

本地实现的 CLS 日志服务 MCP Server，提供日志查询、检索和分析功能。
"""

import logging
import functools
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from pathlib import Path
from fastmcp import FastMCP

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a project dependency
    load_dotenv = None

if load_dotenv:
    load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("CLS_MCP_Server")

mcp = FastMCP("CLS")


def log_tool_call(func):
    """装饰器：记录工具调用的日志，包括方法名、参数和返回状态"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        method_name = func.__name__

        # 记录调用信息
        logger.info(f"=" * 80)
        logger.info(f"调用方法: {method_name}")

        # 记录参数（排除self等）
        if kwargs:
            # 使用 json.dumps 格式化参数，处理可能的序列化错误
            try:
                params_str = json.dumps(kwargs, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                params_str = str(kwargs)
            logger.info(f"参数信息:\n{params_str}")
        else:
            logger.info("参数信息: 无")

        # 执行方法
        try:
            result = func(*args, **kwargs)

            # 记录返回状态
            logger.info(f"返回状态: SUCCESS")

            # 记录返回结果摘要（避免日志过长）
            if isinstance(result, dict):
                summary = {k: v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} with {len(v)} items>"
                          for k, v in list(result.items())[:5]}
                logger.info(f"返回结果摘要: {json.dumps(summary, ensure_ascii=False)}")
            else:
                logger.info(f"返回结果: {result}")

            logger.info(f"=" * 80)
            return result

        except Exception as e:
            # 记录错误状态
            logger.error(f"返回状态: ERROR")
            logger.error(f"错误信息: {str(e)}")
            logger.error(f"=" * 80)
            raise

    return wrapper


def parse_time_or_default(time_str: Optional[str], default_offset_hours: int = 0) -> datetime:
    """解析时间字符串或返回默认时间。

    Args:
        time_str: 时间字符串（格式：YYYY-MM-DD HH:MM:SS）
        default_offset_hours: 默认时间偏移（小时）

    Returns:
        datetime: 解析后的时间对象
    """
    if time_str:
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return datetime.now() + timedelta(hours=default_offset_hours)


def generate_time_series(base_time: datetime, minutes_offset: int) -> str:
    """生成基于基准时间的时间字符串。

    Args:
        base_time: 基准时间
        minutes_offset: 分钟偏移量

    Returns:
        str: 格式化的时间字符串
    """
    result_time = base_time + timedelta(minutes=minutes_offset)
    return result_time.strftime("%Y-%m-%d %H:%M:%S")


LOCAL_LOG_PROVIDER_NAMES = {"local", "local_vps", "local_wsl", "wsl", "vps", "file"}
MOCK_LOG_PROVIDER_NAMES = {"mock", "demo", "sample"}


def _log_provider() -> str:
    return os.getenv("AIOPS_LOG_PROVIDER", "disabled").strip().lower() or "disabled"


def _is_local_log_provider() -> bool:
    return _log_provider() in LOCAL_LOG_PROVIDER_NAMES


def _allow_mock_provider() -> bool:
    return os.getenv("AIOPS_ALLOW_MOCK", "false").strip().lower() in {"1", "true", "yes", "on"}


def _is_mock_log_provider() -> bool:
    return _log_provider() in MOCK_LOG_PROVIDER_NAMES


def _provider_error_response(action: str, **extra: Any) -> Dict[str, Any]:
    provider = _log_provider()
    if _is_mock_log_provider() and not _allow_mock_provider():
        error = "AIOps mock 日志数据已被禁用；拒绝返回假 topic/假日志。"
        suggestion = "设置 AIOPS_LOG_PROVIDER=local_wsl/local_vps，或仅演示时显式设置 AIOPS_ALLOW_MOCK=true。"
    else:
        error = f"未配置可用日志数据源: AIOPS_LOG_PROVIDER={provider}"
        suggestion = "设置 AIOPS_LOG_PROVIDER=local_wsl/local_vps，并配置 AIOPS_SERVICE_LOG_MAP。"
    return {
        "action": action,
        "source": provider,
        "error": error,
        "suggestion": suggestion,
        **extra,
    }


def _load_json_env(name: str) -> dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("环境变量 %s 不是合法 JSON，已忽略", name)
        return {}
    return value if isinstance(value, dict) else {}


def _default_service_name() -> str:
    return os.getenv("AIOPS_DEFAULT_SERVICE", "").strip()


def _local_log_map() -> dict[str, list[str]]:
    """服务到本机日志文件路径的映射。"""
    mapping = _load_json_env("AIOPS_SERVICE_LOG_MAP")
    normalized: dict[str, list[str]] = {}
    for service_name, raw_paths in mapping.items():
        if isinstance(raw_paths, str):
            paths = [item.strip() for item in raw_paths.split("|") if item.strip()]
        elif isinstance(raw_paths, list):
            paths = [str(item).strip() for item in raw_paths if str(item).strip()]
        else:
            paths = []
        normalized[str(service_name)] = paths
    return normalized


def _topic_id_for_service(service_name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in service_name)
    return f"local:{safe}"


def _service_from_topic_id(topic_id: str) -> str | None:
    return topic_id.removeprefix("local:") if topic_id.startswith("local:") else None


def _resolve_service_from_topic_id(topic_id: str, log_map: dict[str, list[str]]) -> str | None:
    """把 local:<service> topic_id 还原为日志映射中的真实 service_name。"""
    if not topic_id.startswith("local:"):
        return None
    direct_service = _service_from_topic_id(topic_id)
    if direct_service in log_map:
        return direct_service
    for mapped_service in log_map:
        if _topic_id_for_service(mapped_service) == topic_id:
            return mapped_service
    return direct_service


def _local_topic_for_service(service_name: str, paths: list[str]) -> dict[str, Any]:
    existing = [path for path in paths if Path(path).exists()]
    return {
        "topic_id": _topic_id_for_service(service_name),
        "topic_name": f"{service_name} 本机日志",
        "service_name": service_name,
        "region_code": "local-vps",
        "create_time": None,
        "log_count": None,
        "description": "local 本机日志文件映射",
        "paths": paths,
        "available": bool(existing),
        "existing_paths": existing,
    }


def _line_matches_query(line: str, query: Optional[str]) -> bool:
    if not query:
        return True
    # 轻量兼容：把常见 CLS 查询里的 level:ERROR / OR 拆成关键字匹配。
    cleaned = (
        query.replace("(", " ")
        .replace(")", " ")
        .replace(":", " ")
        .replace('"', " ")
        .replace("'", " ")
    )
    keywords = [
        part.strip().lower()
        for part in cleaned.replace(" OR ", " ").replace(" or ", " ").split()
        if part.strip() and part.strip().upper() not in {"AND", "OR", "LEVEL", "MESSAGE"}
    ]
    if not keywords:
        return True
    lowered = line.lower()
    return any(keyword in lowered for keyword in keywords)


def _parse_line_timestamp_ms(line: str, fallback_ms: int) -> int:
    # 支持 "YYYY-MM-DD HH:MM:SS" / ISO 前缀；解析失败则用文件 mtime。
    candidates = [line[:19], line[:23], line[:25]]
    for candidate in candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return int(datetime.strptime(candidate[:19], fmt).timestamp() * 1000)
            except ValueError:
                continue
    return fallback_ms


def _search_local_logs(
    *,
    topic_id: str,
    start_time: int,
    end_time: int,
    query: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    log_map = _local_log_map()
    service_name = _resolve_service_from_topic_id(topic_id, log_map)
    paths = log_map.get(service_name or "", []) if service_name else []
    if not service_name or not paths:
        return {
            "topic_id": topic_id,
            "start_time": start_time,
            "end_time": end_time,
            "query": query,
            "limit": limit,
            "total": 0,
            "logs": [],
            "took_ms": 0,
            "source": f"{_log_provider()}:file",
            "error": f"未配置 topic_id={topic_id} 对应的本机日志文件",
            "message": "请在 .env 配置 AIOPS_SERVICE_LOG_MAP",
        }

    logs: list[dict[str, Any]] = []
    scanned_files = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            continue
        scanned_files.append(str(path))
        try:
            fallback_ms = int(path.stat().st_mtime * 1000)
            # 简化读取：保留最后约 5000 行，避免超大日志拖垮诊断。
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]
        except OSError as exc:
            logger.warning("读取本机日志失败 %s: %s", path, exc)
            continue
        for line in lines:
            timestamp_ms = _parse_line_timestamp_ms(line, fallback_ms)
            if timestamp_ms < start_time or timestamp_ms > end_time:
                continue
            if not _line_matches_query(line, query):
                continue
            logs.append(
                {
                    "timestamp": datetime.fromtimestamp(timestamp_ms / 1000).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "level": _guess_log_level(line),
                    "message": line,
                    "file": str(path),
                }
            )
    logs.sort(key=lambda item: item["timestamp"], reverse=True)
    logs = logs[: max(1, limit)]
    return {
        "topic_id": topic_id,
        "start_time": start_time,
        "end_time": end_time,
        "query": query,
        "limit": limit,
        "total": len(logs),
        "logs": logs,
        "took_ms": 0,
        "source": f"{_log_provider()}:file",
        "scanned_files": scanned_files,
        "message": f"本机日志查询返回 {len(logs)} 条",
    }


def _guess_log_level(line: str) -> str:
    lowered = line.lower()
    if "error" in lowered or "exception" in lowered or "traceback" in lowered:
        return "ERROR"
    if "warn" in lowered:
        return "WARN"
    if "debug" in lowered:
        return "DEBUG"
    return "INFO"


@mcp.tool()
@log_tool_call
def get_current_timestamp() -> int:
    """获取当前时间戳（以毫秒为单位）。
    
    此工具用于获取标准的毫秒时间戳，可用于：
    1. 作为 search_log 的 end_time 参数（查询到现在）
    2. 计算历史时间点作为 start_time 参数
    
    Returns:
        int: 当前时间戳（毫秒），例如: 1708012345000
    
    使用示例:
        # 获取当前时间
        current = get_current_timestamp()
        
        # 计算15分钟前的时间
        fifteen_min_ago = current - (15 * 60 * 1000)
        
        # 计算1小时前的时间
        one_hour_ago = current - (60 * 60 * 1000)
        
        # 用于搜索最近15分钟的日志
        search_log(
            topic_id="topic-001",
            start_time=fifteen_min_ago,
            end_time=current
        )
    """
    return int(datetime.now().timestamp() * 1000)


@mcp.tool()
@log_tool_call
def get_region_code_by_name(region_name: str) -> Dict[str, Any]:
    """根据地区名称搜索对应的地区参数。

    Args:
        region_name: 地区名称（如：北京、上海、广州等）

    Returns:
        Dict: 包含地区代码和相关信息的字典
            - region_code: 地区代码
            - region_name: 地区名称
            - available: 是否可用
    """
    if _is_local_log_provider() and region_name.lower() in {"local", "vps", "本机", "本地"}:
        return {"region_code": "local-vps", "region_name": "本机 VPS", "available": True}
    if not (_is_mock_log_provider() and _allow_mock_provider()):
        return _provider_error_response(
            "get_region_code_by_name",
            region_name=region_name,
            region_code=None,
            available=False,
        )

    # 模拟地区映射表（实际应该从配置或数据库读取）
    region_mapping = {
        "北京": {"region_code": "ap-beijing", "region_name": "北京", "available": True},
        "上海": {"region_code": "ap-shanghai", "region_name": "上海", "available": True},
        "广州": {"region_code": "ap-guangzhou", "region_name": "广州", "available": True},
    }

    result = region_mapping.get(region_name)
    if result:
        return result
    else:
        return {
            "region_code": None,
            "region_name": region_name,
            "available": False,
            "error": f"未找到地区: {region_name}"
        }


@mcp.tool()
@log_tool_call
def get_topic_info_by_name(topic_name: str, region_code: Optional[str] = None) -> Dict[str, Any]:
    """根据主题名称搜索相关的主题信息。

    Args:
        topic_name: 主题名称
        region_code: 地区代码（可选）

    Returns:
        Dict: 包含主题信息的字典
            - topic_id: 主题ID
            - topic_name: 主题名称
            - region_code: 所属地区
            - create_time: 创建时间
            - log_count: 日志数量
    """
    if _is_local_log_provider():
        for service_name, paths in _local_log_map().items():
            topic = _local_topic_for_service(service_name, paths)
            if topic["topic_name"] == topic_name or topic["topic_id"] == topic_name:
                return topic
        return {
            "topic_id": None,
            "topic_name": topic_name,
            "region_code": region_code,
            "source": f"{_log_provider()}:file",
            "error": f"未找到本机日志主题: {topic_name}",
        }
    if not (_is_mock_log_provider() and _allow_mock_provider()):
        return _provider_error_response(
            "get_topic_info_by_name",
            topic_name=topic_name,
            region_code=region_code,
            topic_id=None,
        )

    mock_topics = [
        {
            "topic_id": "topic-001",
            "topic_name": "数据同步服务日志",
            "service_name": "data-sync-service",
            "region_code": "ap-beijing",
            "create_time": "2024-01-01 10:00:00",
            "log_count": 0,
            "description": "服务应用日志"
        }
    ]

    # 根据名称和地区筛选
    for topic in mock_topics:
        if topic["topic_name"] == topic_name:
            if region_code is None or topic["region_code"] == region_code:
                return topic

    return {
        "topic_id": None,
        "topic_name": topic_name,
        "region_code": region_code,
        "error": f"未找到主题: {topic_name}"
    }


@mcp.tool()
@log_tool_call
def search_topic_by_service_name(
    service_name: str,
    region_code: Optional[str] = None,
    fuzzy: bool = True
) -> Dict[str, Any]:
    """根据服务名称搜索相关的日志主题信息，支持模糊搜索。
    
    此工具用于根据服务名称查找对应的日志主题（topic），便于后续进行日志查询。
    
    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service", "sync", "data-sync"
            说明: 当 fuzzy=True 时，支持部分匹配
        
        region_code: 地区代码（可选）
            示例: "ap-beijing", "ap-shanghai"
            说明: 如果指定，只返回该地区的主题
        
        fuzzy: 是否启用模糊搜索（可选，默认 True）
            True: 部分匹配，例如 "sync" 可以匹配 "data-sync-service"
            False: 精确匹配，必须完全一致
    
    Returns:
        Dict: 搜索结果
            - total: 匹配到的主题数量
            - topics: 主题列表，每个主题包含:
                * topic_id: 主题ID（用于后续日志查询）
                * topic_name: 主题名称
                * service_name: 服务名称
                * region_code: 所属地区
                * create_time: 创建时间
                * log_count: 日志数量
                * description: 主题描述
            - query: 查询条件
    
    使用示例:
        # 示例1: 模糊搜索（推荐）
        search_topic_by_service_name(service_name="data-sync")
        # 可以匹配: "data-sync-service", "data-sync-worker" 等
        
        # 示例2: 精确搜索
        search_topic_by_service_name(
            service_name="data-sync-service",
            fuzzy=False
        )
        
        # 示例3: 指定地区搜索
        search_topic_by_service_name(
            service_name="sync",
            region_code="ap-beijing"
        )
        
        # 示例4: 查找后进行日志搜索的完整流程
        # 步骤1: 根据服务名查找 topic
        result = search_topic_by_service_name(service_name="data-sync-service")
        
        # 步骤2: 获取 topic_id
        topic_id = result["topics"][0]["topic_id"]  # "topic-001"
        
        # 步骤3: 使用 topic_id 查询日志
        current_ts = get_current_timestamp()
        start_ts = current_ts - (15 * 60 * 1000)
        search_log(
            topic_id=topic_id,
            start_time=start_ts,
            end_time=current_ts
        )
    """
    if _is_local_log_provider():
        log_map = _local_log_map()
        allow_local_region = not region_code or region_code == "local-vps"
        matched_topics = []
        for mapped_service, paths in log_map.items():
            if region_code and region_code != "local-vps":
                continue
            mapped_lower = mapped_service.lower()
            query_lower = service_name.lower()
            is_match = (
                query_lower in mapped_lower or mapped_lower in query_lower
                if fuzzy
                else mapped_lower == query_lower
            )
            if is_match:
                matched_topics.append(_local_topic_for_service(mapped_service, paths))
        fallback_service = None
        if not matched_topics and allow_local_region and len(log_map) == 1:
            fallback_service, fallback_paths = next(iter(log_map.items()))
            matched_topics.append(_local_topic_for_service(fallback_service, fallback_paths))
        elif not matched_topics and allow_local_region and _default_service_name() in log_map:
            fallback_service = _default_service_name()
            matched_topics.append(_local_topic_for_service(fallback_service, log_map[fallback_service]))
        return {
            "total": len(matched_topics),
            "topics": matched_topics,
            "query": {
                "service_name": service_name,
                "region_code": region_code,
                "fuzzy": fuzzy,
            },
            "source": f"{_log_provider()}:file",
            "message": (
                f"未找到服务 '{service_name}' 的精确映射，已 fallback 到唯一已配置服务 '{fallback_service}'"
                if fallback_service
                else f"找到 {len(matched_topics)} 个本机日志主题"
                if matched_topics
                else f"未找到服务 '{service_name}' 的本机日志映射；请配置 AIOPS_SERVICE_LOG_MAP"
            ),
        }
    if not (_is_mock_log_provider() and _allow_mock_provider()):
        return {
            "total": 0,
            "topics": [],
            "query": {
                "service_name": service_name,
                "region_code": region_code,
                "fuzzy": fuzzy,
            },
            **_provider_error_response("search_topic_by_service_name"),
        }

    # Mock 主题数据（实际应该从配置或数据库读取）
    mock_topics = [
        {
            "topic_id": "topic-001",
            "topic_name": "数据同步服务日志",
            "service_name": "data-sync-service",
            "region_code": "ap-beijing",
            "create_time": "2024-01-01 10:00:00",
            "log_count": 0,
            "description": "数据同步服务的应用日志，包含同步任务执行情况"
        },
        {
            "topic_id": "topic-002",
            "topic_name": "数据同步服务错误日志",
            "service_name": "data-sync-service",
            "region_code": "ap-beijing",
            "create_time": "2024-01-01 10:00:00",
            "log_count": 0,
            "description": "数据同步服务的错误日志"
        },
        {
            "topic_id": "topic-003",
            "topic_name": "API网关服务日志",
            "service_name": "api-gateway-service",
            "region_code": "ap-shanghai",
            "create_time": "2024-01-01 10:00:00",
            "log_count": 0,
            "description": "API网关服务日志"
        }
    ]
    
    matched_topics = []
    
    # 搜索逻辑
    for topic in mock_topics:
        # 地区筛选
        if region_code and topic["region_code"] != region_code:
            continue
        
        # 服务名称匹配
        topic_service_name = topic.get("service_name", "")
        
        if fuzzy:
            # 模糊匹配：服务名包含查询字符串，或查询字符串包含服务名
            if (service_name.lower() in topic_service_name.lower() or 
                topic_service_name.lower() in service_name.lower()):
                matched_topics.append(topic)
        else:
            # 精确匹配
            if topic_service_name == service_name:
                matched_topics.append(topic)
    
    return {
        "total": len(matched_topics),
        "topics": matched_topics,
        "query": {
            "service_name": service_name,
            "region_code": region_code,
            "fuzzy": fuzzy
        },
        "message": f"找到 {len(matched_topics)} 个匹配的日志主题" if matched_topics else f"未找到服务 '{service_name}' 的日志主题"
    }


@mcp.tool()
@log_tool_call
def search_log(
    topic_id: str,
    start_time: int,
    end_time: int,
    query: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """基于提供的查询参数搜索日志。

    Args:
        topic_id: 主题ID（必填）
            示例: "topic-001"
        
        start_time: 开始时间戳，单位为毫秒（必填，int类型）
            重要: 必须传递整数类型的毫秒时间戳
            获取方式: 
            1. 使用 get_current_timestamp() 工具获取当前时间戳
            2. 计算历史时间: current_timestamp - (分钟数 * 60 * 1000)
            示例: 
            - 当前时间: 1708012345000
            - 15分钟前: 1708012345000 - (15 * 60 * 1000) = 1708011445000
            - 1小时前: 1708012345000 - (60 * 60 * 1000) = 1708008745000
        
        end_time: 结束时间戳，单位为毫秒（必填，int类型）
            重要: 必须传递整数类型的毫秒时间戳
            通常使用 get_current_timestamp() 工具获取当前时间作为结束时间
            示例: 1708012345000
        
        query: 查询语句（可选，CLS 查询语法）
            示例: "level:ERROR" 或 "message:异常"
        
        limit: 返回结果数量限制（默认100，可选）

    Returns:
        Dict: 搜索结果
            - topic_id: 主题ID
            - start_time: 开始时间戳
            - end_time: 结束时间戳
            - query: 查询语句
            - limit: 结果限制
            - total: 实际返回的日志条数
            - logs: 日志列表，每条日志包含:
                * timestamp: 日志时间（格式: YYYY-MM-DD HH:MM:SS）
                * level: 日志级别
                * message: 日志内容
            - took_ms: 查询耗时（毫秒）
            - message: 查询状态消息
    
    使用示例:
        # 步骤1: 获取当前时间戳
        current_ts = get_current_timestamp()  # 返回: 1708012345000
        
        # 步骤2: 计算开始时间（15分钟前）
        start_ts = current_ts - (15 * 60 * 1000)  # 1708011445000
        
        # 步骤3: 搜索日志
        search_log(
            topic_id="topic-001",
            start_time=start_ts,     # int类型: 1708011445000
            end_time=current_ts,     # int类型: 1708012345000
            limit=100
        )
    """
    if _is_local_log_provider():
        return _search_local_logs(
            topic_id=topic_id,
            start_time=start_time,
            end_time=end_time,
            query=query,
            limit=limit,
        )
    if not (_is_mock_log_provider() and _allow_mock_provider()):
        return {
            "topic_id": topic_id,
            "start_time": start_time,
            "end_time": end_time,
            "query": query,
            "limit": limit,
            "total": 0,
            "logs": [],
            "took_ms": 0,
            **_provider_error_response("search_log"),
        }

    # 根据 topic_id 返回不同的结果
    if topic_id == "topic-001":
        # topic-001: 应用日志，动态生成 INFO 日志
        logs = []
        current_time_ms = start_time
        count = 0

        # 计算最大可生成的日志条数（基于时间范围）
        max_logs_by_time = int((end_time - start_time) / (60 * 1000)) + 1

        # 实际生成的日志数量取 limit 和时间范围内最大日志数的较小值
        actual_limit = min(limit, max_logs_by_time)

        while current_time_ms <= end_time and count < actual_limit:
            # 将毫秒时间戳转换为可读格式
            log_time = datetime.fromtimestamp(current_time_ms / 1000)
            time_str = log_time.strftime("%Y-%m-%d %H:%M:%S")

            log_entry = {
                "timestamp": time_str,
                "level": "INFO",
                "message": "正在同步元数据……"
            }

            logs.append(log_entry)
            count += 1

            # 下一条日志时间增加1分钟（60秒 * 1000毫秒）
            current_time_ms += 60 * 1000

        return {
            "topic_id": topic_id,
            "start_time": start_time,
            "end_time": end_time,
            "query": query,
            "limit": limit,
            "total": len(logs),
            "logs": logs,
            "took_ms": 50,
            "message": f"成功查询 {len(logs)} 条应用日志"
        }
    else:
        # 其他 topic_id: 返回错误，表示 topic 不存在
        return {
            "topic_id": topic_id,
            "start_time": start_time,
            "end_time": end_time,
            "query": query,
            "limit": limit,
            "total": 0,
            "logs": [],
            "took_ms": 0,
            "error": f"主题不存在: {topic_id}",
            "message": f"错误: 未找到主题 {topic_id}，请检查 topic_id 是否正确"
        }



if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8003, path="/mcp")
