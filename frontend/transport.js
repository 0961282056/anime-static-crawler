// Requires: settings, errors
const pendingRequestStates = new WeakMap();
function getPendingRequestState(owner) {
    // Alpine may invoke methods through different scope proxies for one app.
    const key = owner._pendingDataRequests;
    let state = pendingRequestStates.get(key);
    if (!state) {
        state = new Map();
        pendingRequestStates.set(key, state);
    }
    return state;
}
function waitWithAbort(delayMs, signal) {
    if (!signal) {
        return new Promise(resolve => setTimeout(resolve, delayMs));
    }
    if (signal.aborted) {
        return Promise.reject(createAbortError());
    }

    return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (callback, value) => {
            if (settled) return;
            settled = true;
            clearTimeout(timerId);
            signal.removeEventListener('abort', onAbort);
            callback(value);
        };
        const onAbort = () => finish(reject, createAbortError());
        const timerId = setTimeout(() => finish(resolve), delayMs);
        signal.addEventListener('abort', onAbort, {once: true});
    });
}
function raceWithAbort(promise, signal) {
    if (!signal) return promise;
    if (signal.aborted) return Promise.reject(createAbortError());

    return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (callback, value) => {
            if (settled) return;
            settled = true;
            signal.removeEventListener('abort', onAbort);
            callback(value);
        };
        const onAbort = () => finish(reject, createAbortError());
        signal.addEventListener('abort', onAbort, {once: true});
        promise.then(
            value => finish(resolve, value),
            error => finish(reject, error)
        );
    });
}
function subscribeToDataset(entry, signal) {
    const subscriber = Symbol('dataset-subscriber');
    entry.subscribers.add(subscriber);
    let released = false;
    const release = () => {
        if (released) return;
        released = true;
        entry.subscribers.delete(subscriber);
        // A replacement load in the same turn may still need this request.
        Promise.resolve().then(() => {
            if (!entry.settled && entry.subscribers.size === 0) entry.abort();
        });
    };
    if (signal) signal.addEventListener('abort', release, {once: true});
    return raceWithAbort(entry.promise, signal).finally(() => {
        if (signal) signal.removeEventListener('abort', release);
        release();
    });
}
async function fetchWithDeadline(url, signal, readResponse = response => response, timeoutMs = FETCH_TIMEOUT_MS) {
    const requestController = new AbortController();
    let timedOut = false;
    let timeoutId;
    const abortFromCaller = () => requestController.abort();

    if (signal) {
        if (signal.aborted) throw createAbortError();
        signal.addEventListener('abort', abortFromCaller, {once: true});
    }

    timeoutId = setTimeout(() => {
        timedOut = true;
        requestController.abort();
    }, timeoutMs);

    try {
        const response = await fetch(url, {signal: requestController.signal});
        return await readResponse(response);
    } catch (error) {
        if (signal && signal.aborted) throw createAbortError();
        if (timedOut) {
            const timeoutSeconds = Math.max(1, Math.ceil(Number(timeoutMs) / 1000));
            throw createDataError(
                'FETCH_TIMEOUT',
                `資料請求逾時（超過 ${timeoutSeconds} 秒）。`,
                {retryable: true}
            );
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
        if (signal) signal.removeEventListener('abort', abortFromCaller);
    }
}
async function fetchJsonWithDeadline(url, signal, timeoutMs = FETCH_TIMEOUT_MS) {
    return fetchWithDeadline(url, signal, async response => {
        if (!response.ok) return {response, data: null};
        try {
            return {response, data: await response.json()};
        } catch (error) {
            throw createDataError(
                'JSON_PARSE',
                '資料檔案不是有效的 JSON，請稍後再試；若持續發生，請聯絡維護者。',
                {retryable: false}
            );
        }
    }, timeoutMs);
}
