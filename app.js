/**
 * Aniverse - Main Application
 * Searches MyAnimeList & AniList, embeds StreamP2P player, toggles hard/soft sub
 * Auto-syncs with StreamP2P library on load
 */

// ===== Configuration =====
const CONFIG = {
    JIKAN_BASE: 'https://api.jikan.moe/v4',
    ANILIST_BASE: 'https://graphql.anilist.co',
    STREAMP2P_BASE: 'https://streamp2p.com',
    STREAMP2P_API: 'https://streamp2p.com/api/v1',
    STREAMP2P_KEY: '46d3af3546d3931092a5b078',  // Hardcoded default key
    DEFAULT_SUB: 'soft',
    DATA_SOURCE: 'both', // 'mal', 'anilist', 'both'
    AUTO_MONITOR: true,
    AUTO_SYNC: true,
    REPO_URL: '',
};

// ===== State =====
let state = {
    currentAnime: null,
    currentSubType: CONFIG.DEFAULT_SUB,
    searchResults: [],
    uploadedAnime: {},  // local DB of uploaded anime with StreamP2P IDs
    ongoingList: [],    // auto-monitored ongoing anime
    streamP2PFolders: [], // cached folder list
    streamP2PLibrary: {},  // anime_name -> { softsub: [videos], hardsub: [videos], dub: [videos] }
};

// ===== Load Settings =====
function loadSettings() {
    try {
        const saved = localStorage.getItem('aniverse_settings');
        if (saved) {
            const s = JSON.parse(saved);
            if (s.streamp2pKey) CONFIG.STREAMP2P_KEY = s.streamp2pKey;
            CONFIG.DEFAULT_SUB = s.defaultSub || 'soft';
            CONFIG.DATA_SOURCE = s.dataSource || 'both';
            CONFIG.AUTO_MONITOR = s.autoMonitor !== false;
            CONFIG.AUTO_SYNC = s.autoSync !== false;
            CONFIG.REPO_URL = s.repoUrl || '';
            state.currentSubType = CONFIG.DEFAULT_SUB;
        }
    } catch (e) { console.warn('Failed to load settings:', e); }
}

function saveSettings() {
    const settings = {
        streamp2pKey: document.getElementById('settStreamP2PKey').value,
        defaultSub: document.getElementById('settDefaultSub').value,
        dataSource: document.getElementById('settDataSource').value,
        autoMonitor: document.getElementById('settAutoMonitor').checked,
        autoSync: document.getElementById('settAutoSync').checked,
        repoUrl: document.getElementById('settRepoUrl').value,
    };
    localStorage.setItem('aniverse_settings', JSON.stringify(settings));
    Object.assign(CONFIG, {
        STREAMP2P_KEY: settings.streamp2pKey || CONFIG.STREAMP2P_KEY,
        DEFAULT_SUB: settings.defaultSub,
        DATA_SOURCE: settings.dataSource,
        AUTO_MONITOR: settings.autoMonitor,
        AUTO_SYNC: settings.autoSync,
        REPO_URL: settings.repoUrl,
    });
    state.currentSubType = settings.defaultSub;
    showToast('Settings saved! Syncing library...', 'success');
    syncStreamP2PLibrary();
}

function loadLocalDB() {
    try {
        const db = localStorage.getItem('aniverse_db');
        if (db) state.uploadedAnime = JSON.parse(db);
        const ongoing = localStorage.getItem('aniverse_ongoing');
        if (ongoing) state.ongoingList = JSON.parse(ongoing);
    } catch (e) { console.warn('Failed to load local DB:', e); }
}

function saveLocalDB() {
    localStorage.setItem('aniverse_db', JSON.stringify(state.uploadedAnime));
    localStorage.setItem('aniverse_ongoing', JSON.stringify(state.ongoingList));
}

// ===== API: MyAnimeList (via Jikan) =====
async function searchMAL(query, limit = 10) {
    try {
        const res = await fetch(`${CONFIG.JIKAN_BASE}/anime?q=${encodeURIComponent(query)}&limit=${limit}&sfw=true`);
        if (!res.ok) throw new Error(`MAL API: ${res.status}`);
        const data = await res.json();
        return (data.data || []).map(a => ({
            id: a.mal_id,
            source: 'mal',
            title: a.title,
            titleEnglish: a.title_english,
            titleJapanese: a.title_japanese,
            cover: a.images?.jpg?.large_image_url || a.images?.jpg?.image_url,
            banner: a.images?.jpg?.large_image_url,
            synopsis: a.synopsis,
            score: a.score,
            episodes: a.episodes,
            status: a.status,
            year: a.year || a.aired?.prop?.from?.year,
            genres: (a.genres || []).map(g => g.name),
            type: a.type,
            airing: a.airing,
            season: a.season,
        }));
    } catch (e) {
        console.error('MAL search error:', e);
        return [];
    }
}

