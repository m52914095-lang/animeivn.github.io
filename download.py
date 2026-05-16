#!/usr/bin/env python3
"""
Download Manager - Downloads torrents via aria2c from magnet links.
Handles large files in 30GB segments.

IMPORTANT: Only the final JSON is printed to stdout (for pipeline capture).
All progress/info is printed to stderr.
"""

import sys
import os
import json
import subprocess
import time
import re

def log(msg):
    """Print to stderr so it doesn't corrupt stdout JSON output"""
    print(msg, file=sys.stderr)

def run_aria2c(magnet_link, output_dir, max_size_gb=30, timeout=7200):
    """
    Download a torrent using aria2c.
    For files >30GB, downloads in segments.
    """
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        'aria2c',
        '--seed-time=0',
        '--max-tries=5',
        '--retry-wait=30',
        '--continue=true',
        '--max-connection-per-server=4',
        '--split=4',
        '--min-split-size=10M',    # FIXED: was '1G' which caused aria2c error code 28
        '--file-allocation=none',
        '--summary-interval=30',
        '--timeout=300',
        '--connect-timeout=60',
        '--bt-max-peers=55',
        '--enable-dht=true',
        '--enable-dht6=true',
        '--dir=' + output_dir,
        magnet_link,
    ]

    log(f"  Starting aria2c download...")
    log(f"  Output dir: {output_dir}")

    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            log(f"  Download complete!")
            return True
        else:
            log(f"  aria2c exited with code {result.returncode}")
            log(f"  stderr: {result.stderr[:500]}")

            # If aria2c fails with magnet, try adding trackers to help
            if result.returncode != 0 and 'magnet:?' in magnet_link:
                log(f"  Retrying with tracker list...")
                return run_aria2c_with_trackers(magnet_link, output_dir, timeout)

            return False

    except subprocess.TimeoutExpired:
        log(f"  Download timed out after {timeout}s")
        return False
    except Exception as e:
        log(f"  Download error: {e}")
        return False

def run_aria2c_with_trackers(magnet_link, output_dir, timeout=7200):
    """Retry download with popular BitTorrent trackers appended to magnet link"""
    # Popular public trackers to improve peer discovery
    trackers = [
        'udp://tracker.opentrackr.org:1337/announce',
        'udp://open.stealth.si:80/announce',
        'udp://tracker.torrent.eu.org:451/announce',
        'udp://tracker.bittor.pw:1337/announce',
        'udp://public.popcorn-tracker.org:6969/announce',
        'udp://tracker.dler.org:6969/announce',
        'udp://exodus.desync.com:6969/announce',
        'udp://open.demonii.si:1337/announce',
    ]

    # Append trackers to magnet link
    enhanced_magnet = magnet_link
    for tracker in trackers:
        enhanced_magnet += '&tr=' + tracker

    cmd = [
        'aria2c',
        '--seed-time=0',
        '--max-tries=5',
        '--retry-wait=30',
        '--continue=true',
        '--max-connection-per-server=4',
        '--split=4',
        '--min-split-size=10M',
        '--file-allocation=none',
        '--summary-interval=30',
        '--timeout=300',
        '--connect-timeout=60',
        '--bt-max-peers=80',
        '--enable-dht=true',
        '--enable-dht6=true',
        '--bt-enable-lpd=true',
        '--dir=' + output_dir,
        enhanced_magnet,
    ]

    log(f"  Retrying with enhanced trackers...")

    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            log(f"  Download complete with trackers!")
            return True
        else:
            log(f"  aria2c (with trackers) exited with code {result.returncode}")
            log(f"  stderr: {result.stderr[:500]}")
            return False

    except subprocess.TimeoutExpired:
        log(f"  Download (with trackers) timed out after {timeout}s")
        return False
    except Exception as e:
        log(f"  Download (with trackers) error: {e}")
        return False

def organize_downloads(download_dir, anime_title):
    """
    Organize downloaded files into a structured directory.
    Separates episodes, movies, specials.
    """
    organized = {
        'episodes': [],
        'movies': [],
        'specials': [],
        'other': [],
    }

    video_extensions = ('.mkv', '.mp4', '.avi', '.wmv', '.flv', '.webm')
    for root, dirs, files in os.walk(download_dir):
        for f in files:
            if f.lower().endswith(video_extensions):
                filepath = os.path.join(root, f)
                name_lower = f.lower()

                if 'movie' in name_lower or 'film' in name_lower:
                    organized['movies'].append(filepath)
                elif any(x in name_lower for x in ['special', 'ova', 'ona', 'extra', 'ncop', 'nced', 'preview']):
                    organized['specials'].append(filepath)
                elif any(x in name_lower for x in ['ep', 'episode', 'e0', 'e1', 'e2', ' - 0', ' - 1', ' - 2']):
                    organized['episodes'].append(filepath)
                else:
                    organized['episodes'].append(filepath)

    def natural_sort_key(s):
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', os.path.basename(s))]

    organized['episodes'].sort(key=natural_sort_key)
    organized['movies'].sort(key=natural_sort_key)
    organized['specials'].sort(key=natural_sort_key)

    return organized

