# MCP Servers

为 AIOps 智能诊断提供日志查询和监控数据工具。

## 📚 服务列表

### CLS Server (`cls_server.py`)
**日志查询服务** - 端口 8003

**核心工具：**
- `get_current_timestamp` - 获取当前时间戳
- `get_topic_info_by_name` - 查询日志主题
- `get_region_code_by_name` - 查询地区代码；本机模式支持 `local/vps/本机/本地`
- `search_topic_by_service_name` - 根据服务名查找日志 topic
- `search_log` - 日志搜索

### Monitor Server (`monitor_server.py`)
**监控数据服务** - 端口 8004

**核心工具：**
- `query_cpu_metrics` - CPU 使用率查询
- `query_memory_metrics` - 内存使用查询
- `query_metric_instant` - Prometheus instant query（`/api/v1/query`）
- `query_metric_range` - Prometheus range query（`/api/v1/query_range`，带点数/序列限制）
- `list_active_alerts` - Alertmanager 当前活跃告警（`/api/v2/alerts`）
- `query_alert_history` - 告警历史能力说明；Alertmanager 未接归档时明确返回 `history_available=false`

## 🚀 快速开始

### 安装依赖
```bash
pip install fastmcp
```

### 启动服务

**方式一：使用 Makefile（推荐）**
```bash
make mcp-start   # 启动所有 MCP 服务
make mcp-stop    # 停止所有 MCP 服务
make mcp-status  # 查看服务状态
```

**方式二：手动启动**
```bash
python mcp_servers/cls_server.py
python mcp_servers/monitor_server.py
```

## 💡 使用示例

### AIOps 诊断场景

```
用户: data-sync-service 出现告警，请排查

Agent 自动执行:
1. list_active_alerts() → 查看 Alertmanager 当前活跃告警
2. query_cpu_metrics("data-sync-service") → CPU 指标；Prometheus 可用时查真实历史曲线
3. query_memory_metrics("data-sync-service") → 内存指标；Prometheus 可用时查真实历史曲线
4. search_topic_by_service_name("data-sync-service") → 查找日志 topic
5. search_log(topic_id, start_time, end_time, query="level:ERROR OR level:WARN") → 同窗口错误日志
6. Evidence Package + Analyzer Findings → 生成诊断报告；没证据则返回证据不足
```

### 工具参数示例

**查询 CPU 指标：**
```python
query_cpu_metrics(
    service_name="data-sync-service",
    start_time="2024-02-14 02:00:00",
    end_time="2024-02-14 03:00:00",
    interval="1m"
)
```

**查询 Prometheus 原始 PromQL：**
```python
query_metric_range(
    query='rate(http_requests_total{job="api"}[5m])',
    start_time="2024-02-14 02:00:00",
    end_time="2024-02-14 03:00:00",
    step="60s",
    max_points=500,
)
```

**查询 Alertmanager 当前活跃告警：**
```python
list_active_alerts(label_filter='severity="critical"')
```

**搜索错误日志：**
```python
search_log(
    topic_id="local:super-biz-agent",
    start_time=1718359200000,
    end_time=1718362800000,
    query="level:ERROR OR timeout",
    limit=100
)
```

## 🔧 高级配置

### 接入真实观测源

默认禁止 mock。未配置真实数据源时，工具会返回 `error` 和 `suggestion`，不会生成假曲线。

**本机/WSL 最短链路：**
```bash
export AIOPS_MONITOR_PROVIDER=local_wsl
export AIOPS_LOG_PROVIDER=local_wsl
export AIOPS_ALLOW_MOCK=false
```

该模式读取 `/proc` 当前快照，`history_available=false`，不会伪造历史曲线。

**VPS 推荐链路：Prometheus + node_exporter + Alertmanager**
```bash
export AIOPS_MONITOR_PROVIDER=prometheus
export AIOPS_PROMETHEUS_URL=http://127.0.0.1:9090
export AIOPS_ALERTMANAGER_URL=http://127.0.0.1:9093
export AIOPS_PROMETHEUS_MAX_POINTS=500
export AIOPS_PROMETHEUS_MAX_SERIES=50
```

可选 PromQL 模板：
```bash
export AIOPS_PROMETHEUS_CPU_QUERY_TEMPLATE='100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))'
export AIOPS_PROMETHEUS_MEMORY_QUERY_TEMPLATE='100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))'
```

**腾讯云 CLS：**
```bash
# 安装 SDK
pip install tencentcloud-sdk-python

# 配置环境变量
export TENCENTCLOUD_SECRET_ID="your-id"
export TENCENTCLOUD_SECRET_KEY="your-key"

# 在 cls_server.py 中集成
from tencentcloud.cls.v20201016 import cls_client
```

**其他监控系统：**
- Prometheus
- Grafana
- 云监控（腾讯云/阿里云/AWS）
- 自建监控平台

### Mock 数据

仅演示时显式开启：

```bash
export AIOPS_MONITOR_PROVIDER=mock
export AIOPS_ALLOW_MOCK=true
```

未设置 `AIOPS_ALLOW_MOCK=true` 时，mock provider 会被拒绝。

## 📚 参考资料

- [FastMCP 文档](https://github.com/jlowin/fastmcp)
- [MCP 协议](https://modelcontextprotocol.io/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [主项目 README](../README.md)

---

**注意**: 诊断报告只应引用工具返回的真实证据。Prometheus/Alertmanager/本机采集不可用时，系统应返回真实错误原因或“证据不足”，不得补故事。
