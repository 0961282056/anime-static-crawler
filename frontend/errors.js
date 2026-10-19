// Requires: settings
function createAbortError() {
    const error = new Error('Request aborted');
    error.name = 'AbortError';
    return error;
}
function isAbortError(error, signal = null) {
    return Boolean(signal && signal.aborted) || Boolean(error && error.name === 'AbortError');
}
function createDataError(code, userMessage, {retryable = false, status = null} = {}) {
    const error = new Error(userMessage);
    error.name = 'DataLoadError';
    error.code = code;
    error.status = Number.isInteger(status) ? status : null;
    error.retryable = retryable;
    error.userMessage = userMessage;
    return error;
}
function createHttpDataError(status) {
    const safeStatus = Number.isInteger(status) ? status : 0;
    let code = 'HTTP_ERROR';
    let userMessage = `資料服務拒絕請求（HTTP ${safeStatus || '未知'}）。請稍後再試。`;

    if (safeStatus === 404) {
        code = 'HTTP_404';
        userMessage = '找不到這個季度的資料（HTTP 404）。請確認季度是否已發布，或稍後再試。';
    } else if (safeStatus === 429) {
        code = 'HTTP_429';
        userMessage = '資料服務目前忙碌（HTTP 429）。';
    } else if (safeStatus >= 500 && safeStatus <= 599) {
        code = 'HTTP_5XX';
        userMessage = `資料服務暫時異常（HTTP ${safeStatus}）。`;
    } else if (safeStatus === 408 || safeStatus === 425) {
        code = `HTTP_${safeStatus}`;
        userMessage = `資料服務回應逾時或尚未準備完成（HTTP ${safeStatus}）。`;
    }

    return createDataError(code, userMessage, {
        retryable: isRetryableStatus(safeStatus),
        status: safeStatus
    });
}
function normalizeDataError(error) {
    if (error && error.code && error.userMessage) return error;
    return createDataError(
        'NETWORK',
        '無法連線到資料服務。',
        {retryable: true}
    );
}
function describeDataError(error, hasStaleData = false) {
    const normalized = normalizeDataError(error);
    const attempts = Number.isInteger(normalized.attempts) ? normalized.attempts : 0;
    let title = '資料載入失敗';
    let message = normalized.userMessage;

    if (normalized.code === 'NETWORK') title = '網路連線失敗';
    else if (normalized.code === 'FETCH_TIMEOUT') title = '資料請求逾時';
    else if (normalized.code === 'HTTP_404') title = '找不到季度資料';
    else if (normalized.code === 'HTTP_429') title = '資料服務忙碌';
    else if (normalized.code === 'HTTP_5XX') title = '資料服務暫時異常';
    else if (normalized.code === 'JSON_PARSE') {
        title = '資料內容錯誤';
        message = '資料檔案不是有效的 JSON，請稍後再試；若持續發生，請聯絡維護者。';
    } else if (normalized.code === 'DATA_SCHEMA') {
        title = '資料格式錯誤';
    }

    const finalAttempts = attempts || 1;
    if (normalized.retryable) {
        message += `系統已自動嘗試 ${finalAttempts} 次仍未成功，請稍後按「重新載入」再試。`;
    } else if (attempts > 1) {
        message += `（已嘗試 ${attempts} 次）`;
    }
    return {
        code: normalized.code || 'UNKNOWN',
        title,
        message,
        retryable: Boolean(normalized.retryable),
        hasStaleData,
    };
}

function isRetryableStatus(status) {
    return status === 408 || status === 425 || status === 429 || (status >= 500 && status <= 599);
}