async function getMALFull(id) {
    try {
        const res = await fetch(`${CONFIG.JIKAN_BASE}/anime/${id}/full`);
        if (!res.ok) throw new Error(`MAL API: ${res.status}`);
        const data = await res.json();
        const a = data.data;
        return {
            id: a.mal_id,
            source: 'mal',
            title: a.title,
            titleEnglish: a.title_english,
            titleJapanese: a.title_japanese,
            cover: a.images?.jpg?.large_image_url || a.images?.jpg?.image_url,
            banner: a.images?.jpg?.large_image_url,
            synopsis: a.synopsis,
            score: a.score,
            episodes: a.episodes,
            status: a.status,
            year: a.year || a.aired?.prop?.from?.year,
            genres: [...(a.genres || []), ...(a.themes || [])].map(g => g.name),
            type: a.type,
            airing: a.airing,
            season: a.season,
            relations: a.relations || [],
            broadcast: a.broadcast,
        };
    } catch (e) {
        console.error('MAL full error:', e);
        return null;
    }
}

// ===== API: AniList =====
async function searchAniList(query, limit = 10) {
    const gql = `
        query ($search: String, $perPage: Int) {
            Page(page: 1, perPage: $perPage) {
                media(search: $search, type: ANIME, isAdult: false) {
                    id idMal title { romaji english native } coverImage { large extraLarge }
                    bannerImage synopsis meanScore episodes status seasonYear format
                    genres nextAiringEpisode { episode airingAt } airingSchedule { nodes { episode airingAt } }
                    relations { edges { relationType node { id title { romaji english } format episodes type } } }
                }
            }
        }
    `;
    try {
        const res = await fetch(CONFIG.ANILIST_BASE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: gql, variables: { search: query, perPage: limit } }),
        });
        const data = await res.json();
        return (data.data?.Page?.media || []).map(a => ({
            id: a.id,
            malId: a.idMal,
            source: 'anilist',
            title: a.title?.english || a.title?.romaji,
            titleEnglish: a.title?.english,
            titleJapanese: a.title?.native,
            cover: a.coverImage?.extraLarge || a.coverImage?.large,
            banner: a.bannerImage || a.coverImage?.extraLarge,
            synopsis: a.synopsis,
            score: a.meanScore ? a.meanScore / 10 : null,
            episodes: a.episodes,
            status: a.status === 'RELEASING' ? 'Currently Airing' : a.status === 'NOT_YET_RELEASED' ? 'Not yet aired' : a.status,
            year: a.seasonYear,
            genres: a.genres || [],
            type: a.format,
            airing: a.status === 'RELEASING',
            nextEpisode: a.nextAiringEpisode,
            anilistRelations: a.relations,
        }));
    } catch (e) {
        console.error('AniList search error:', e);
        return [];
    }
}

// ===== Combined Search =====
async function searchAnime(query) {
    showLoading(true);
    const results = [];

    if (CONFIG.DATA_SOURCE === 'mal' || CONFIG.DATA_SOURCE === 'both') {
        const malResults = await searchMAL(query, 15);
        results.push(...malResults);
    }

    if (CONFIG.DATA_SOURCE === 'anilist' || CONFIG.DATA_SOURCE === 'both') {
        const anilistResults = await searchAniList(query, 15);
        // Deduplicate by MAL ID
        const existingMalIds = new Set(results.filter(r => r.source === 'mal').map(r => r.id));
        const uniqueAnilist = anilistResults.filter(r => !r.malId || !existingMalIds.has(r.malId));
        results.push(...uniqueAnilist);
    }

    showLoading(false);
    return results;
}

// ===== StreamP2P Integration =====
async function getStreamP2PFolders() {
    if (!CONFIG.STREAMP2P_KEY) return [];
    try {
        const res = await fetch(`${CONFIG.STREAMP2P_API}/video/folder`, {
            headers: { 'api-token': CONFIG.STREAMP2P_KEY },
        });
        if (!res.ok) throw new Error(`StreamP2P: ${res.status}`);
        const data = await res.json();
        state.streamP2PFolders = data;
        return data;
    } catch (e) {
        console.error('StreamP2P folders error:', e);
        return [];
    }
}

async function getStreamP2PFolderVideos(folderId) {
    if (!CONFIG.STREAMP2P_KEY) return [];
    try {
        const res = await fetch(`${CONFIG.STREAMP2P_API}/video/folder/${folderId}?perPage=100`, {
            headers: { 'api-token': CONFIG.STREAMP2P_KEY },
        });
        if (!res.ok) throw new Error(`StreamP2P: ${res.status}`);
        const data = await res.json();
        return data.data || [];
    } catch (e) {
        console.error('StreamP2P folder videos error:', e);
        return [];
    }
}

/**
 * Sync the entire StreamP2P library into state
 * Folder structure: Anime -> [Anime Name] -> SoftSub|HardSub|Dub
 */
