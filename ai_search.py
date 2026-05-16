#!/usr/bin/env python3
"""
AI Search Script - Groq (PRIMARY) / Cerebras (SECONDARY) / Gemini Flash (BACKUP)
Discovers all anime content: Episodes, Movies, Specials, OVA, ONA

IMPORTANT: Only the final JSON is printed to stdout (for pipeline capture).
All progress/info is printed to stderr.
"""

import sys
import os
import json
import time
import re
import requests
from urllib.parse import quote

# Import config
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_FALLBACK_MODEL, CEREBRAS_API_KEY, GEMINI_API_KEY, JIKAN_BASE_URL, ANILIST_BASE_URL

JIKAN_BASE = JIKAN_BASE_URL
ANILIST_BASE = ANILIST_BASE_URL

def log(msg):
    """Print to stderr so it doesn't corrupt stdout JSON output"""
    print(msg, file=sys.stderr)

# ===== Groq AI (PRIMARY) =====
def groq_chat(prompt, system_prompt="", max_retries=3):
    """Use Groq as primary AI for lightweight tasks"""
    if not GROQ_API_KEY:
        log("[Groq] No API key set, skipping")
        return None

    try:
        from groq import Groq
    except ImportError:
        log("[Groq] groq package not installed, trying direct HTTP...")
        return _groq_http(prompt, system_prompt, max_retries)

    client = Groq(api_key=GROQ_API_KEY)

    # Try models in order: primary model first, then fallback
    models_to_try = [GROQ_MODEL, GROQ_FALLBACK_MODEL]

    for model in models_to_try:
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=4096,
                )
                log(f"[Groq] Success with model: {model}")
                return response.choices[0].message.content
            except Exception as e:
                error_str = str(e)
                log(f"[Groq] model={model} attempt {attempt+1} failed: {error_str}")
                # If 403, try next model or fallback to HTTP
                if '403' in error_str or 'Forbidden' in error_str:
                    log(f"[Groq] 403 Forbidden - may be geo-blocked, trying next option...")
                    break  # Don't retry same model, try next one
                if '401' in error_str or 'Unauthorized' in error_str:
                    log(f"[Groq] 401 Unauthorized - key may be invalid")
                    break  # No point retrying with same key
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

    # All SDK attempts failed, try direct HTTP
    return _groq_http(prompt, system_prompt, max_retries)

