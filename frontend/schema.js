// Requires: settings, errors
const OFFICIAL_STORY_SOURCES = JSON.parse(document.getElementById('app-config')?.dataset.officialStorySources || '{}');

function officialStoryUrl(anime) {
    const expected = OFFICIAL_STORY_SOURCES[anime.bangumi_id];
    const source = anime.story_source;
    const exactOfficialIdentity = Array.isArray(expected) &&
        ((Number.isInteger(expected[0]) && expected[0] > 0 && expected.length === 2) ||
         (expected[0] === null && expected.length === 4 && anime.anime_name === expected[2] && anime.original_name === expected[3]));
    return exactOfficialIdentity &&
        typeof expected[1] === 'string' && /^https:\/\//.test(expected[1]) &&
        source?.provider === 'official' && source.method === 'manual_official' &&
        source.subject_id === expected[0] && anime.external_ids?.bangumi === expected[0] &&
        source.source_url === expected[1] ? expected[1] : null;
}
function validateDataset(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw createDataError('DATA_SCHEMA', '資料檔案格式不完整或已變更，無法安全顯示。', {retryable: false});
    }
    if (!Array.isArray(payload.anime_list)) {
        throw createDataError('DATA_SCHEMA', '資料檔案缺少動畫清單，無法安全顯示。', {retryable: false});
    }

    const optionalStringFields = ['bangumi_id', 'anime_image_url', 'premiere_date', 'premiere_time', 'story'];
    payload.anime_list.forEach((item, index) => {
        if (!item || typeof item !== 'object' || Array.isArray(item)) {
            throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫資料格式錯誤，無法安全顯示。`, {retryable: false});
        }
        if (typeof item.anime_name !== 'string' || !item.anime_name.trim()) {
            throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫資料缺少名稱，無法安全顯示。`, {retryable: false});
        }
        optionalStringFields.forEach(field => {
            if (item[field] !== undefined && item[field] !== null && typeof item[field] !== 'string') {
                throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫資料的欄位格式錯誤，無法安全顯示。`, {retryable: false});
            }
        });
        if (item.episodes !== undefined && item.episodes !== null) {
            const info = item.episodes;
            const validCount = (value, minimum) => value === null || value === undefined || (Number.isInteger(value) && value >= minimum);
            if (typeof info !== 'object' || Array.isArray(info) ||
                !validCount(info.total_episodes, 1) || !validCount(info.aired_episodes, 0) ||
                !Object.hasOwn(AIRING_STATUS_LABELS, info.airing_status) ||
                !['unknown', 'planned', 'confirmed'].includes(info.episode_count_status) ||
                !['ok', 'unmatched', 'stale', 'conflict'].includes(info.fetch_state) ||
                (info.note !== undefined && typeof info.note !== 'string') ||
                (info.source_urls !== undefined && (!Array.isArray(info.source_urls) || info.source_urls.some(url => typeof url !== 'string'))) ||
                (info.source_kind !== undefined && !['unknown', 'bangumi_api', 'bangumi_html', 'bangumi_archive', 'manual_official', 'official_catalogue'].includes(info.source_kind)) ||
                (info.episode_count_status !== 'unknown' && !Number.isInteger(info.total_episodes)) ||
                (info.airing_status === 'not_yet_aired' && Number.isInteger(info.aired_episodes) && info.aired_episodes !== 0) ||
                (Number.isInteger(info.total_episodes) && Number.isInteger(info.aired_episodes) && info.aired_episodes > info.total_episodes) ||
                (info.airing_status === 'finished' && (!Number.isInteger(info.total_episodes) || info.aired_episodes !== info.total_episodes || info.episode_count_status !== 'confirmed'))) {
                throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫的集數資料格式錯誤，無法安全顯示。`, {retryable: false});
            }
        }
        if (item.story_source !== undefined && item.story_source !== null) {
            const source = item.story_source;
            const official = source?.provider === 'official' && source.method === 'manual_official' && officialStoryUrl(item) !== null;
            const bangumi = source?.provider === 'bangumi' && ['bangumi_api', 'bangumi_archive'].includes(source.method);
            const validSubject = (Number.isInteger(source?.subject_id) && source.subject_id > 0) || (official && source.subject_id === null);
            if (typeof source !== 'object' || Array.isArray(source) || (!official && !bangumi) || !validSubject || source.subject_id !== item.external_ids?.bangumi || (source.method === 'bangumi_archive' && (typeof source.source_snapshot_date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(source.source_snapshot_date)))) {
                throw createDataError('DATA_SCHEMA', `第 ${index + 1} 筆動畫的簡介來源無法確認。`, {retryable: false});
            }
        }
    });

    return payload;
}
function validateSearchIndex(data, app) {
    const fail = () => { throw createDataError('SEARCH_SCHEMA', '搜尋資料不完整或版本不一致，請重新載入頁面。', {retryable: false}); };
    if (!data || data.schema_version !== 1 || data.data_version !== app.dataVersion || !Array.isArray(data.entries) || data.entries.length > 50000) fail();
    const quarters = Object.entries(app.availableData).flatMap(([year, seasons]) => seasons.map(season => `${year}_${season}`));
    const versions = data.quarter_versions;
    if (!versions || Object.keys(versions).length !== quarters.length || quarters.some(key => !/^[a-f0-9]{64}$/.test(versions[key]) || (app.quarterVersions[key] && app.quarterVersions[key] !== versions[key]))) fail();
    const seen = new Set();
    for (const row of data.entries) {
        const id = row?.identity;
        if (!Array.isArray(id) || id.length !== 4 || !/^anime-\d+$/.test(id[0]) || typeof id[1] !== 'string' || !id[1] || typeof id[2] !== 'string' || !(id[3] === null || (Number.isInteger(id[3]) && id[3] > 0)) || typeof row.story !== 'string' || row.story.length > 50000 || !Array.isArray(row.quarters) || !row.quarters.length || new Set(row.quarters).size !== row.quarters.length || row.quarters.some(key => !quarters.includes(key))) fail();
        const key = JSON.stringify(id);
        if (seen.has(key)) fail();
        seen.add(key);
    }
    return data;
}