async function syncStreamP2PLibrary() {
    if (!CONFIG.STREAMP2P_KEY) {
        console.log('No StreamP2P key, skipping sync');
        return;
    }

    try {
        showToast('Syncing StreamP2P library...', 'info');
        const folders = await getStreamP2PFolders();

        // Find "Anime" parent folder
        const animeParent = folders.find(f => f.name.toLowerCase() === 'anime');

        if (!animeParent) {
            console.log('No "Anime" parent folder found in StreamP2P');
            showToast('No Anime folder found in StreamP2P', 'error');
            return;
        }

        // Get all anime subfolders
        const animeSubfolders = folders.filter(f => f.parentId === animeParent.id);

        state.streamP2PLibrary = {};
        let totalEpisodes = 0;

        for (const animeFolder of animeSubfolders) {
            const animeName = animeFolder.name;
            state.streamP2PLibrary[animeName] = {
                softsub: [],
                hardsub: [],
                dub: [],
                folderId: animeFolder.id,
            };

            // Find SoftSub, HardSub, Dub subfolders
            const subFolders = folders.filter(f => f.parentId === animeFolder.id);

            for (const subFolder of subFolders) {
                const subName = subFolder.name.toLowerCase();
                let subType = null;

                if (subName === 'softsub' || subName === 'soft sub' || subName === 'soft-sub') {
                    subType = 'softsub';
                } else if (subName === 'hardsub' || subName === 'hard sub' || subName === 'hard-sub') {
                    subType = 'hardsub';
                } else if (subName === 'dub' || subName === 'english dub' || subName === 'eng dub') {
                    subType = 'dub';
                }

                if (subType) {
                    const videos = await getStreamP2PFolderVideos(subFolder.id);
                    state.streamP2PLibrary[animeName][subType] = videos;
                    totalEpisodes += videos.length;
                }
            }

            // Also check: if there are no sub-folders, videos might be directly in the anime folder
            const hasSubFolders = subFolders.length > 0;
            if (!hasSubFolders) {
                const videos = await getStreamP2PFolderVideos(animeFolder.id);
                if (videos.length > 0) {
                    // Put all videos in softsub by default
                    state.streamP2PLibrary[animeName].softsub = videos;
                    totalEpisodes += videos.length;
                }
            }

            // Update local DB
            const library = state.streamP2PLibrary[animeName];
            const totalVideos = library.softsub.length + library.hardsub.length + library.dub.length;
            if (totalVideos > 0) {
                state.uploadedAnime[animeName] = {
                    softsub: library.softsub.length,
                    hardsub: library.hardsub.length,
                    dub: library.dub.length,
                    folderId: animeFolder.id,
                    lastSynced: new Date().toISOString(),
                };
            }
        }

        saveLocalDB();

        // Update hero stats
        const libraryCount = Object.keys(state.streamP2PLibrary).length;
        document.getElementById('libraryCount').textContent = libraryCount;
        document.getElementById('episodeCount').textContent = totalEpisodes;
        document.getElementById('monitorCount').textContent = state.ongoingList.length;

        // Render library section
        renderLibrarySection();

        showToast(`Synced ${libraryCount} anime, ${totalEpisodes} episodes`, 'success');

    } catch (e) {
        console.error('StreamP2P sync error:', e);
        showToast('Failed to sync StreamP2P library', 'error');
    }
}

/**
 * Render the My Library section from StreamP2P data
 */
function renderLibrarySection() {
    const libraryNames = Object.keys(state.streamP2PLibrary);
    const libraryGrid = document.getElementById('libraryGrid');
    const librarySection = document.getElementById('librarySection');

    if (libraryNames.length === 0) {
        librarySection.style.display = 'none';
        return;
    }

    librarySection.style.display = 'block';

    // Create card data from library entries
    const libraryAnime = libraryNames.map(name => {
        const lib = state.streamP2PLibrary[name];
        const totalVids = lib.softsub.length + lib.hardsub.length + lib.dub.length;
        return {
            title: name,
            cover: null, // Will be fetched on search
            libraryEntry: true,
            softsub: lib.softsub.length,
            hardsub: lib.hardsub.length,
            dub: lib.dub.length,
            totalEpisodes: totalVids,
            folderId: lib.folderId,
        };
    });

    libraryGrid.innerHTML = '';
    libraryAnime.forEach(anime => {
        const card = document.createElement('div');
        card.className = 'anime-card library-card';

        const subTypes = [];
        if (anime.softsub > 0) subTypes.push(`<span class="sub-indicator soft">Soft ${anime.softsub}</span>`);
        if (anime.hardsub > 0) subTypes.push(`<span class="sub-indicator hard">Hard ${anime.hardsub}</span>`);
        if (anime.dub > 0) subTypes.push(`<span class="sub-indicator dub">Dub ${anime.dub}</span>`);

        card.innerHTML = `
            <div class="anime-card-body library-card-body">
                <div class="anime-card-title">${anime.title}</div>
                <div class="anime-card-meta">
                    <span>${anime.totalEpisodes} videos</span>
                </div>
                <div class="sub-indicators">${subTypes.join('')}</div>
                <i class="fas fa-play-circle library-play-icon"></i>
            </div>
        `;

        card.addEventListener('click', async () => {
            // Search for this anime to get full details
            const results = await searchAnime(anime.title);
            const match = results.find(r => r.title?.toLowerCase() === anime.title.toLowerCase()) || results[0];
            if (match) {
                openAnimeModal(match);
            } else {
                // Create a minimal anime object
                openAnimeModal({
                    title: anime.title,
                    cover: null,
                    banner: null,
                    synopsis: 'Available in your StreamP2P library.',
                    episodes: anime.totalEpisodes,
                    source: 'library',
                    id: anime.folderId,
                });
            }
        });

        libraryGrid.appendChild(card);
    });
}