def _groq_http(prompt, system_prompt="", max_retries=3):
    """Direct HTTP fallback for Groq when SDK fails (e.g., geo-block)"""
    if not GROQ_API_KEY:
        return None

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {GROQ_API_KEY}',
    }
    payload = {
        'model': GROQ_MODEL,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt}
        ],
        'temperature': 0.3,
        'max_tokens': 4096,
    }

    models_to_try = [GROQ_MODEL, GROQ_FALLBACK_MODEL]

    for model in models_to_try:
        payload['model'] = model
        for attempt in range(max_retries):
            try:
                res = requests.post(
                    'https://api.groq.com/openai/v1/chat/completions',
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                if res.status_code == 200:
                    data = res.json()
                    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    if content:
                        log(f"[Groq HTTP] Success with model: {model}")
                        return content
                elif res.status_code == 403:
                    log(f"[Groq HTTP] 403 Forbidden with model {model} - geo-blocked or key issue")
                    break  # Try next model
                else:
                    log(f"[Groq HTTP] model={model} attempt {attempt+1} failed: {res.status_code} {res.text[:200]}")
            except Exception as e:
                log(f"[Groq HTTP] model={model} attempt {attempt+1} error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    return None

# ===== Cerebras AI (SECONDARY) =====
def cerebras_chat(prompt, system_prompt="", max_retries=3):
    """Use Cerebras as secondary AI — very fast inference"""
    if not CEREBRAS_API_KEY:
        log("[Cerebras] No API key set, skipping")
        return None

    try:
        from cerebras.cloud.sdk import Cerebras
    except ImportError:
        log("[Cerebras] cerebras_cloud_sdk not installed, trying direct HTTP...")
        return _cerebras_http(prompt, system_prompt, max_retries)

    client = Cerebras(api_key=CEREBRAS_API_KEY)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model='gpt-oss-120b',
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            return response.choices[0].message.content
        except Exception as e:
            log(f"[Cerebras] attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    return None

def _cerebras_http(prompt, system_prompt="", max_retries=3):
    """Direct HTTP fallback for Cerebras when SDK is not installed"""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {CEREBRAS_API_KEY}',
    }
    payload = {
        'model': 'gpt-oss-120b',
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt}
        ],
        'temperature': 0.3,
        'max_tokens': 4096,
    }

    for attempt in range(max_retries):
        try:
            res = requests.post(
                'https://api.cerebras.ai/v1/chat/completions',
                headers=headers,
                json=payload,
                timeout=60,
            )
            res.raise_for_status()
            return res.json()['choices'][0]['message']['content']
        except Exception as e:
            log(f"[Cerebras HTTP] attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    return None

# ===== Gemini Flash (BACKUP) =====
def gemini_chat(prompt, system_prompt="", max_retries=3):
    """Use Gemini Flash as backup AI"""
    if not GEMINI_API_KEY:
        log("[Gemini] No API key set, skipping")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)

        for attempt in range(max_retries):
            try:
                config = types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=4096,
                )
                if system_prompt:
                    config.system_instruction = system_prompt

                response = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=prompt,
                    config=config,
                )
                return response.text
            except Exception as e:
                log(f"[Gemini] attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

    except ImportError:
        log("[Gemini] google-genai not installed, trying legacy google.generativeai...")
        try:
            import google.generativeai as genai_legacy
            genai_legacy.configure(api_key=GEMINI_API_KEY)
            model = genai_legacy.GenerativeModel('gemini-2.0-flash', system_instruction=system_prompt)

            for attempt in range(max_retries):
                try:
                    response = model.generate_content(prompt)
                    return response.text
                except Exception as e:
                    log(f"[Gemini] (legacy) attempt {attempt+1} failed: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
        except ImportError:
            log("[Gemini] Neither google-genai nor google.generativeai is installed!")

    return None

def ai_chat(prompt, system_prompt=""):
    """Try Groq first, then Cerebras, then Gemini"""
    result = groq_chat(prompt, system_prompt)
    if result:
        log("[AI] Used Groq (primary)")
        return result

    log("[AI] Groq failed, trying Cerebras...")
    result = cerebras_chat(prompt, system_prompt)
    if result:
        log("[AI] Used Cerebras (secondary)")
        return result

    log("[AI] Cerebras failed, falling back to Gemini Flash...")
    result = gemini_chat(prompt, system_prompt)
    if result:
        log("[AI] Used Gemini Flash (backup)")
        return result

    log("[AI] All AI providers failed!")
    return None

# ===== MAL/AniList Data Fetching =====
def fetch_mal_anime(query):
    """Fetch anime data from MyAnimeList via Jikan API"""
    try:
        res = requests.get(f'{JIKAN_BASE}/anime', params={'q': query, 'limit': 5, 'sfw': 'true'}, timeout=15)
        if res.status_code == 429:
            time.sleep(2)
            res = requests.get(f'{JIKAN_BASE}/anime', params={'q': query, 'limit': 5, 'sfw': 'true'}, timeout=15)
        res.raise_for_status()
        data = res.json().get('data', [])
        if data:
            mal_id = data[0]['mal_id']
            time.sleep(0.5)
            full_res = requests.get(f'{JIKAN_BASE}/anime/{mal_id}/full', timeout=15)
            if full_res.status_code == 429:
                time.sleep(2)
                full_res = requests.get(f'{JIKAN_BASE}/anime/{mal_id}/full', timeout=15)
            if full_res.status_code == 200:
                return full_res.json().get('data', data[0])
        return data[0] if data else None
    except Exception as e:
        log(f"MAL fetch error: {e}")
        return None

def fetch_anilist_anime(query):
    """Fetch anime data from AniList with full relations"""
    gql = """
    query ($search: String) {
        Page(page: 1, perPage: 5) {
            media(search: $search, type: ANIME, isAdult: false) {
                id idMal
                title { romaji english native }
                episodes format status seasonYear type
                nextAiringEpisode { episode airingAt }
                airingSchedule { nodes { episode airingAt } }
                relations {
                    edges { relationType
                    node { id title { romaji english } format episodes type }
                    }
                }
            }
        }
    }
    """
    try:
        res = requests.post(ANILIST_BASE, json={'query': gql, 'variables': {'search': query}}, timeout=15)
        res.raise_for_status()
        data = res.json().get('data', {}).get('Page', {}).get('media', [])
        return data[0] if data else None
    except Exception as e:
        log(f"AniList fetch error: {e}")
        return None

def fetch_anilist_full_relations(anilist_id):
    """Fetch ALL related anime from AniList including OVAs, specials, movies"""
    gql = """
    query ($id: Int) {
        Media(id: $id, type: ANIME) {
            id
            title { romaji english }
            format type episodes status
            relations {
                edges { relationType
                node {
                    id
                    title { romaji english }
                    format type episodes status
                    siteUrl
                }
                }
            }
        }
    }
    """
    try:
        res = requests.post(ANILIST_BASE, json={'query': gql, 'variables': {'id': anilist_id}}, timeout=15)
        res.raise_for_status()
        return res.json().get('data', {}).get('Media')
    except Exception as e:
        log(f"AniList full relations fetch error: {e}")
        return None

def search_anilist_for_related(anime_name, content_type):
    """Search AniList specifically for related content (OVA, Special, Movie, ONA)"""
    gql = """
    query ($search: String, $format: MediaFormat) {
        Page(page: 1, perPage: 10) {
            media(search: $search, type: ANIME, format: $format, isAdult: false) {
                id idMal
                title { romaji english }
                format type episodes status
            }
        }
    }
    """
    format_map = {
        'ova': 'OVA',
        'special': 'SPECIAL',
        'movie': 'MOVIE',
        'ona': 'ONA',
    }
    anilist_format = format_map.get(content_type.lower())
    if not anilist_format:
        return []

    try:
        res = requests.post(ANILIST_BASE, json={
            'query': gql,
            'variables': {'search': anime_name, 'format': anilist_format}
        }, timeout=15)
        res.raise_for_status()
        return res.json().get('data', {}).get('Page', {}).get('media', [])
    except Exception as e:
        log(f"AniList search for {content_type} error: {e}")
        return []

# ===== AI Content Discovery =====
def discover_all_content(anime_name, mal_data=None, anilist_data=None):
    """Use AI to list ALL anime content: Episodes, Movies, Specials, OVA, ONA"""

    context = f"Anime: {anime_name}\n"

    if mal_data:
        relations_str = 'None'
        try:
            relations_str = json.dumps([
                {'type': r.get('relation',''), 'name': r.get('entry',[{}])[0].get('name','') if r.get('entry') else ''}
                for r in (mal_data.get('relations') or [])[:10]
            ])
        except Exception:
            pass

        context += f"""
MAL Data:
- Title: {mal_data.get('title', '')}
- English: {mal_data.get('title_english', '')}
- Japanese: {mal_data.get('title_japanese', '')}
- Type: {mal_data.get('type', '')}
- Episodes: {mal_data.get('episodes', 'Unknown')}
- Status: {mal_data.get('status', '')}
- Score: {mal_data.get('score', 'N/A')}
- Season: {mal_data.get('season', '')} {mal_data.get('year', '')}
- Relations: {relations_str}
"""

    if anilist_data:
        next_ep = anilist_data.get('nextAiringEpisode') or {}
        relations_str = 'None'
        try:
            relations_str = json.dumps([
                {'type': e.get('relationType',''), 'name': e.get('node',{}).get('title',{}).get('english','') or e.get('node',{}).get('title',{}).get('romaji',''), 'format': e.get('node',{}).get('format','')}
                for e in ((anilist_data.get('relations') or {}).get('edges') or [])[:15]
            ])
        except Exception:
            pass

        context += f"""
AniList Data:
- Format: {anilist_data.get('format', '')}
- Episodes: {anilist_data.get('episodes', 'Unknown')}
- Status: {anilist_data.get('status', '')}
- Next Episode: {next_ep.get('episode', 'N/A')} at {next_ep.get('airingAt', 'N/A')}
- Relations: {relations_str}
"""

    mal_id_val = mal_data.get('mal_id', 'null') if mal_data else 'null'

    system_prompt = """You are an anime content discovery expert. Your job is to list ALL content for a given anime series, including:
1. Regular Episodes (TV episodes)
2. Movies
3. Specials
4. OVAs
5. ONAs
6. Recap episodes
7. Extra episodes

You MUST output valid JSON only, no other text. Do NOT wrap it in markdown code blocks."""

    prompt = f"""Based on the following anime information, list ALL content available for this anime.
Include every episode, movie, special, OVA, and ONA.

{context}

Return a JSON object with this exact structure (output ONLY the JSON, nothing else):
{{
    "anime_title": "Full anime title in English",
    "anime_title_japanese": "Japanese title if known",
    "anime_title_romaji": "Romaji title if known",
    "mal_id": {mal_id_val},
    "status": "Currently Airing / Finished Airing / Not yet aired",
    "total_episodes": number_or_null,
    "next_episode": number_or_null,
    "content": [
        {{
            "type": "episode",
            "number": 1,
            "title": "Episode 1",
            "search_query": "anime title Episode 1 1080p",
            "search_query_alt": "anime title EP01 1080p"
        }},
        {{
            "type": "movie",
            "number": 1,
            "title": "Movie Name",
            "search_query": "anime title Movie 1080p",
            "search_query_alt": "anime title Movie 1080p BluRay"
        }},
        {{
            "type": "special",
            "number": 1,
            "title": "Special Name",
            "search_query": "anime title Special 1080p",
            "search_query_alt": "anime title SP01 1080p"
        }},
        {{
            "type": "ova",
            "number": 1,
            "title": "OVA Name",
            "search_query": "anime title OVA 1080p",
            "search_query_alt": "anime title OVA 1080p"
        }}
    ],
    "search_queries": {{
        "batch_query": "anime title batch 1080p",
        "batch_query_alt": "anime title complete 1080p",
        "movie_query": "anime title movie 1080p",
        "special_query": "anime title specials 1080p"
    }}
}}

For episodes, if the anime has many episodes (50+), group them in batches (e.g., 1-12, 13-24).
For ongoing anime, list only confirmed aired episodes.
For each item, provide TWO search queries: one natural and one abbreviated.
Make sure to include MOVIES, SPECIALS, OVAs as separate entries.
Output ONLY valid JSON. No markdown, no explanation."""

    result = ai_chat(prompt, system_prompt)

    if not result:
        log("[AI] AI failed, generating content list from API data with enhanced AniList discovery")
        return generate_fallback_content(anime_name, mal_data, anilist_data)

    try:
        json_match = re.search(r'```(?:json)?\s*(.*?)```', result, re.DOTALL)
        if json_match:
            result = json_match.group(1)

        result = result.strip()
        if not result.startswith('{'):
            brace_start = result.find('{')
            if brace_start >= 0:
                result = result[brace_start:]

        content_data = json.loads(result)

        # Enhance with AniList data - add missing OVAs/specials/movies that AI missed
        content_data = enhance_with_anilist_relations(content_data, anilist_data, anime_name)

        return content_data
    except json.JSONDecodeError as e:
        log(f"[AI] Failed to parse AI response as JSON: {e}")
        log(f"Raw response (first 500 chars): {result[:500]}")
        return generate_fallback_content(anime_name, mal_data, anilist_data)

def enhance_with_anilist_relations(content_data, anilist_data, anime_name):
    """Add missing related content from AniList relations that AI might have missed"""
    if not anilist_data:
        return content_data

    existing_titles = set()
    for item in content_data.get('content', []):
        existing_titles.add(item.get('title', '').lower())

    # Get relations from AniList
    relations = ((anilist_data.get('relations') or {}).get('edges') or [])

    # Also try to get full relations if we have an AniList ID
    anilist_id = anilist_data.get('id')
    if anilist_id:
        full_data = fetch_anilist_full_relations(anilist_id)
        if full_data:
            relations = ((full_data.get('relations') or {}).get('edges') or []) or relations

    for edge in relations:
        rel_type = edge.get('relationType', '').upper()
        node = edge.get('node', {})
        entry_name = node.get('title', {}).get('english') or node.get('title', {}).get('romaji', '')
        entry_format = node.get('format', '')
        entry_episodes = node.get('episodes')

        if not entry_name:
            continue

        # Skip if already included
        if entry_name.lower() in existing_titles:
            continue

        # Also skip if it's a prequel/sequel TV series (handled separately by pipeline)
        if rel_type in ('PREQUEL', 'SEQUEL') and entry_format == 'TV':
            continue

        # Determine content type from format
        if entry_format == 'MOVIE' or 'MOVIE' in rel_type:
            content_type = 'movie'
            search_q = f"{entry_name} 1080p"
            search_q_alt = f"{entry_name} Movie 1080p BluRay"
        elif entry_format == 'OVA' or 'OVA' in rel_type:
            content_type = 'ova'
            search_q = f"{entry_name} OVA 1080p"
            search_q_alt = f"{entry_name} 1080p"
        elif entry_format == 'SPECIAL' or 'SPECIAL' in rel_type:
            content_type = 'special'
            search_q = f"{entry_name} Special 1080p"
            search_q_alt = f"{entry_name} SP01 1080p"
        elif entry_format == 'ONA' or 'ONA' in rel_type:
            content_type = 'ona'
            search_q = f"{entry_name} ONA 1080p"
            search_q_alt = f"{entry_name} 1080p"
        elif entry_format == 'TV':
            # This is a related TV series (spinoff, side story, etc.)
            # Include it as a special entry
            content_type = 'special'
            search_q = f"{entry_name} 1080p"
            search_q_alt = f"{entry_name} batch 1080p"
        else:
            content_type = 'special'
            search_q = f"{entry_name} 1080p"
            search_q_alt = f"{entry_name}"

        # For multi-episode OVAs/specials, create individual entries
        if entry_episodes and entry_episodes > 1 and content_type in ('ova', 'special', 'ona'):
            for ep in range(1, entry_episodes + 1):
                ep_title = f"{entry_name} Episode {ep}"
                if ep_title.lower() in existing_titles:
                    continue
                content_data['content'].append({
                    "type": content_type,
                    "number": ep,
                    "title": ep_title,
                    "search_query": f"{entry_name} Episode {ep} 1080p",
                    "search_query_alt": f"{entry_name} EP{ep:02d} 1080p",
                })
                existing_titles.add(ep_title.lower())
        else:
            content_data['content'].append({
                "type": content_type,
                "number": len([c for c in content_data['content'] if c['type'] == content_type]) + 1,
                "title": entry_name,
                "search_query": search_q,
                "search_query_alt": search_q_alt,
            })
            existing_titles.add(entry_name.lower())

    # Also search AniList for OVAs/specials/movies that aren't in relations
    for content_type in ['ova', 'special', 'movie']:
        search_results = search_anilist_for_related(anime_name, content_type)
        for result in search_results[:5]:  # Limit to top 5
            title = result.get('title', {}).get('english') or result.get('title', {}).get('romaji', '')
            if not title or title.lower() in existing_titles:
                continue
            # Skip if it's clearly a different anime (different franchise)
            anime_name_lower = anime_name.lower()
            title_lower = title.lower()
            if anime_name_lower not in title_lower and title_lower not in anime_name_lower:
                # Check for common short names
                name_words = set(anime_name_lower.split())
                title_words = set(title_lower.split())
                if not name_words.intersection(title_words):
                    continue

            format_map = {'ova': 'ova', 'special': 'special', 'movie': 'movie'}
            ct = format_map.get(content_type, 'special')
            content_data['content'].append({
                "type": ct,
                "number": len([c for c in content_data['content'] if c['type'] == ct]) + 1,
                "title": title,
                "search_query": f"{title} 1080p",
                "search_query_alt": f"{title} {content_type.upper()} 1080p",
            })
            existing_titles.add(title.lower())

    return content_data

def generate_fallback_content(anime_name, mal_data=None, anilist_data=None):
    """Fallback content list when AI fails — uses MAL/AniList relations to find movies, OVAs, specials"""
    episodes = None
    if mal_data:
        episodes = mal_data.get('episodes')
    if not episodes and anilist_data:
        episodes = anilist_data.get('episodes')

    content = []
    if episodes:
        for i in range(1, episodes + 1):
            content.append({
                "type": "episode",
                "number": i,
                "title": f"Episode {i}",
                "search_query": f"{anime_name} Episode {i} 1080p",
                "search_query_alt": f"{anime_name} EP{i:02d} 1080p"
            })

    # Extract related content (movies, specials, OVAs) from MAL relations
    related_content = []
    if mal_data:
        for rel in (mal_data.get('relations') or []):
            rel_type = rel.get('relation', '').lower()
            entries = rel.get('entry', [])
            for entry in entries:
                entry_name = entry.get('name', '')
                entry_type = entry.get('type', '')
                if not entry_name:
                    continue

                # Determine content type from relation or entry name
                entry_name_lower = entry_name.lower()
                if 'movie' in rel_type or 'movie' in entry_type.lower() or 'movie' in entry_name_lower or 'film' in entry_name_lower:
                    content_type = 'movie'
                    search_q = f"{entry_name} 1080p"
                    search_q_alt = f"{entry_name} Movie 1080p BluRay"
                elif 'ova' in rel_type or 'ova' in entry_type.lower() or 'ova' in entry_name_lower:
                    content_type = 'ova'
                    search_q = f"{entry_name} OVA 1080p"
                    search_q_alt = f"{entry_name} 1080p"
                elif 'special' in rel_type or 'special' in entry_type.lower():
                    content_type = 'special'
                    search_q = f"{entry_name} Special 1080p"
                    search_q_alt = f"{entry_name} SP01 1080p"
                elif 'ona' in rel_type or 'ona' in entry_type.lower():
                    content_type = 'ona'
                    search_q = f"{entry_name} ONA 1080p"
                    search_q_alt = f"{entry_name} 1080p"
                else:
                    content_type = 'special'
                    search_q = f"{entry_name} 1080p"
                    search_q_alt = f"{entry_name}"

                related_content.append({
                    "type": content_type,
                    "number": len([c for c in content if c['type'] == content_type]) + len([r for r in related_content if r['type'] == content_type]) + 1,
                    "title": entry_name,
                    "search_query": search_q,
                    "search_query_alt": search_q_alt,
                })

    # Also extract from AniList relations
    if anilist_data:
        for edge in ((anilist_data.get('relations') or {}).get('edges') or []):
            rel_type = edge.get('relationType', '').upper()
            node = edge.get('node', {})
            entry_name = node.get('title', {}).get('english') or node.get('title', {}).get('romaji', '')
            entry_format = node.get('format', '')
            entry_episodes = node.get('episodes')
            if not entry_name:
                continue

            # Skip if already in content from MAL relations
            if any(c['title'] == entry_name for c in related_content):
                continue

            # Skip prequel/sequel TV series
            if rel_type in ('PREQUEL', 'SEQUEL') and entry_format == 'TV':
                continue

            entry_name_lower = entry_name.lower()
            if 'MOVIE' in rel_type or entry_format == 'MOVIE' or 'movie' in entry_name_lower or 'film' in entry_name_lower:
                content_type = 'movie'
                search_q = f"{entry_name} 1080p"
                search_q_alt = f"{entry_name} Movie 1080p BluRay"
            elif 'OVA' in rel_type or entry_format == 'OVA' or 'ova' in entry_name_lower:
                content_type = 'ova'
                search_q = f"{entry_name} OVA 1080p"
                search_q_alt = f"{entry_name} 1080p"
            elif 'SPECIAL' in rel_type or entry_format == 'SPECIAL':
                content_type = 'special'
                search_q = f"{entry_name} Special 1080p"
                search_q_alt = f"{entry_name} SP01 1080p"
            elif 'ONA' in rel_type or entry_format == 'ONA':
                content_type = 'ona'
                search_q = f"{entry_name} ONA 1080p"
                search_q_alt = f"{entry_name} 1080p"
            else:
                content_type = 'special'
                search_q = f"{entry_name} 1080p"
                search_q_alt = f"{entry_name}"

            # For multi-episode content, create individual entries
            if entry_episodes and entry_episodes > 1 and content_type in ('ova', 'special', 'ona'):
                for ep in range(1, entry_episodes + 1):
                    related_content.append({
                        "type": content_type,
                        "number": len([c for c in content if c['type'] == content_type]) + len([r for r in related_content if r['type'] == content_type]) + 1,
                        "title": f"{entry_name} Episode {ep}",
                        "search_query": f"{entry_name} Episode {ep} 1080p",
                        "search_query_alt": f"{entry_name} EP{ep:02d} 1080p",
                    })
            else:
                related_content.append({
                    "type": content_type,
                    "number": len([c for c in content if c['type'] == content_type]) + len([r for r in related_content if r['type'] == content_type]) + 1,
                    "title": entry_name,
                    "search_query": search_q,
                    "search_query_alt": search_q_alt,
                })

    content.extend(related_content)

    result = {
        "anime_title": anime_name,
        "anime_title_japanese": mal_data.get('title_japanese', '') if mal_data else '',
        "anime_title_romaji": (anilist_data.get('title', {}).get('romaji', '') if anilist_data else ''),
        "mal_id": mal_data.get('mal_id') if mal_data else None,
        "status": mal_data.get('status', 'Unknown') if mal_data else 'Unknown',
        "total_episodes": episodes,
        "next_episode": None,
        "content": content,
        "search_queries": {
            "batch_query": f"{anime_name} batch 1080p",
            "batch_query_alt": f"{anime_name} complete 1080p",
            "movie_query": f"{anime_name} movie 1080p",
            "special_query": f"{anime_name} specials 1080p"
        }
    }

    # Enhance with AniList search for missing content types
    result = enhance_with_anilist_relations(result, anilist_data, anime_name)

    return result

# ===== Main =====
def main():
    if len(sys.argv) < 2:
        log("Usage: python ai_search.py <anime_name>")
        sys.exit(1)

    anime_name = sys.argv[1]
    log(f"\n{'='*60}")
    log(f"  AI Content Discovery: {anime_name}")
    log(f"{'='*60}\n")

    log(f"  Groq API key: {'SET' if GROQ_API_KEY else 'NOT SET'}")
    log(f"  Cerebras API key: {'SET' if CEREBRAS_API_KEY else 'NOT SET'}")
    log(f"  Gemini API key: {'SET' if GEMINI_API_KEY else 'NOT SET'}")

    log("[1/2] Fetching anime data from MAL & AniList...")
    mal_data = fetch_mal_anime(anime_name)
    if mal_data:
        log(f"  MAL: {mal_data.get('title')} ({mal_data.get('type')}, {mal_data.get('episodes', '?')} eps)")

    anilist_data = fetch_anilist_anime(anime_name)
    if anilist_data:
        title = anilist_data.get('title', {}).get('english') or anilist_data.get('title', {}).get('romaji')
        log(f"  AniList: {title} ({anilist_data.get('format')}, {anilist_data.get('episodes', '?')} eps)")

    log("\n[2/2] AI discovering all anime content...")
    content_data = discover_all_content(anime_name, mal_data, anilist_data)

    episodes = [c for c in content_data.get('content', []) if c.get('type') == 'episode']
    movies = [c for c in content_data.get('content', []) if c.get('type') == 'movie']
    specials = [c for c in content_data.get('content', []) if c.get('type') == 'special']
    ovas = [c for c in content_data.get('content', []) if c.get('type') in ('ova', 'ona')]

    log(f"\n  Found: {len(episodes)} Episodes, {len(movies)} Movies, {len(specials)} Specials, {len(ovas)} OVAs/ONAs")
    log(f"  Status: {content_data.get('status', 'Unknown')}")
    log(f"  Total Episodes: {content_data.get('total_episodes', 'Unknown')}")

    # Output ONLY pure JSON to stdout
    print(json.dumps(content_data, indent=2))

if __name__ == '__main__':
    main()
