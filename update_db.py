#!/usr/bin/env python3
"""
Update Database Script
Updates the local anime database with upload results
and generates the website data file.

IMPORTANT: Only the final JSON is printed to stdout (for pipeline capture).
All progress/info is printed to stderr.
"""

import sys
import os
import json
from datetime import datetime, timezone

def log(msg):
    """Print to stderr so it doesn't corrupt stdout JSON output"""
    print(msg, file=sys.stderr)

DB_FILE = 'anime_db.json'

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def update_anime_entry(anime_name, upload_results):
    """Update or create an entry for an anime"""
    db = load_db()

    key = anime_name.lower().replace(' ', '_')

    if key not in db:
        db[key] = {
            'name': anime_name,
            'added_at': datetime.now(timezone.utc).isoformat(),
            'versions': {},
            'episodes': {},
            'status': 'available',
        }

    entry = db[key]
    entry['last_updated'] = datetime.now(timezone.utc).isoformat()

    if 'folders' in upload_results:
        entry['streamp2p_folders'] = upload_results['folders']

    if 'softsub' in upload_results:
        for item in upload_results['softsub']:
            if item.get('status') == 'uploaded':
                entry['versions']['softsub'] = True

    if 'hardsub' in upload_results:
        for item in upload_results['hardsub']:
            if item.get('status') == 'uploaded':
                entry['versions']['hardsub'] = True

    if 'dub' in upload_results:
        for item in upload_results['dub']:
            if item.get('status') == 'uploaded':
                entry['versions']['dub'] = True

    entry['episode_count'] = {
        'softsub': len([x for x in upload_results.get('softsub', []) if x.get('status') == 'uploaded']),
        'hardsub': len([x for x in upload_results.get('hardsub', []) if x.get('status') == 'uploaded']),
        'dub': len([x for x in upload_results.get('dub', []) if x.get('status') == 'uploaded']),
    }

    db[key] = entry
    save_db(db)

    log(f"  Updated database entry for: {anime_name}")
    log(f"    Soft sub: {entry['episode_count']['softsub']} episodes")
    log(f"    Hard sub: {entry['episode_count']['hardsub']} episodes")
    log(f"    Dub: {entry['episode_count']['dub']} episodes")

def generate_website_data():
    """Generate a JSON file for the website to consume"""
    db = load_db()

    website_data = []
    for key, entry in db.items():
        website_data.append({
            'name': entry.get('name', key),
            'key': key,
            'status': entry.get('status', 'unknown'),
            'versions': entry.get('versions', {}),
            'episode_count': entry.get('episode_count', {}),
            'last_updated': entry.get('last_updated', ''),
            'streamp2p_folders': entry.get('streamp2p_folders', {}),
        })

    output_file = 'website_data.json'
    with open(output_file, 'w') as f:
        json.dump(website_data, f, indent=2)

    log(f"  Generated website data: {len(website_data)} anime entries")
    return website_data

def main():
    if len(sys.argv) < 2:
        log("Usage: python update_db.py <anime_name> [upload_results.json]")
        log("       python update_db.py --generate")
        sys.exit(1)

    if sys.argv[1] == '--generate':
        generate_website_data()
        return

    anime_name = sys.argv[1]
    upload_results = {}

    if len(sys.argv) >= 3:
        results_file = sys.argv[2]
        if os.path.exists(results_file):
            with open(results_file, 'r') as f:
                upload_results = json.load(f)

    update_anime_entry(anime_name, upload_results)
    generate_website_data()

if __name__ == '__main__':
    main()