/**
 * Check if anime is uploaded to StreamP2P and return folder info
 * FIXED: Now properly navigates Anime -> [AnimeName] -> SoftSub|HardSub|Dub
 */
async function checkAnimeUploaded(animeTitle) {
    const normalizedName = animeTitle.toLowerCase().trim();

    // First check cached library
    for (const [name, lib] of Object.entries(state.streamP2PLibrary)) {
        if (name.toLowerCase() === normalizedName ||
            name.toLowerCase().includes(normalizedName) ||
            normalizedName.includes(name.toLowerCase())) {
            return {
                found: true,
                folderId: lib.folderId,
                libraryData: lib,
                folderName: name,
            };
        }
    }

    // Fallback: fetch from API
    const folders = await getStreamP2PFolders();

    // Look for "Anime" parent folder first
    let animeFolder = folders.find(f => f.name.toLowerCase() === 'anime');

    if (animeFolder) {
        const subfolders = folders.filter(f => f.parentId === animeFolder.id);
        const match = subfolders.find(f =>
            f.name.toLowerCase() === normalizedName ||
            f.name.toLowerCase().includes(normalizedName) ||
            normalizedName.includes(f.name.toLowerCase())
        );
        if (match) {
            return { found: true, folder: match, folderId: match.id, folderName: match.name };
        }
    }

    // Also check root-level folders
    const match = folders.find(f =>
        f.name.toLowerCase() === normalizedName ||
        f.name.toLowerCase().includes(normalizedName) ||
        normalizedName.includes(f.name.toLowerCase())
    );
    if (match) {
        return { found: true, folder: match, folderId: match.id, folderName: match.name };
    }

    return { found: false };
}

function buildStreamP2PEmbedUrl(videoId) {
    return `${CONFIG.STREAMP2P_BASE}/e/${videoId}`;
}

// ===== UI: Search Dropdown =====
const searchInput = document.getElementById('searchInput');
const searchDropdown = document.getElementById('searchDropdown');
let searchTimeout = null;

searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    const query = e.target.value.trim();
    if (query.length < 2) {
        searchDropdown.classList.remove('active');
        return;
    }
    searchTimeout = setTimeout(async () => {
        const results = await searchAnime(query);
        renderSearchDropdown(results);
    }, 400);
});

searchInput.addEventListener('focus', () => {
    if (searchDropdown.children.length > 0) {
        searchDropdown.classList.add('active');
    }
});

document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-wrapper')) {
        searchDropdown.classList.remove('active');
    }
});

function renderSearchDropdown(results) {
    searchDropdown.innerHTML = '';
    if (results.length === 0) {
        searchDropdown.innerHTML = '<div class="search-result-item"><span style="color:var(--text-muted)">No results found</span></div>';
        searchDropdown.classList.add('active');
        return;
    }

    results.forEach(anime => {
        const item = document.createElement('div');
        item.className = 'search-result-item';

        // Check if in library
        const isInLibrary = isAnimeInLibrary(anime.title);
        const libraryBadge = isInLibrary ? '<span class="library-badge"><i class="fas fa-check-circle"></i> In Library</span>' : '';

        item.innerHTML = `
            <img src="${anime.cover}" alt="${anime.title}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 140%22><rect fill=%22%231a1a2e%22 width=%22100%22 height=%22140%22/><text x=%2250%22 y=%2270%22 fill=%22%23555%22 text-anchor=%22middle%22 font-size=%2212%22>No Image</text></svg>'">
            <div class="search-result-info">
                <h4>${anime.title} ${libraryBadge}</h4>
                <p>${anime.type || ''} ${anime.episodes ? '· ' + anime.episodes + ' eps' : ''} ${anime.year ? '· ' + anime.year : ''}</p>
            </div>
            ${anime.score ? `<span class="search-score">★ ${anime.score}</span>` : ''}
        `;
        item.addEventListener('click', () => openAnimeModal(anime));
        searchDropdown.appendChild(item);
    });
    searchDropdown.classList.add('active');
}

function isAnimeInLibrary(title) {
    const normalized = title.toLowerCase().trim();
    for (const name of Object.keys(state.streamP2PLibrary)) {
        if (name.toLowerCase() === normalized || name.toLowerCase().includes(normalized) || normalized.includes(name.toLowerCase())) {
            return true;
        }
    }
    return false;
}

// ===== UI: Hero Search =====
const heroSearchInput = document.getElementById('heroSearchInput');
const heroSearchBtn = document.getElementById('heroSearchBtn');

