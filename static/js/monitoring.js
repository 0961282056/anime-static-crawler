(function initializeMonitoring() {
    const config = document.getElementById('app-config');
    if (!config || !window.Sentry || !config.dataset.sentryDsn) {
        return;
    }

    window.Sentry.init({
        dsn: config.dataset.sentryDsn,
        environment: config.dataset.environment || 'production',
        integrations: [new window.Sentry.BrowserTracing()],
        tracesSampleRate: 0.1,
        replaysSessionSampleRate: 0,
        replaysOnErrorSampleRate: 0
    });
})();
