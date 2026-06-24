const EVIDENCE_KEYS = [
    'tool',
    'source',
    'history_available',
    'result_type',
    'result_count',
    'series_count',
    'point_count',
    'total',
    'statistics',
    'alert_info',
    'query',
    'results',
    'alerts',
    'error',
    'limited',
    'duration_ms',
];

export function normalizeTraceToolPayload(event) {
    const candidates = [
        event?.summary,
        event?.result,
        event?.output,
        event?.content,
        event?.metadata?.payload,
        event?.metadata?.raw,
    ];

    for (const candidate of candidates) {
        const parsed = parseJsonLoose(candidate);
        const normalized = normalizeToolTextPayload(parsed);
        if (normalized) {
            return normalized;
        }
    }

    return null;
}

export function parseJsonLoose(value) {
    if (value === null || value === undefined) {
        return null;
    }
    if (typeof value === 'object') {
        return value;
    }
    if (typeof value !== 'string') {
        return null;
    }

    let text = value.trim();
    if (!text) {
        return null;
    }

    const fencedMatch = text.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
    if (fencedMatch) {
        text = fencedMatch[1].trim();
    }

    for (let attempt = 0; attempt < 4; attempt += 1) {
        try {
            const parsed = JSON.parse(text);
            if (typeof parsed === 'string') {
                const nextText = parsed.trim();
                if (!nextText || nextText === text) {
                    return parsed;
                }
                text = nextText;
                continue;
            }
            return parsed;
        } catch (e) {
            const jsonSlice = extractJsonSlice(text);
            if (!jsonSlice || jsonSlice === text) {
                return null;
            }
            text = jsonSlice;
        }
    }

    return null;
}

export function normalizeToolTextPayload(value) {
    if (value === null || value === undefined) {
        return null;
    }

    if (Array.isArray(value)) {
        for (const item of value) {
            const normalized = normalizeToolTextPayload(item);
            if (normalized) {
                return normalized;
            }
        }
        return null;
    }

    if (typeof value === 'string') {
        return normalizeToolTextPayload(parseJsonLoose(value));
    }

    if (typeof value !== 'object') {
        return null;
    }

    const textFields = ['text', 'content', 'message'];
    for (const field of textFields) {
        if (typeof value[field] === 'string') {
            const nested = normalizeToolTextPayload(parseJsonLoose(value[field]));
            if (nested) {
                return nested;
            }
        }
    }

    if (value.data && typeof value.data === 'object') {
        const nested = normalizeToolTextPayload(value.data);
        if (nested) {
            return nested;
        }
    }

    return hasEvidencePayloadShape(value) ? value : null;
}

export function hasEvidencePayloadShape(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return false;
    }

    return EVIDENCE_KEYS.some(key => Object.prototype.hasOwnProperty.call(value, key));
}

function extractJsonSlice(text) {
    const firstObject = text.indexOf('{');
    const firstArray = text.indexOf('[');
    const starts = [firstObject, firstArray].filter(index => index >= 0);
    if (starts.length === 0) {
        return null;
    }

    const start = Math.min(...starts);
    const opener = text[start];
    const closer = opener === '{' ? '}' : ']';
    const end = text.lastIndexOf(closer);
    if (end <= start) {
        return null;
    }
    return text.slice(start, end + 1).trim();
}