heroSearchBtn.addEventListener('click', () => {
    const query = heroSearchInput.value.trim();
    if (query) performHeroSearch(query);
});

heroSearchInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        const query = heroSearchInput.value.trim();
        if (query) performHeroSearch(query);
    }
});

async function performHeroSearch(query) {
    showLoading(true);
    const results = await searchAnime(query);
    showLoading(false);

    if (results.length > 0) {
        document.getElementById('heroSection').style.display = 'none';
        renderAnimeGrid(results, 'trendingGrid');
        document.querySelector('#trendingSection .section-header h2').innerHTML =
            `<i class="fas fa-search"></i> Search Results for "${query}"`;
    } else {
        showToast('No anime found. Try a different name.', 'error');
    }
}

// ===== UI: Anime Grid =====
function renderAnimeGrid(animeList, containerId) {
    const grid = document.getElementById(containerId);
    grid.innerHTML = '';

    animeList.forEach(anime => {
        const card = document.createElement('div');
        card.className = 'anime-card';

        const isInLibrary = isAnimeInLibrary(anime.title);
        const statusClass = anime.airing ? 'airing' : (anime.status?.includes('Not') ? 'upcoming' : 'completed');
        const statusText = anime.airing ? 'Airing' : (anime.status?.includes('Not') ? 'Upcoming' : '');

        card.innerHTML = `
            ${statusText ? `<span class="status-badge ${statusClass}">${statusText}</span>` : ''}
            ${isInLibrary ? `<div class="library-available-badge"><i class="fas fa-play-circle"></i><span>Available</span></div>` : `<div class="coming-soon-badge"><i class="fas fa-hourglass-half"></i><span>Coming Soon</span></div>`}
            <img class="anime-card-img" src="${anime.cover}" alt="${anime.title}" loading="lazy"
                 onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 140%22><rect fill=%22%231a1a2e%22 width=%22100%22 height=%22140%22/><text x=%2250%22 y=%2270%22 fill=%22%23555%22 text-anchor=%22middle%22 font-size=%2212%22>No Image</text></svg>'">
            <div class="anime-card-body">
                <div class="anime-card-title" title="${anime.title}">${anime.title}</div>
                <div class="anime-card-meta">
                    <span>${anime.type || 'TV'}</span>
                    ${anime.episodes ? `<span>${anime.episodes} eps</span>` : ''}
                    ${anime.score ? `<span class="score">★ ${anime.score}</span>` : ''}
                </div>
            </div>
        `;
        card.addEventListener('click', () => openAnimeModal(anime));
        grid.appendChild(card);
    });
}

// ===== UI: Anime Modal =====
async function openAnimeModal(anime) {
    searchDropdown.classList.remove('active');
    const modal = document.getElementById('animeModal');
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';

    // Get full details
    let fullAnime = anime;
    if (anime.source === 'mal' && !anime.relations) {
        const full = await getMALFull(anime.id);
        if (full) fullAnime = full;
    }

    state.currentAnime = fullAnime;
    state.currentSubType = CONFIG.DEFAULT_SUB;

    // Populate modal
    document.getElementById('modalBannerImg').src = fullAnime.banner || fullAnime.cover || '';
    document.getElementById('modalCover').src = fullAnime.cover || '';
    document.getElementById('modalTitle').textContent = fullAnime.title;
    document.getElementById('modalYear').textContent = fullAnime.year || 'TBA';
    document.getElementById('modalStatus').textContent = fullAnime.status || 'Unknown';
    document.getElementById('modalEpisodes').textContent = fullAnime.episodes ? `${fullAnime.episodes} Episodes` : 'Episodes TBA';
    document.getElementById('modalScore').textContent = fullAnime.score ? `★ ${fullAnime.score}` : 'No Score';
    document.getElementById('modalSynopsis').textContent = fullAnime.synopsis || 'No synopsis available.';

    // Genres
    const genresContainer = document.getElementById('modalGenres');
    genresContainer.innerHTML = (fullAnime.genres || []).map(g => `<span class="genre-tag">${g}</span>`).join('');

    // Check StreamP2P for uploaded content
    const uploadCheck = await checkAnimeUploaded(fullAnime.title);

    if (uploadCheck.found) {
        // Get videos from the correct sub-type folders
        await loadEpisodesForSubType(uploadCheck);
        document.getElementById('comingSoon').style.display = 'none';
        document.getElementById('playerContainer').style.display = 'none';

        // If ongoing, add to auto-monitor
        if (fullAnime.airing && CONFIG.AUTO_MONITOR) {
            addToOngoing(fullAnime);
        }
    } else {
        // Show coming soon
        document.getElementById('episodeGrid').innerHTML = '';
        document.getElementById('playerContainer').style.display = 'none';
        document.getElementById('comingSoon').style.display = 'block';

        const nextEp = fullAnime.nextEpisode;
        if (nextEp) {
            const nextDate = new Date(nextEp.airingAt * 1000);
            document.getElementById('comingSoonSub').textContent =
                `Next: Episode ${nextEp.episode} on ${nextDate.toLocaleDateString()} — Auto-monitoring enabled`;
        } else if (fullAnime.airing) {
            document.getElementById('comingSoonSub').textContent = 'This anime is airing. Trigger the pipeline to download episodes.';
        } else {
            document.getElementById('comingSoonSub').textContent = 'Trigger the GitHub Actions pipeline to download and upload this anime.';
        }

        // Generate placeholder episode buttons
        renderPlaceholderEpisodes(fullAnime);
    }

    // Reset sub toggle
    document.querySelectorAll('.sub-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.sub === state.currentSubType) btn.classList.add('active');
    });
}