def main():
    if len(sys.argv) < 3:
        log("Usage: python download.py <magnet_links.json> <output_dir>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_dir = sys.argv[2]

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    with open(input_file, 'r') as f:
        magnet_data = json.load(f)

    anime_title = magnet_data.get('anime_title', 'Unknown')
    batch_magnets = magnet_data.get('batch_magnets', [])
    individual_magnets = magnet_data.get('magnets', [])

    log(f"\n{'='*60}")
    log(f"  Download Manager: {anime_title}")
    log(f"{'='*60}\n")

    download_results = {
        'anime_title': anime_title,
        'downloaded': [],
        'failed': [],
    }

    # Step 1: Download batch torrents
    if batch_magnets:
        log(f"[1/2] Downloading {len(batch_magnets)} batch torrent(s)...")
        for idx, batch in enumerate(batch_magnets):
            log(f"\n  Batch {idx+1}: {batch.get('title', 'Unknown')}")
            batch_dir = os.path.join(output_dir, f'batch_{idx+1}')

            success = run_aria2c(
                batch['magnet'],
                batch_dir,
                timeout=7200
            )

            if success:
                download_results['downloaded'].append({
                    'type': 'batch',
                    'title': batch.get('title', ''),
                    'path': batch_dir,
                    'size': batch.get('size', 'unknown'),
                })
            else:
                download_results['failed'].append({
                    'type': 'batch',
                    'title': batch.get('title', ''),
                    'magnet': batch.get('magnet', ''),
                })

    # Step 2: Download individual items
    items_to_download = [m for m in individual_magnets if m.get('magnet')]
    if items_to_download:
        log(f"\n[2/2] Downloading {len(items_to_download)} individual item(s)...")
        for idx, item in enumerate(items_to_download):
            log(f"\n  [{idx+1}/{len(items_to_download)}] {item.get('type', '?').upper()}: {item.get('title', '?')}")

            item_dir = os.path.join(output_dir, f"{item.get('type', 'ep')}_{item.get('number', idx+1)}")

            timeout = 3600
            try:
                size_gb = float(item.get('size_gb', 0))
            except (ValueError, TypeError):
                size_gb = 0
            if size_gb > 30:
                log(f"  Large file ({size_gb:.1f}GB) - extended timeout")
                timeout = 7200

            success = run_aria2c(
                item['magnet'],
                item_dir,
                timeout=timeout,
            )

            if success:
                download_results['downloaded'].append({
                    'type': item.get('type', 'episode'),
                    'number': item.get('number'),
                    'title': item.get('title', ''),
                    'path': item_dir,
                    'size': item.get('size', 'unknown'),
                })
            else:
                download_results['failed'].append({
                    'type': item.get('type', 'episode'),
                    'number': item.get('number'),
                    'title': item.get('title', ''),
                    'magnet': item.get('magnet', ''),
                })

    # Organize downloaded files
    log(f"\n  Organizing downloaded files...")
    organized = organize_downloads(output_dir, anime_title)
    download_results['organized'] = {
        'episodes': len(organized['episodes']),
        'movies': len(organized['movies']),
        'specials': len(organized['specials']),
        'episode_files': organized['episodes'],
        'movie_files': organized['movies'],
        'special_files': organized['specials'],
    }

    # Summary (to stderr only)
    log(f"\n{'='*60}")
    log(f"  Download Summary")
    log(f"  Downloaded: {len(download_results['downloaded'])}")
    log(f"  Failed: {len(download_results['failed'])}")
    log(f"  Episodes: {len(organized['episodes'])}")
    log(f"  Movies: {len(organized['movies'])}")
    log(f"  Specials: {len(organized['specials'])}")
    log(f"{'='*60}\n")

    # Save results to file
    results_file = os.path.join(output_dir, 'download_results.json')
    with open(results_file, 'w') as f:
        json.dump(download_results, f, indent=2, default=str)

    # Output ONLY pure JSON to stdout
    print(json.dumps(download_results, indent=2, default=str))

if __name__ == '__main__':
    main()
