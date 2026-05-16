#!/usr/bin/env python3
"""
Auto-Schedule Script for Ongoing Anime
- Checks monitored ongoing anime for new episodes
- Triggers download pipeline for new episodes
- Manages the ongoing anime monitoring schedule

IMPORTANT: Only the final JSON is printed to stdout (for pipeline capture).
All progress/info is printed to stderr.
"""

import sys
import os
import json
import time
import re
import requests
from datetime import datetime, timezone

# Import config with hardcoded keys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import GROQ_API_KEY, GEMINI_API_KEY, STREAMP2P_API_KEY, STREAMP2P_API_BASE, JIKAN_BASE_URL, ANILIST_BASE_URL

JIKAN_BASE = JIKAN_BASE_URL
ANILIST_BASE = ANILIST_BASE_URL

MONITORED_FILE = 'monitored_anime.json'
STATE_FILE = 'monitor_state.json'

def log(msg):
    """Print to stderr so it doesn't corrupt stdout JSON output"""
    print(msg, file=sys.stderr)

def load_monitored():
    if os.path.exists(MONITORED_FILE):
        with open(MONITORED_FILE, 'r') as f:
            return json.load(f)
    return []

def save_monitored(data):
    with open(MONITORED_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_state(data):
    with open(STATE_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def check_anilist_new_episodes(anime_name):
    """Check AniList for new episode airing schedule"""
    gql = """
    query ($search: String) {
        Page(page: 1, perPage: 1) {
            media(search: $search, type: ANIME, isAdult: false) {
                id
                title { romaji english }
                status
                episodes
                nextAiringEpisode { episode airingAt timeUntilAiring }
                airingSchedule { nodes { episode airingAt } }
            }
        }
    }
    """
    try:
        res = requests.post(ANILIST_BASE,
                          json={'query': gql, 'variables': {'search': anime_name}},
                          timeout=15)
        res.raise_for_status()
        media = res.json().get('data', {}).get('Page', {}).get('media', [])
        if media:
            return media[0]
    except Exception as e:
        log(f"  AniList check error for {anime_name}: {e}")
    return None

def check_mal_anime(mal_id):
    """Check MAL for anime status"""
    try:
        res = requests.get(f'{JIKAN_BASE}/anime/{mal_id}', timeout=15)
        if res.status_code == 429:
            time.sleep(1)
            res = requests.get(f'{JIKAN_BASE}/anime/{mal_id}', timeout=15)
        res.raise_for_status()
        return res.json().get('data')
    except Exception as e:
        log(f"  MAL check error for ID {mal_id}: {e}")
    return None

def get_streamp2p_uploaded_episodes(anime_name, api_key=None):
    """Check how many episodes are already uploaded to StreamP2P"""
    api_key = api_key or STREAMP2P_API_KEY
    if not api_key:
        return 0

    try:
        headers = {'api-token': api_key}
        res = requests.get(f'{STREAMP2P_API_BASE}/video/folder', headers=headers, timeout=15)
        res.raise_for_status()
        folders = res.json()

        anime_parent = next((f for f in folders if f.get('name', '').lower() == 'anime'), None)
        if anime_parent:
            subfolders = [f for f in folders if f.get('parentId') == anime_parent['id']]
            anime_folder = next((f for f in subfolders if anime_name.lower() in f.get('name', '').lower()), None)
        else:
            anime_folder = next((f for f in folders if anime_name.lower() in f.get('name', '').lower()), None)

        if anime_folder:
            res = requests.get(
                f"{STREAMP2P_API_BASE}/video/folder/{anime_folder['id']}?perPage=100",
                headers=headers, timeout=15
            )
            res.raise_for_status()
            data = res.json()
            return len(data.get('data', []))

    except Exception as e:
        log(f"  StreamP2P check error: {e}")

    return 0

def trigger_download(anime_name, episode_range=None):
    """Print the GitHub Actions command to trigger download"""
    cmd = f"gh workflow run anime-pipeline.yml -f anime_name=\"{anime_name}\""
    if episode_range:
        log(f"  Would trigger: {cmd} (episodes: {episode_range})")
    else:
        log(f"  Would trigger: {cmd}")
    return cmd

def add_anime_to_monitor(anime_name, mal_id=None, anilist_id=None):
    """Add an anime to the monitoring list"""
    monitored = load_monitored()

    for item in monitored:
        if item.get('name', '').lower() == anime_name.lower():
            log(f"  {anime_name} is already being monitored")
            return

    monitored.append({
        'name': anime_name,
        'mal_id': mal_id,
        'anilist_id': anilist_id,
        'added_at': datetime.now(timezone.utc).isoformat(),
        'last_episode_checked': 0,
        'status': 'monitoring',
    })

    save_monitored(monitored)
    log(f"  Added {anime_name} to monitoring list")

def remove_anime_from_monitor(anime_name):
    """Remove an anime from the monitoring list"""
    monitored = load_monitored()
    monitored = [m for m in monitored if m.get('name', '').lower() != anime_name.lower()]
    save_monitored(monitored)
    log(f"  Removed {anime_name} from monitoring list")

def check_all_monitored():
    """Check all monitored ongoing anime for new episodes"""
    monitored = load_monitored()
    state = load_state()

    if not monitored:
        log("  No anime being monitored. Add anime with: python auto_schedule.py --add \"Anime Name\"")
        return

    log(f"\n  Checking {len(monitored)} monitored anime...\n")

    actions = []

    for item in monitored:
        anime_name = item.get('name', '')
        log(f"  Checking: {anime_name}")

        anilist_data = check_anilist_new_episodes(anime_name)

        if anilist_data:
            status = anilist_data.get('status', '')
            total_eps = anilist_data.get('episodes')
            next_ep = anilist_data.get('nextAiringEpisode', {})

            log(f"    Status: {status}")
            log(f"    Total episodes: {total_eps}")
            log(f"    Next episode: {next_ep.get('episode', 'N/A')}")

            uploaded = get_streamp2p_uploaded_episodes(anime_name)
            log(f"    Uploaded to StreamP2P: {uploaded} videos")

            last_checked = item.get('last_episode_checked', 0)

            if status == 'RELEASING' and next_ep:
                next_ep_num = next_ep.get('episode', 0)
                if next_ep_num > last_checked + 1:
                    new_range = f"{last_checked + 1}-{next_ep_num - 1}"
                    log(f"    NEW EPISODES: {new_range}")
                    actions.append({
                        'anime': anime_name,
                        'action': 'download_new',
                        'episode_range': new_range,
                        'from': last_checked + 1,
                        'to': next_ep_num - 1,
                    })
                    item['last_episode_checked'] = next_ep_num - 1

            elif status == 'FINISHED':
                if total_eps and total_eps > last_checked:
                    new_range = f"{last_checked + 1}-{total_eps}"
                    log(f"    ANIME COMPLETED - remaining: {new_range}")
                    actions.append({
                        'anime': anime_name,
                        'action': 'download_remaining',
                        'episode_range': new_range,
                        'from': last_checked + 1,
                        'to': total_eps,
                    })
                    item['last_episode_checked'] = total_eps
                    item['status'] = 'completed'

            state_key = anime_name.lower().replace(' ', '_')
            state[state_key] = {
                'last_checked': datetime.now(timezone.utc).isoformat(),
                'status': status,
                'next_episode': next_ep,
                'uploaded': uploaded,
            }

        time.sleep(1)

    save_monitored(monitored)
    save_state(state)

    if actions:
        log(f"\n  Actions to take:")
        for action in actions:
            log(f"    - {action['anime']}: {action['action']} ({action['episode_range']})")
            trigger_download(action['anime'], action['episode_range'])
    else:
        log(f"\n  No new episodes found.")

    return actions

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Auto-Schedule Ongoing Anime Monitor')
    parser.add_argument('--add', help='Add anime to monitor list')
    parser.add_argument('--remove', help='Remove anime from monitor list')
    parser.add_argument('--list', action='store_true', help='List monitored anime')
    parser.add_argument('--check', action='store_true', help='Check all monitored anime')
    parser.add_argument('--mal-id', type=int, help='MAL ID for --add')
    parser.add_argument('--anilist-id', type=int, help='AniList ID for --add')

    args = parser.parse_args()

    if args.add:
        add_anime_to_monitor(args.add, args.mal_id, args.anilist_id)
    elif args.remove:
        remove_anime_from_monitor(args.remove)
    elif args.list:
        monitored = load_monitored()
        if not monitored:
            log("  No anime being monitored.")
        else:
            log(f"\n  Monitored Anime ({len(monitored)}):")
            for idx, item in enumerate(monitored, 1):
                log(f"    {idx}. {item.get('name')} (Last checked ep: {item.get('last_episode_checked', 0)}, Status: {item.get('status', 'unknown')})")
    elif args.check:
        check_all_monitored()
    else:
        specific = os.environ.get('SPECIFIC_ANIME', '')
        if specific:
            log(f"  Checking specific anime: {specific}")
            add_anime_to_monitor(specific)
        check_all_monitored()

if __name__ == '__main__':
    main()