/**
 * Load episodes for the current sub type
 * FIXED: Now properly navigates the Anime/[Name]/SoftSub|HardSub|Dub folder structure
 */
async function loadEpisodesForSubType(uploadCheck) {
    const subType = state.currentSubType;
    let videos = [];

    // Method 1: Use cached library data
    if (uploadCheck.libraryData) {
        const lib = uploadCheck.libraryData;
        if (subType === 'soft' && lib.softsub.length > 0) {
            videos = lib.softsub;
        } else if (subType === 'hard' && lib.hardsub.length > 0) {
            videos = lib.hardsub;
        } else if (subType === 'dub' && lib.dub.length > 0) {
            videos = lib.dub;
        }

        // Fallback: if selected sub type has no videos, try others
        if (videos.length === 0) {
            if (lib.softsub.length > 0) videos = lib.softsub;
            else if (lib.hardsub.length > 0) videos = lib.hardsub;
            else if (lib.dub.length > 0) videos = lib.dub;
        }
    }

    // Method 2: Fetch from API
    if (videos.length === 0 && uploadCheck.folderId) {
        const folders = state.streamP2PFolders.length > 0 ? state.streamP2PFolders : await getStreamP2PFolders();

        // Find sub-folders of the anime folder
        const subFolders = folders.filter(f => f.parentId === uploadCheck.folderId);

        let targetSubFolder = null;
        const subTypeMap = {
            'soft': ['softsub', 'soft sub', 'soft-sub'],
            'hard': ['hardsub', 'hard sub', 'hard-sub'],
            'dub': ['dub', 'english dub', 'eng dub'],
        };

        // Try to find the matching sub-type folder
        const searchNames = subTypeMap[subType] || [];
        for (const name of searchNames) {
            targetSubFolder = subFolders.find(f => f.name.toLowerCase() === name);
            if (targetSubFolder) break;
        }

        // If no sub-type folder found, try all sub-folders
        if (!targetSubFolder && subFolders.length > 0) {
            // First try SoftSub, then HardSub, then Dub
            const fallbackOrder = ['softsub', 'hardsub', 'dub'];
            for (const fbName of fallbackOrder) {
                targetSubFolder = subFolders.find(f => f.name.toLowerCase().includes(fbName));
                if (targetSubFolder) break;
            }
        }

        if (targetSubFolder) {
            videos = await getStreamP2PFolderVideos(targetSubFolder.id);
        } else if (subFolders.length === 0) {
            // No sub-folders - videos might be directly in the anime folder
            videos = await getStreamP2PFolderVideos(uploadCheck.folderId);
        }
    }

    // Update episode count display
    const countEl = document.getElementById('subEpisodeCount');
    if (uploadCheck.libraryData) {
        const lib = uploadCheck.libraryData;
        countEl.innerHTML = `<span class="ep-count">Soft: ${lib.softsub.length} | Hard: ${lib.hardsub.length} | Dub: ${lib.dub.length}</span>`;
    }

    if (videos.length > 0) {
        renderEpisodes(videos, state.currentAnime);
        document.getElementById('comingSoon').style.display = 'none';
    } else {
        document.getElementById('episodeGrid').innerHTML = '';
        document.getElementById('playerContainer').style.display = 'none';
        document.getElementById('comingSoon').style.display = 'block';
        document.getElementById('comingSoonSub').textContent =
            `${subType === 'dub' ? 'English Dub' : subType === 'hard' ? 'Hard Sub' : 'Soft Sub'} version not yet available.`;
    }
}

