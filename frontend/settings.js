// Requires: browser platform only
const DATA_CACHE_TTL_MS = 5 * 60 * 1000;
const FETCH_TIMEOUT_MS = 15 * 1000;
const MAX_FETCH_RETRIES = 2;
const RETRY_DELAYS_MS = [300, 700];
const HTML2CANVAS_TIMEOUT_MS = 15 * 1000;
const SHARE_IMAGE_TIMEOUT_MS = 15 * 1000;
const BROADCAST_DAY_LABELS = {
    '一': '週一', '二': '週二', '三': '週三', '四': '週四', '五': '週五', '六': '週六', '日': '週日',
    '一至五': '週一至五', '不定期公開': '不定期公開', '無首播日期': '播出日待確認'
};
const AIRING_STATUS_LABELS = {
    unknown: '狀態待確認', not_yet_aired: '尚未播出', airing: '播出中',
    finished: '本季已播畢', paused: '暫停播出', cancelled: '取消播出'
};
function readAppConfig() {
    const element = document.getElementById('app-config');
    if (!element) {
        throw new Error('Missing #app-config build metadata');
    }
    return {
        defaultYear: element.dataset.defaultYear,
        defaultSeason: element.dataset.defaultSeason,
        buildVersion: element.dataset.buildVersion,
        dataVersion: element.dataset.dataVersion || element.dataset.buildVersion,
        quarterVersions: JSON.parse(element.dataset.quarterVersions || '{}'),
        reviewedBroadcastSources: JSON.parse(element.dataset.reviewedBroadcastSources || '{}'),
        availableData: JSON.parse(element.dataset.availableData)
    };
}
