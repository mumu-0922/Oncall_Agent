import assert from 'node:assert/strict';
import { normalizeTraceToolPayload, parseJsonLoose } from './tracePayload.js';

const prometheusPayload = {
    tool: 'query_metric_instant',
    source: 'prometheus:http://127.0.0.1:9090',
    result_type: 'vector',
    result_count: 1,
    query: '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    results: [{ metric: { instance: 'node-exporter:9100' }, value: [1782290000, '1.2'] }],
};

const mcpTextWrapped = JSON.stringify([
    {
        type: 'text',
        text: JSON.stringify(prometheusPayload),
    },
]);

assert.deepEqual(normalizeTraceToolPayload({ summary: mcpTextWrapped }), prometheusPayload);
assert.equal(
    normalizeTraceToolPayload({ summary: JSON.stringify(prometheusPayload) }).source,
    'prometheus:http://127.0.0.1:9090',
);
assert.equal(
    normalizeTraceToolPayload({ metadata: { payload: prometheusPayload } }).tool,
    'query_metric_instant',
);
assert.equal(parseJsonLoose(`prefix ${JSON.stringify(prometheusPayload)} suffix`).tool, 'query_metric_instant');
assert.equal(normalizeTraceToolPayload({ summary: '普通文本，不是工具 JSON' }), null);

console.log('tracePayload parser tests passed');