function renderEpisodes(videos, anime) {
    const episodeGrid = document.getElementById('episodeGrid');
    const moviesGrid = document.getElementById('moviesGrid');
    const specialsGrid = document.getElementById('specialsGrid');
    episodeGrid.innerHTML = '';
    moviesGrid.innerHTML = '';
    specialsGrid.innerHTML = '';

    let hasMovies = false;
    let hasSpecials = false;

    // Sort videos by name
    const sorted = [...videos].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));

    sorted.forEach((video, idx) => {
        const name = video.name.toLowerCase();
        const isMovie = name.includes('movie') || name.includes('film');
        const isSpecial = name.includes('special') || name.includes('ova') || name.includes('ona') || name.includes('extra');

        // Try to extract episode number from filename
        let displayText = '';
        if (isMovie) {
            displayText = video.name;
        } else if (isSpecial) {
            displayText = video.name;
        } else {
            // Try to find episode number
            const epMatch = video.name.match(/[Ee][Pp]?\.?\s*(\d+)/) ||
                           video.name.match(/(\d+)\s*[-_]/) ||
                           video.name.match(/[-_\s](\d+)[-_\s]/);
            if (epMatch) {
                displayText = `Ep ${parseInt(epMatch[1])}`;
            } else {
                displayText = `Ep ${idx + 1}`;
            }
        }

        const btn = document.createElement('button');
        btn.className = 'ep-btn';
        btn.textContent = displayText;
        btn.dataset.videoId = video.id;
        btn.dataset.videoName = video.name;

        btn.addEventListener('click', () => playVideo(video.id, video.name));

        if (isMovie) {
            moviesGrid.appendChild(btn);
            hasMovies = true;
        } else if (isSpecial) {
            specialsGrid.appendChild(btn);
            hasSpecials = true;
        } else {
            episodeGrid.appendChild(btn);
        }
    });

    document.getElementById('moviesSection').style.display = hasMovies ? 'block' : 'none';
    document.getElementById('specialsSection').style.display = hasSpecials ? 'block' : 'none';
}

function renderPlaceholderEpisodes(anime) {
    const episodeGrid = document.getElementById('episodeGrid');
    episodeGrid.innerHTML = '';

    const totalEps = anime.episodes || 12;
    for (let i = 1; i <= totalEps; i++) {
        const btn = document.createElement('button');
        btn.className = 'ep-btn locked';
        btn.textContent = `Ep ${i}`;
        btn.title = 'Not yet available';
        episodeGrid.appendChild(btn);
    }

    document.getElementById('moviesSection').style.display = 'none';
    document.getElementById('specialsSection').style.display = 'none';
}

function playVideo(videoId, videoName) {
    const playerContainer = document.getElementById('playerContainer');
    const player = document.getElementById('videoPlayer');

    player.src = buildStreamP2PEmbedUrl(videoId);
    playerContainer.style.display = 'block';

    document.getElementById('playerEpisode').textContent = videoName;
    document.getElementById('playerSubType').textContent = `Sub: ${state.currentSubType.toUpperCase()}`;

    // Highlight active episode button
    document.querySelectorAll('.ep-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector(`.ep-btn[data-video-id="${videoId}"]`)?.classList.add('active');

    // Scroll to player
    playerContainer.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ===== Sub Toggle =====
document.querySelectorAll('.sub-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        const subType = btn.dataset.sub;

        document.querySelectorAll('.sub-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.currentSubType = subType;

        // Re-check StreamP2P for this sub type's folder
        if (state.currentAnime) {
            const uploadCheck = await checkAnimeUploaded(state.currentAnime.title);
            if (uploadCheck.found) {
                await loadEpisodesForSubType(uploadCheck);
            } else {
                document.getElementById('episodeGrid').innerHTML = '';
                document.getElementById('playerContainer').style.display = 'none';
                document.getElementById('comingSoon').style.display = 'block';
                document.getElementById('comingSoonSub').textContent =
                    `${subType === 'dub' ? 'English Dub' : subType === 'hard' ? 'Hard Sub' : 'Soft Sub'} version not yet available.`;
            }
        }
    });
});

// ===== Auto-Monitor Ongoing =====
function addToOngoing(anime) {
    const key = `${anime.source}_${anime.id}`;
    const exists = state.ongoingList.find(o => o.key === key);
    if (!exists) {
        state.ongoingList.push({
            key,
            title: anime.title,
            source: anime.source,
            id: anime.id,
            airing: true,
            nextEpisode: anime.nextEpisode,
            addedAt: new Date().toISOString(),
        });
        saveLocalDB();
        showToast(`Auto-monitoring: ${anime.title}`, 'success');
        renderOngoingGrid();
    }
}

function renderOngoingGrid() {
    const grid = document.getElementById('ongoingGrid');
    grid.innerHTML = '';

    if (state.ongoingList.length === 0) {
        grid.innerHTML = '<p style="color:var(--text-muted); grid-column:1/-1;">No ongoing anime monitored yet.</p>';
        return;
    }

    state.ongoingList.forEach(anime => {
        const card = document.createElement('div');
        card.className = 'anime-card ongoing-card';
        card.innerHTML = `
            <span class="status-badge airing">LIVE</span>
            <div class="anime-card-body" style="padding-top:2rem;">
                <div class="anime-card-title">${anime.title}</div>
                <div class="anime-card-meta">
                    <span>Monitoring</span>
                    ${anime.nextEpisode ? `<span>Ep ${anime.nextEpisode.episode} next</span>` : ''}
                </div>
            </div>
        `;
        card.addEventListener('click', async () => {
            const results = await searchAnime(anime.title);
            const match = results.find(r => r.id === anime.id);
            if (match) openAnimeModal(match);
        });
        grid.appendChild(card);
    });
}

