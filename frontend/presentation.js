// Requires: settings, schema, images
const presentationMethods = {
    seasonLabel(season) {
        const monthBySeason = {'冬': 1, '春': 4, '夏': 7, '秋': 10};
        return `${season}(${monthBySeason[season]}月)`;
    },

    animeSearchUrl(name) {
        return `https://ani.gamer.com.tw/search.php?keyword=${encodeURIComponent(name)}`;
    },

    cardImageUrl(anime, width = 600) {
        const sourceUrl = anime && anime.anime_image_url;
        if (!sourceUrl || sourceUrl === '無圖片') {
            return 'https://placehold.co/300x450/333/999?text=No+Image';
        }
        return buildCloudinaryTransformationUrl(sourceUrl, `c_limit,w_${width},f_auto,q_auto`) || sourceUrl;
    },

    cardImageSrcset(anime) {
        const sourceUrl = anime && anime.anime_image_url;
        if (!sourceUrl || sourceUrl === '無圖片') return '';
        const widths = [300, 600, 900];
        const urls = widths.map(width => {
            const imageUrl = buildCloudinaryTransformationUrl(
                sourceUrl,
                `c_limit,w_${width},f_auto,q_auto`
            );
            return imageUrl ? `${imageUrl} ${width}w` : null;
        }).filter(Boolean);
        return urls.join(', ');
    },

    episodeCountLabel(anime) {
        const info = anime.episodes;
        if (!info) return '集數待確認';
        const aired = Number.isInteger(info.aired_episodes) ? info.aired_episodes : null;
        const total = Number.isInteger(info.total_episodes) ? info.total_episodes : null;
        if (info.fetch_state === 'conflict') {
            return aired === null ? '集數待確認' : `已播 ${aired} 集／總集數待確認`;
        }
        if (info.airing_status === 'finished' && total !== null) return `全 ${total} 集`;
        if (total !== null && aired !== null) {
            return info.episode_count_status === 'planned'
                ? `已播 ${aired} 集／預定 ${total} 集`
                : `已播 ${aired}／${total} 集`;
        }
        if (aired !== null) return `已播 ${aired} 集／總集數未定`;
        if (total !== null) return `${info.episode_count_status === 'planned' ? '預定' : '共'} ${total} 集／進度待確認`;
        return '集數待確認';
    },

    airingStatusLabel(anime) {
        return AIRING_STATUS_LABELS[anime.episodes?.airing_status || 'unknown'];
    },

    episodeMetadataDetail(anime) {
        const info = anime.episodes;
        if (!info) return '尚未取得可靠的集數資料。';
        const checked = info.checked_at ? new Date(info.checked_at) : null;
        const stamp = checked && !Number.isNaN(checked.getTime())
            ? `最後確認：${checked.toLocaleString('zh-TW', {timeZone: 'Asia/Taipei'})}` : '尚未確認';
        return `${stamp}。${info.note || '集數以對應動畫條目的正篇為範圍。'}`;
    },

    episodeSourceKind(anime) {
        const info = anime.episodes;
        if (!info) return '尚無可靠來源';
        const labels = {bangumi_api: 'Bangumi API', bangumi_html: 'Bangumi 頁面備援', bangumi_archive: 'Bangumi 固定資料快照', manual_official: '已人工核對的官方快照', official_catalogue: '官方完整節目目錄'};
        if (Object.hasOwn(labels, info.source_kind)) return labels[info.source_kind];
        if ((info.note || '').startsWith('Bangumi 官方資料快照')) return 'Bangumi 固定快照（依既有備註）';
        if ((info.note || '').startsWith('Bangumi 頁面備援')) return 'Bangumi 頁面備援（依既有備註）';
        const first = this.episodeSourceLinks(anime)[0];
        if (first && !['bgm.tv', 'bangumi.tv', 'chii.in', 'anilist.co'].includes(new URL(first.url).hostname)) return '已審查的外部來源';
        return anime.external_ids?.bangumi ? 'Bangumi（舊資料未記錄取得方式）' : '舊資料未記錄取得方式';
    },

    storySourceUrl(anime) {
        const id = anime.story_source?.subject_id;
        if (anime.story_source?.provider === 'official') return officialStoryUrl(anime);
        return Number.isInteger(id) && id > 0 && id === anime.external_ids?.bangumi ? `https://bgm.tv/subject/${id}` : null;
    },

    storySourceLabel(anime) {
        const source = anime.story_source;
        if (!source || !this.storySourceUrl(anime)) return '';
        if (source.provider === 'official') return '簡介來源：官方作品介紹原文（節錄）';
        return source.method === 'bangumi_archive'
            ? `簡介來源：Bangumi 原文快照（截至 ${source.source_snapshot_date || '日期未記錄'}）`
            : '簡介來源：Bangumi 條目原文';
    },

    formatSourceTimestamp(value) {
        if (typeof value !== 'string' || !value.trim()) return '尚未記錄';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '尚未記錄' : date.toLocaleString('zh-TW', {timeZone: 'Asia/Taipei'});
    },

    episodeVerificationState(anime) {
        const info = anime.episodes;
        if (!info || this.formatSourceTimestamp(info.checked_at) === '尚未記錄') return '尚未成功查證';
        if (info.fetch_state === 'stale') return '最近更新失敗，保留上次成功資料';
        if (info.fetch_state === 'conflict') return '來源有差異，需核對計數範圍';
        if (['manual_official', 'bangumi_archive'].includes(info.source_kind) || (info.note || '').startsWith('Bangumi 官方資料快照')) return '固定快照，需定期複查';
        return '已記錄成功查證時間；不代表即時播出進度';
    },

    episodeSourceLinks(anime) {
        const sources = Array.isArray(anime.episodes?.source_urls) ? anime.episodes.source_urls : [];
        const links = [];
        for (const source of sources) {
            try {
                const url = new URL(source);
                if (url.protocol !== 'https:' || url.username || url.password || url.hash || links.some(link => link.url === url.href)) continue;
                links.push({url: url.href, label: url.hostname === 'bgm.tv' ? 'Bangumi 集數證據' : url.hostname});
            } catch { /* Preserve valid evidence links; ignore malformed values. */ }
        }
        const id = anime.external_ids?.bangumi;
        if (!links.length && Number.isInteger(id) && id > 0) links.push({url: `https://bgm.tv/subject/${id}/ep`, label: 'Bangumi 條目（參考）'});
        const anilist = anime.external_ids?.anilist;
        if (Number.isInteger(anilist) && anilist > 0) links.push({url: `https://anilist.co/anime/${anilist}`, label: 'AniList 條目（參考；不代表計數範圍已一致）'});
        return links;
    },

    episodeSourceUrl(anime) {
        for (const source of anime.episodes?.source_urls || []) {
            try {
                const url = new URL(source);
                if (url.protocol === 'https:' && !url.username && !url.password && !url.hash) return url.href;
            } catch { /* Invalid source: fall back to the canonical provider link. */ }
        }
        const id = anime.external_ids?.bangumi;
        return Number.isInteger(id) && id > 0 ? `https://bgm.tv/subject/${id}/ep` : null;
    },

    episodeUpdateWarning(anime) {
        if (anime.episodes?.fetch_state === 'stale') return anime.episodes.checked_at ? '更新未成功，資料可能落後' : '暫時無法取得集數';
        if (anime.episodes?.fetch_state === 'conflict') return '來源資料待核對';
        return '';
    },

    showStory(title, story) {
        Swal.fire({
            titleText: title,
            text: story || '暫無簡介',
            background: '#1e1e1e', color: '#e0e0e0',
            confirmButtonColor: '#bb86fc'
        });
    },

    scrollToTop() {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
};
