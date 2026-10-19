// Requires: errors, data
const searchMethods = {
    async getAllQuarterData(signal) {
        const keyword = this.searchKeyword.normalize('NFKC').toLowerCase().trim();
        const index = await this.getDataset('__search_index__', signal);
        if (signal.aborted) throw createAbortError();
        const matches = index.entries.filter(row => [row.identity[1], row.identity[2], row.story]
            .some(value => value.normalize('NFKC').toLowerCase().includes(keyword)));
        const quarters = [...new Set(matches.map(row => row.quarters[0]))];
        const datasets = new Map();
        let next = 0;
        let stopped = false;
        const worker = async () => {
            while (!stopped && next < quarters.length) {
                if (signal.aborted) throw createAbortError();
                const quarter = quarters[next++];
                try {
                    const dataset = await this.getDataset(quarter, signal);
                    const records = new Map(dataset.anime_list.map(anime => [JSON.stringify([anime.bangumi_id, anime.anime_name, anime.original_name || '', anime.external_ids?.bangumi || null]), anime]));
                    datasets.set(quarter, records);
                } catch (error) {
                    stopped = true;
                    throw error;
                }
            }
        };
        await Promise.all(Array.from({length: Math.min(4, quarters.length)}, worker));
        if (signal.aborted) throw createAbortError();
        const anime_list = matches.map(row => {
            const key = JSON.stringify(row.identity);
            const anime = datasets.get(row.quarters[0]).get(key);
            if (!anime) throw createDataError('SEARCH_VERSION', '搜尋資料已有更新，請重新載入頁面後再試。', {retryable: false});
            return {...anime, result_key: key, source_quarters: row.quarters.map(quarter => `${quarter.slice(0, 4)}年${quarter.slice(-1)}季`)};
        });
        return {anime_list, generated_at: index.generated_at};
    },

    animeResultKey(anime) {
        return anime.result_key || anime.bangumi_id || anime.anime_name;
    },

    sourceQuarterLabel(anime) {
        return (anime.source_quarters || []).join('、');
    },

    get filteredAnime() {
        let list = this.rawAnimeList;

        if (this.filterDay !== '全部') {
            list = list.filter(item => (item.premiere_date || '無首播日期') === this.filterDay ||
                (item.premiere_date === '一至五' && ['一', '二', '三', '四', '五'].includes(this.filterDay)));
        }
        if (this.filterStatus !== '全部') {
            list = list.filter(item => (item.episodes?.airing_status || 'unknown') === this.filterStatus);
        }

        if (this.searchKeyword.trim()) {
            const k = this.searchKeyword.normalize('NFKC').toLowerCase().trim();
            list = list.filter(item => [item.anime_name, item.original_name, item.story]
                .some(value => typeof value === 'string' && value.normalize('NFKC').toLowerCase().includes(k))
            );
        }
        return list;
    }
};