// ===== Sync Button =====
document.getElementById('syncBtn').addEventListener('click', () => {
    syncStreamP2PLibrary();
});

// ===== Nav Brand Click (go home) =====
document.getElementById('navBrand').addEventListener('click', () => {
    document.getElementById('heroSection').style.display = '';
    document.getElementById('trendingSection').style.display = '';
    document.getElementById('recentSection').style.display = '';
    document.getElementById('ongoingSection').style.display = '';
    document.getElementById('librarySection').style.display = Object.keys(state.streamP2PLibrary).length > 0 ? 'block' : 'none';
    searchInput.value = '';
    searchDropdown.classList.remove('active');
    // Reload trending
    loadTrending();
});

// ===== Modals =====
document.getElementById('modalClose').addEventListener('click', () => {
    document.getElementById('animeModal').classList.remove('active');
    document.body.style.overflow = '';
    document.getElementById('videoPlayer').src = '';
});

document.getElementById('settingsBtn').addEventListener('click', () => {
    document.getElementById('settingsModal').classList.add('active');
    document.getElementById('settStreamP2PKey').value = CONFIG.STREAMP2P_KEY || '';
    document.getElementById('settDefaultSub').value = CONFIG.DEFAULT_SUB;
    document.getElementById('settDataSource').value = CONFIG.DATA_SOURCE;
    document.getElementById('settAutoMonitor').checked = CONFIG.AUTO_MONITOR;
    document.getElementById('settAutoSync').checked = CONFIG.AUTO_SYNC;
    document.getElementById('settRepoUrl').value = CONFIG.REPO_URL || '';
});

document.getElementById('settingsClose').addEventListener('click', () => {
    document.getElementById('settingsModal').classList.remove('active');
});

document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);

// Close modals on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.classList.remove('active');
            document.body.style.overflow = '';
            if (overlay.id === 'animeModal') {
                document.getElementById('videoPlayer').src = '';
            }
        }
    });
});

// ===== Nav Filter Links =====
document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', (e) => {
        e.preventDefault();
        document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
        link.classList.add('active');

        const filter = link.dataset.filter;
        if (filter === 'library') {
            document.getElementById('heroSection').style.display = 'none';
            document.getElementById('trendingSection').style.display = 'none';
            document.getElementById('recentSection').style.display = 'none';
            document.getElementById('ongoingSection').style.display = 'none';
            document.getElementById('librarySection').style.display = Object.keys(state.streamP2PLibrary).length > 0 ? 'block' : 'none';
            if (Object.keys(state.streamP2PLibrary).length === 0) {
                syncStreamP2PLibrary();
            }
        } else if (filter === 'ongoing-monitor') {
            document.getElementById('heroSection').style.display = 'none';
            document.getElementById('trendingSection').style.display = 'none';
            document.getElementById('recentSection').style.display = 'none';
            document.getElementById('ongoingSection').style.display = 'block';
            document.getElementById('librarySection').style.display = 'none';
            renderOngoingGrid();
        } else {
            document.getElementById('heroSection').style.display = '';
            document.getElementById('trendingSection').style.display = '';
            document.getElementById('recentSection').style.display = '';
            document.getElementById('ongoingSection').style.display = '';
            document.getElementById('librarySection').style.display = Object.keys(state.streamP2PLibrary).length > 0 ? 'block' : 'none';
        }
    });
});

// ===== Toast =====
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icon = type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-exclamation-circle' : 'fa-info-circle';
    toast.innerHTML = `<i class="fas ${icon}"></i> ${message}`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// ===== Loading =====
function showLoading(show) {
    document.getElementById('loadingOverlay').classList.toggle('active', show);
}

// ===== Load Trending =====
async function loadTrending() {
    try {
        showLoading(true);
        const res = await fetch(`${CONFIG.JIKAN_BASE}/top/anime?limit=20&sfw=true`);
        if (res.ok) {
            const data = await res.json();
            const trending = (data.data || []).map(a => ({
                id: a.mal_id,
                source: 'mal',
                title: a.title,
                titleEnglish: a.title_english,
                cover: a.images?.jpg?.large_image_url || a.images?.jpg?.image_url,
                banner: a.images?.jpg?.large_image_url,
                synopsis: a.synopsis,
                score: a.score,
                episodes: a.episodes,
                status: a.status,
                year: a.year,
                genres: (a.genres || []).map(g => g.name),
                type: a.type,
                airing: a.airing,
            }));
            renderAnimeGrid(trending, 'trendingGrid');
        }
    } catch (e) {
        console.error('Failed to load trending:', e);
    }
}

// ===== Initialize =====
async function init() {
    loadSettings();
    loadLocalDB();

    // Load trending anime
    await loadTrending();

    // Render ongoing list
    renderOngoingGrid();

    // Auto-sync StreamP2P library
    if (CONFIG.AUTO_SYNC && CONFIG.STREAMP2P_KEY) {
        await syncStreamP2PLibrary();
    }

    showLoading(false);
}

init();
