"""智能运维监控 MCP Server

本地实现的监控服务 MCP Server，提供：
- 监控数据查询（CPU、内存、磁盘、网络等）
- 进程信息查询
- 历史工单查询
- 服务信息查询

用于支持运维 Agent 的故障排查场景。
"""

import logging
import functools
import json
import os
import random
import time
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
logger = logging.getLogger("Monitor_MCP_Server")

mcp = FastMCP("Monitor")


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


# ============================================================
# 辅助函数
# ============================================================

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
    # 返回默认时间（当前时间 + 偏移）
    return datetime.now() + timedelta(hours=default_offset_hours)


def generate_time_series(base_time: datetime, minutes_offset: int, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """生成时间序列字符串。

    Args:
        base_time: 基准时间
        minutes_offset: 分钟偏移量
        format_str: 时间格式字符串

    Returns:
        str: 格式化的时间字符串
    """
    result_time = base_time + timedelta(minutes=minutes_offset)
    return result_time.strftime(format_str)


LOCAL_SAMPLE_SECONDS = float(os.getenv("AIOPS_LOCAL_SAMPLE_SECONDS", "0.25"))
LOCAL_PROVIDER_NAMES = {"local", "local_vps", "local_wsl", "wsl", "vps", "procfs"}
MOCK_PROVIDER_NAMES = {"mock", "demo", "sample"}


def _monitor_provider() -> str:
    return os.getenv("AIOPS_MONITOR_PROVIDER", "disabled").strip().lower() or "disabled"


def _is_local_provider() -> bool:
    return _monitor_provider() in LOCAL_PROVIDER_NAMES


def _allow_mock_provider() -> bool:
    return os.getenv("AIOPS_ALLOW_MOCK", "false").strip().lower() in {"1", "true", "yes", "on"}


def _is_mock_provider() -> bool:
    return _monitor_provider() in MOCK_PROVIDER_NAMES


def _provider_error_response(service_name: str, metric_name: str, interval: str) -> Dict[str, Any]:
    provider = _monitor_provider()
    if _is_mock_provider() and not _allow_mock_provider():
        error = "AIOps mock 监控数据已被禁用；拒绝返回假 CPU/内存曲线。"
        suggestion = "设置 AIOPS_MONITOR_PROVIDER=local_wsl/local_vps，或仅演示时显式设置 AIOPS_ALLOW_MOCK=true。"
    else:
        error = f"未配置可用监控数据源: AIOPS_MONITOR_PROVIDER={provider}"
        suggestion = "设置 AIOPS_MONITOR_PROVIDER=local_wsl/local_vps，并配置 AIOPS_SERVICE_PROCESS_MAP。"
    return {
        "service_name": service_name,
        "metric_name": metric_name,
        "interval": interval,
        "source": provider,
        "history_available": False,
        "data_points": [],
        "statistics": {},
        "processes": [],
        "alert_info": {
            "triggered": False,
            "threshold": None,
            "message": "监控数据源不可用，未判断告警状态",
        },
        "error": error,
        "suggestion": suggestion,
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


def _service_patterns(service_name: str) -> tuple[str, list[str]]:
    """从环境变量获取服务到进程关键字的映射；未配置时用服务名自身匹配。"""
    mapping = _load_json_env("AIOPS_SERVICE_PROCESS_MAP")
    matched_service = service_name
    raw_patterns = mapping.get(service_name) or mapping.get(service_name.lower())
    default_service = _default_service_name()
    if raw_patterns is None and default_service in mapping:
        matched_service = default_service
        raw_patterns = mapping[default_service]
        logger.warning(
            "未找到服务 %s 的进程映射，local 本机模式使用默认服务 %s 作为 fallback",
            service_name,
            default_service,
        )
    elif raw_patterns is None and len(mapping) == 1:
        only_service, raw_patterns = next(iter(mapping.items()))
        matched_service = str(only_service)
        logger.warning(
            "未找到服务 %s 的进程映射，local 本机模式使用唯一已配置服务 %s 作为 fallback",
            service_name,
            only_service,
        )
    if isinstance(raw_patterns, str):
        patterns = [item.strip() for item in raw_patterns.split("|") if item.strip()]
    elif isinstance(raw_patterns, list):
        patterns = [str(item).strip() for item in raw_patterns if str(item).strip()]
    else:
        patterns = [service_name]
    return matched_service, patterns


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _iter_processes(patterns: list[str]) -> list[dict[str, Any]]:
    """按关键字匹配 /proc 进程，返回 pid/name/cmdline。"""
    lowered_patterns = [pattern.lower() for pattern in patterns if pattern]
    matches: list[dict[str, Any]] = []
    current_pid = os.getpid()
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        if pid == current_pid:
            continue
        try:
            cmdline = (proc_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="ignore"
            ).strip()
            name = _read_text(proc_dir / "comm")
        except OSError:
            continue
        haystack = f"{name} {cmdline}".lower()
        if any(pattern in haystack for pattern in lowered_patterns):
            matches.append({"pid": pid, "name": name, "cmdline": cmdline or name})
    return matches


def _read_total_cpu_ticks() -> int:
    fields = _read_text(Path("/proc/stat")).splitlines()[0].split()[1:]
    return sum(int(float(item)) for item in fields)


def _read_idle_cpu_ticks() -> int:
    fields = _read_text(Path("/proc/stat")).splitlines()[0].split()[1:]
    # user nice system idle iowait irq softirq steal guest guest_nice
    idle = int(float(fields[3])) if len(fields) > 3 else 0
    iowait = int(float(fields[4])) if len(fields) > 4 else 0
    return idle + iowait


def _read_process_cpu_ticks(pid: int) -> int | None:
    try:
        fields = _read_text(Path(f"/proc/{pid}/stat")).split()
        return int(fields[13]) + int(fields[14])
    except (OSError, IndexError, ValueError):
        return None


def _read_mem_total_bytes() -> int:
    for line in _read_text(Path("/proc/meminfo")).splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return 0


def _read_mem_available_bytes() -> int:
    for line in _read_text(Path("/proc/meminfo")).splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return 0


def _read_process_rss_bytes(pid: int) -> int:
    try:
        fields = _read_text(Path(f"/proc/{pid}/statm")).split()
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(fields[1]) * int(page_size)
    except (OSError, IndexError, ValueError):
        return 0


def _local_cpu_snapshot(service_name: str) -> dict[str, Any]:
    """读取当前 Linux/WSL 真实 CPU 快照；不伪造历史曲线。"""
    matched_service, patterns = _service_patterns(service_name)
    processes = _iter_processes(patterns)

    total_1 = _read_total_cpu_ticks()
    idle_1 = _read_idle_cpu_ticks()
    proc_ticks_1 = {
        proc["pid"]: ticks
        for proc in processes
        if (ticks := _read_process_cpu_ticks(proc["pid"])) is not None
    }
    time.sleep(max(0.05, LOCAL_SAMPLE_SECONDS))
    total_2 = _read_total_cpu_ticks()
    idle_2 = _read_idle_cpu_ticks()
    proc_ticks_2 = {
        proc["pid"]: ticks
        for proc in processes
        if (ticks := _read_process_cpu_ticks(proc["pid"])) is not None
    }

    total_delta = max(total_2 - total_1, 1)
    busy_delta = max((total_2 - idle_2) - (total_1 - idle_1), 0)
    system_cpu_percent = round((busy_delta / total_delta) * 100, 2)
    cpu_count = os.cpu_count() or 1

    process_details = []
    process_cpu_total = 0.0
    for proc in processes:
        pid = proc["pid"]
        if pid not in proc_ticks_1 or pid not in proc_ticks_2:
            continue
        proc_delta = max(proc_ticks_2[pid] - proc_ticks_1[pid], 0)
        proc_cpu = round((proc_delta / total_delta) * cpu_count * 100, 2)
        process_cpu_total += proc_cpu
        process_details.append({**proc, "cpu_percent": proc_cpu})

    value = round(process_cpu_total, 2) if process_details else system_cpu_percent
    return {
        "value": value,
        "system_cpu_percent": system_cpu_percent,
        "scope": "process" if process_details else "system",
        "matched_service": matched_service,
        "patterns": patterns,
        "processes": process_details,
    }


def _local_memory_snapshot(service_name: str) -> dict[str, Any]:
    """读取当前 Linux/WSL 真实内存快照；不伪造历史曲线。"""
    matched_service, patterns = _service_patterns(service_name)
    processes = _iter_processes(patterns)
    total_bytes = _read_mem_total_bytes()
    available_bytes = _read_mem_available_bytes()
    used_bytes = max(total_bytes - available_bytes, 0)
    system_memory_percent = round((used_bytes / total_bytes) * 100, 2) if total_bytes else 0.0

    process_details = []
    process_rss_total = 0
    for proc in processes:
        rss = _read_process_rss_bytes(proc["pid"])
        if rss <= 0:
            continue
        process_rss_total += rss
        process_details.append(
            {
                **proc,
                "rss_mb": round(rss / 1024 / 1024, 2),
                "memory_percent": round((rss / total_bytes) * 100, 2) if total_bytes else 0.0,
            }
        )

    process_memory_percent = (
        round((process_rss_total / total_bytes) * 100, 2)
        if total_bytes and process_details
        else system_memory_percent
    )
    return {
        "value": process_memory_percent,
        "system_memory_percent": system_memory_percent,
        "used_gb": round((process_rss_total if process_details else used_bytes) / 1024**3, 2),
        "total_gb": round(total_bytes / 1024**3, 2) if total_bytes else 0.0,
        "scope": "process" if process_details else "system",
        "matched_service": matched_service,
        "patterns": patterns,
        "processes": process_details,
    }


def _single_point_response(
    *,
    service_name: str,
    metric_name: str,
    interval: str,
    snapshot: dict[str, Any],
    threshold: float,
    alert_message: str,
    normal_message: str,
) -> Dict[str, Any]:
    now = datetime.now()
    value = float(snapshot["value"])
    triggered = value > threshold
    return {
        "service_name": service_name,
        "matched_service": snapshot.get("matched_service", service_name),
        "metric_name": metric_name,
        "interval": interval,
        "source": f"{_monitor_provider()}:/proc",
        "history_available": False,
        "note": "local 本机模式读取当前 Linux/WSL /proc 快照；未部署时序采集器时不会伪造历史曲线。",
        "data_points": [
            {
                "timestamp": now.strftime("%H:%M:%S"),
                "value": value,
                "scope": snapshot.get("scope"),
            }
        ],
        "statistics": {
            "avg": value,
            "max": value,
            "min": value,
            "p95": value,
            **{k: v for k, v in snapshot.items() if k not in {"value", "processes"}},
        },
        "processes": snapshot.get("processes", []),
        "alert_info": {
            "triggered": triggered,
            "threshold": threshold,
            "message": alert_message if triggered else normal_message,
        },
    }





# ============================================================
# 监控数据查询工具
# ============================================================

@mcp.tool()
@log_tool_call
def query_cpu_metrics(
    service_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m"
) -> Dict[str, Any]:
    """查询服务的 CPU 使用率监控数据。

    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service"
        
        start_time: 开始时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 10:00:00"
            默认值: 如果不传，默认为当前时间的1小时前
            注意: 必须使用字符串格式，而非时间戳
        
        end_time: 结束时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 11:00:00"
            默认值: 如果不传，默认为当前时间
            注意: 必须使用字符串格式，而非时间戳
        
        interval: 数据聚合间隔（可选）
            可选值: "1m" (1分钟), "5m" (5分钟), "1h" (1小时)
            默认值: "1m"
            说明: 控制数据点的时间间隔

    Returns:
        Dict: CPU 监控数据
            - service_name: 服务名称
            - metric_name: 指标名称 (cpu_usage_percent)
            - interval: 数据聚合间隔
            - data_points: 数据点列表，每个点包含:
                * timestamp: 时间点（格式: HH:MM）
                * value: CPU 使用率百分比
            - statistics: 统计信息
                * average: 平均值
                * max: 最大值
                * min: 最小值
            - alert: 告警信息（如有）
                * triggered: 是否触发告警
                * threshold: 告警阈值
                * message: 告警消息
    
    使用示例:
        # 示例1: 使用默认时间（最近1小时）
        query_cpu_metrics(service_name="data-sync-service")
        
        # 示例2: 指定时间范围
        query_cpu_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00",
            end_time="2026-02-14 11:00:00",
            interval="5m"
        )
        
        # 示例3: 只指定开始时间（结束时间自动为当前时间）
        query_cpu_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00"
        )
    """
    if _is_local_provider():
        snapshot = _local_cpu_snapshot(service_name)
        return _single_point_response(
            service_name=service_name,
            metric_name="cpu_usage_percent",
            interval=interval,
            snapshot=snapshot,
            threshold=80.0,
            alert_message="CPU 使用率超过 80% 阈值",
            normal_message="CPU 使用率未超过 80% 阈值",
        )
    if not (_is_mock_provider() and _allow_mock_provider()):
        return _provider_error_response(service_name, "cpu_usage_percent", interval)

    # 解析时间参数
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    
    # 解析间隔时间（interval: 1m, 5m, 1h 等）
    interval_minutes = 1  # 默认 1 分钟
    if interval.endswith('m'):
        interval_minutes = int(interval[:-1])
    elif interval.endswith('h'):
        interval_minutes = int(interval[:-1]) * 60

    # 动态生成 CPU 使用率数据：从低到高逐渐增长
    data_points = []
    current_time = start_dt
    time_index = 0

    # 初始 CPU 使用率（10%）
    base_cpu = 10.0

    while current_time <= end_dt:
        # CPU 使用率逐渐升高的算法：
        # - 前几个数据点保持在 10% 左右
        # - 然后开始快速上升
        # - 最终达到 95% 左右

        if time_index < 3:
            # 初始阶段：10% 左右波动
            cpu_value = base_cpu + (time_index * 0.5)
        else:
            # 上升阶段：使用指数增长模型
            growth_factor = (time_index - 2) * 8.5
            cpu_value = min(base_cpu + growth_factor, 96.0)

        # 添加一些随机波动（±2%）
        cpu_value = round(cpu_value + random.uniform(-2, 2), 1)
        cpu_value = max(0, min(100, cpu_value))  # 确保在 0-100 范围内

        data_point = {
            "timestamp": current_time.strftime("%H:%M"),
            "value": cpu_value,
            "process_id": "pid-12345"
        }

        data_points.append(data_point)

        # 下一个时间点
        current_time += timedelta(minutes=interval_minutes)
        time_index += 1

    # 计算统计信息
    if data_points:
        values = [d["value"] for d in data_points]
        avg_value = round(sum(values) / len(values), 2)
        max_value = max(values)
        min_value = min(values)

        # 检测是否有 CPU 突增（超过 80%）
        spike_detected = max_value > 80.0

        return {
            "service_name": service_name,
            "metric_name": "cpu_usage_percent",
            "interval": interval,
            "data_points": data_points,
            "statistics": {
                "avg": avg_value,
                "max": max_value,
                "min": min_value,
                "p95": round(sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else max_value, 2),
                "spike_detected": spike_detected
            },
            "alert_info": {
                "triggered": spike_detected,
                "threshold": 80.0,
                "message": "CPU 使用率持续超过 80% 阈值" if spike_detected else "CPU 使用率正常"
            }
        }
    else:
        return {
            "service_name": service_name,
            "metric_name": "cpu_usage_percent",
            "interval": interval,
            "data_points": [],
            "statistics": {},
        }


@mcp.tool()
@log_tool_call
def query_memory_metrics(
    service_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m"
) -> Dict[str, Any]:
    """查询服务的内存使用监控数据。

    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service"
        
        start_time: 开始时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 10:00:00"
            默认值: 如果不传，默认为当前时间的1小时前
            注意: 必须使用字符串格式，而非时间戳
        
        end_time: 结束时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 11:00:00"
            默认值: 如果不传，默认为当前时间
            注意: 必须使用字符串格式，而非时间戳
        
        interval: 数据聚合间隔（可选）
            可选值: "1m" (1分钟), "5m" (5分钟), "1h" (1小时)
            默认值: "1m"

    Returns:
        Dict: 内存监控数据
            - service_name: 服务名称
            - metric_name: 指标名称 (memory_usage_percent)
            - interval: 数据聚合间隔
            - data_points: 数据点列表，每个点包含:
                * timestamp: 时间点（格式: HH:MM）
                * value: 内存使用率百分比
                * used_gb: 已使用内存（GB）
                * total_gb: 总内存（GB）
            - statistics: 统计信息
                * average: 平均值
                * max: 最大值
                * min: 最小值
            - alert: 告警信息（如有）
                * triggered: 是否触发告警
                * threshold: 告警阈值
                * message: 告警消息
    
    使用示例:
        # 示例1: 使用默认时间（最近1小时）
        query_memory_metrics(service_name="data-sync-service")
        
        # 示例2: 指定时间范围
        query_memory_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00",
            end_time="2026-02-14 11:00:00",
            interval="5m"
        )
    """
    if _is_local_provider():
        snapshot = _local_memory_snapshot(service_name)
        return _single_point_response(
            service_name=service_name,
            metric_name="memory_usage_percent",
            interval=interval,
            snapshot=snapshot,
            threshold=70.0,
            alert_message="内存使用率超过 70% 阈值，存在内存压力",
            normal_message="内存使用率未超过 70% 阈值",
        )
    if not (_is_mock_provider() and _allow_mock_provider()):
        return _provider_error_response(service_name, "memory_usage_percent", interval)

    # 解析时间参数
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)
    
    # 解析间隔时间（interval: 1m, 5m, 1h 等）
    interval_minutes = 1  # 默认 1 分钟
    if interval.endswith('m'):
        interval_minutes = int(interval[:-1])
    elif interval.endswith('h'):
        interval_minutes = int(interval[:-1]) * 60
    
    # 动态生成内存使用率数据：从低到高逐渐增长
    data_points = []
    current_time = start_dt
    time_index = 0
    
    # 初始内存使用率（30%）
    base_memory = 30.0
    total_gb = 8.0  # 总内存 8GB
    
    while current_time <= end_dt:
        # 内存使用率逐渐升高的算法：
        # - 前几个数据点保持在 30% 左右
        # - 然后开始逐步上升
        # - 最终达到 85% 左右
        
        if time_index < 3:
            # 初始阶段：30% 左右波动
            memory_value = base_memory + (time_index * 1.0)
        else:
            # 上升阶段：使用线性增长模型（内存增长比 CPU 慢）
            growth_factor = (time_index - 2) * 5.5
            memory_value = min(base_memory + growth_factor, 85.0)
        
        # 添加一些随机波动（±1%）
        memory_value = round(memory_value + random.uniform(-1, 1), 1)
        memory_value = max(0, min(100, memory_value))  # 确保在 0-100 范围内
        
        # 计算已使用内存（GB）
        used_gb = round((memory_value / 100.0) * total_gb, 2)
        
        data_point = {
            "timestamp": current_time.strftime("%H:%M"),
            "value": memory_value,
            "used_gb": used_gb,
            "total_gb": total_gb
        }
        
        data_points.append(data_point)
        
        # 下一个时间点
        current_time += timedelta(minutes=interval_minutes)
        time_index += 1
    
    # 计算统计信息
    if data_points:
        values = [d["value"] for d in data_points]
        avg_value = round(sum(values) / len(values), 2)
        max_value = max(values)
        min_value = min(values)
        
        # 检测是否有内存压力（超过 70%）
        memory_pressure = max_value > 70.0
        
        return {
            "service_name": service_name,
            "metric_name": "memory_usage_percent",
            "interval": interval,
            "data_points": data_points,
            "statistics": {
                "avg": avg_value,
                "max": max_value,
                "min": min_value,
                "p95": round(sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else max_value, 2),
                "memory_pressure": memory_pressure
            },
            "alert_info": {
                "triggered": memory_pressure,
                "threshold": 70.0,
                "message": "内存使用率超过 70% 阈值，存在内存压力" if memory_pressure else "内存使用率正常"
            }
        }
    else:
        return {
            "service_name": service_name,
            "metric_name": "memory_usage_percent",
            "interval": interval,
            "data_points": [],
            "statistics": {},
            "error": "时间范围无效或没有生成数据点"
        }




if __name__ == "__main__":
    # 使用 streamable-http 模式，运行在 8004 端口
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8004, path="/mcp")
