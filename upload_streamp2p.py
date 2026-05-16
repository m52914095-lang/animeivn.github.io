#!/usr/bin/env python3
"""
StreamP2P Upload Script
Uploads processed videos to StreamP2P using their API.
Creates folder structure: Anime -> [Anime Name] -> SoftSUB/HardSub/Dub
Implements tus upload protocol natively with requests (no external tus client needed).

IMPORTANT: Only the final JSON is printed to stdout (for pipeline capture).
All progress/info is printed to stderr.
"""

import sys
import os
import json
import time
import hashlib
import requests
from pathlib import Path

# Import config with hardcoded key
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import STREAMP2P_API_KEY, STREAMP2P_API_BASE

CHUNK_SIZE = 52_428_800  # 50MB chunks for tus upload

def log(msg):
    """Print to stderr so it doesn't corrupt stdout JSON output"""
    print(msg, file=sys.stderr)

class StreamP2PUploader:
    def __init__(self, api_key=None):
        self.api_key = api_key or STREAMP2P_API_KEY
        if not self.api_key:
            raise ValueError('StreamP2P API key not set! Set STREAMP2P_API_KEY env var or update config.py')
        self.headers = {'api-token': self.api_key}
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _request(self, method, endpoint, **kwargs):
        url = f'{STREAMP2P_API_BASE}{endpoint}'
        try:
            res = self.session.request(method, url, **kwargs)
            if res.status_code == 401:
                raise Exception('StreamP2P API: Unauthorized - check API key')
            if res.status_code == 429:
                log('  Rate limited, waiting 60s...')
                time.sleep(60)
                res = self.session.request(method, url, **kwargs)
            return res
        except requests.exceptions.RequestException as e:
            raise Exception(f'StreamP2P API request failed: {e}')

    # ===== Folders =====
    def list_folders(self):
        """Get all folders"""
        res = self._request('GET', '/video/folder')
        res.raise_for_status()
        return res.json()

    def create_folder(self, name, parent_id=None, description=None):
        """Create a new folder"""
        body = {'name': name}
        if parent_id:
            body['folderId'] = parent_id
        if description:
            body['description'] = description

        res = self._request('POST', '/video/folder', json=body)
        if res.status_code == 201:
            data = res.json()
            log(f"  Created folder: {name} (ID: {data.get('id')})")
            return data.get('id')
        elif res.status_code == 200:
            data = res.json()
            return data.get('id')
        else:
            log(f"  Folder creation failed ({res.status_code}): {res.text[:200]}")
            return None

    def find_or_create_folder(self, name, parent_id=None):
        """Find existing folder or create new one"""
        try:
            folders = self.list_folders()
        except Exception as e:
            log(f"  Failed to list folders: {e}")
            return None

        for folder in folders:
            if folder.get('name') == name:
                if parent_id:
                    if folder.get('parentId') == parent_id:
                        return folder.get('id')
                elif not folder.get('parentId'):
                    return folder.get('id')

        return self.create_folder(name, parent_id)

    def ensure_folder_structure(self, anime_name):
        """
        Create folder structure: Anime -> [Anime Name]
        Returns folder IDs for anime subfolder
        """
        anime_parent_id = self.find_or_create_folder('Anime')
        anime_folder_id = self.find_or_create_folder(anime_name, anime_parent_id)
        return anime_folder_id

    # ===== Video Upload (tus protocol implemented natively) =====
    def get_upload_endpoint(self):
        """Get tus upload endpoint and access token"""
        res = self._request('GET', '/video/upload')
        res.raise_for_status()
        data = res.json()
        return data.get('tusUrl'), data.get('accessToken')

    def upload_file_tus(self, filepath, folder_id=None, progress_callback=None):
        """Upload a video file using the tus protocol."""
        tus_url, access_token = self.get_upload_endpoint()

        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)

        import base64
        metadata_pairs = [
            ('filename', base64.b64encode(filename.encode()).decode()),
            ('filetype', base64.b64encode(('video/x-matroska' if filename.endswith('.mkv') else 'video/mp4').encode()).decode()),
            ('accessToken', base64.b64encode(access_token.encode()).decode()),
        ]
        if folder_id:
            metadata_pairs.append(('folderId', base64.b64encode(str(folder_id).encode()).decode()))

        metadata_str = ','.join(f'{key} {value}' for key, value in metadata_pairs)

        headers = {
            'Tus-Resumable': '1.0.0',
            'Upload-Length': str(file_size),
            'Upload-Metadata': metadata_str,
        }

        log(f"  Uploading: {filename} ({file_size / 1024 / 1024:.1f} MB)")

        try:
            create_res = requests.post(tus_url, headers=headers, timeout=30)
            if create_res.status_code not in (200, 201):
                log(f"  tus POST failed ({create_res.status_code}): {create_res.text[:300]}")
                return False

            upload_url = create_res.headers.get('Location')
            if not upload_url:
                log(f"  tus POST missing Location header. Headers: {dict(create_res.headers)}")
                return False

            if upload_url.startswith('/'):
                from urllib.parse import urlparse
                parsed = urlparse(tus_url)
                upload_url = f"{parsed.scheme}://{parsed.netloc}{upload_url}"

        except Exception as e:
            log(f"  tus create failed: {e}")
            return False

        # Step 2: Upload chunks via PATCH
        offset = 0
        try:
            with open(filepath, 'rb') as f:
                while offset < file_size:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    chunk_headers = {
                        'Tus-Resumable': '1.0.0',
                        'Upload-Offset': str(offset),
                        'Content-Type': 'application/offset+octet-stream',
                    }

                    patch_res = requests.patch(
                        upload_url,
                        headers=chunk_headers,
                        data=chunk,
                        timeout=300,
                    )

                    if patch_res.status_code not in (200, 204):
                        log(f"  tus PATCH failed at offset {offset} ({patch_res.status_code}): {patch_res.text[:200]}")
                        time.sleep(2)
                        head_res = requests.head(upload_url, headers={'Tus-Resumable': '1.0.0'}, timeout=15)
                        if head_res.status_code in (200, 204):
                            server_offset = int(head_res.headers.get('Upload-Offset', offset))
                            if server_offset > offset:
                                f.seek(server_offset)
                                offset = server_offset
                                continue
                        return False

                    offset += len(chunk)
                    pct = (offset / file_size) * 100
                    if progress_callback:
                        progress_callback(pct)
                    else:
                        log(f"    Progress: {pct:.1f}% ({offset / 1024 / 1024:.1f} / {file_size / 1024 / 1024:.1f} MB)")

        except Exception as e:
            log(f"  tus upload failed: {e}")
            return False

        if offset >= file_size:
            log(f"  Upload complete: {filename}")
            return True
        else:
            log(f"  Upload incomplete: {offset}/{file_size} bytes")
            return False

    # ===== Advance Upload (magnet/URL direct) =====
    def create_advance_upload(self, url, name=None, folder_id=None, selected=None):
        """Create advanced upload task from URL/magnet."""
        body = {'url': url}
        if name:
            body['name'] = name
        if folder_id:
            body['folderId'] = folder_id
        if selected:
            body['selected'] = selected

        res = self._request('POST', '/video/advance-upload', json=body)
        if res.status_code == 201:
            data = res.json()
            task_id = data.get('id')
            log(f"  Advance upload task created: {task_id}")
            return task_id
        else:
            log(f"  Advance upload failed ({res.status_code}): {res.text[:200]}")
            return None

    def check_advance_upload(self, task_id):
        """Check advance upload task status"""
        res = self._request('GET', f'/video/advance-upload/{task_id}')
        res.raise_for_status()
        return res.json()

    # ===== Subtitles =====
    def upload_subtitle(self, video_id, subtitle_file, language='en', name=None):
        """Upload subtitle file for a video"""
        with open(subtitle_file, 'rb') as f:
            files = {
                'file': (os.path.basename(subtitle_file), f),
                'language': (None, language[:2]),
            }
            if name:
                files['name'] = (None, name)

            res = self._request('PUT', f'/video/manage/{video_id}/subtitle', files=files)
            if res.status_code == 201:
                log(f"  Subtitle uploaded: {os.path.basename(subtitle_file)}")
                return res.json()
            else:
                log(f"  Subtitle upload failed: {res.status_code}")
                return None

    # ===== Video Management =====
    def list_videos(self, page=1, per_page=50, search=None):
        """List videos"""
        params = {'page': page, 'perPage': per_page}
        if search:
            params['search'] = search
        res = self._request('GET', '/video/manage', params=params)
        res.raise_for_status()
        return res.json()

    def rename_video(self, video_id, new_name):
        """Rename a video"""
        res = self._request('PATCH', f'/video/manage/{video_id}', json={'name': new_name})
        return res.status_code == 200

    def link_video_to_folder(self, folder_id, video_id, position=None):
        """Link a video to a folder"""
        body = {'videoId': video_id}
        if position is not None:
            body['position'] = position
        res = self._request('POST', f'/video/folder/{folder_id}/link', json=body)
        return res.status_code == 204


def upload_processed_anime(uploader, processed_dir, anime_name):
    """Upload all processed videos for an anime"""
    results = {
        'anime_name': anime_name,
        'softsub': [],
        'hardsub': [],
        'dub': [],
        'folders': {},
    }

    log(f"\n[1/3] Setting up StreamP2P folders...")
    anime_folder_id = uploader.ensure_folder_structure(anime_name)
    results['folders']['anime'] = anime_folder_id

    softsub_folder = uploader.find_or_create_folder('SoftSub', anime_folder_id)
    hardsub_folder = uploader.find_or_create_folder('HardSub', anime_folder_id)
    dub_folder = uploader.find_or_create_folder('Dub', anime_folder_id)

    results['folders']['softsub'] = softsub_folder
    results['folders']['hardsub'] = hardsub_folder
    results['folders']['dub'] = dub_folder

    video_exts = ('.mkv', '.mp4', '.avi', '.webm')

    log(f"\n[2/3] Uploading soft sub files...")
    softsub_dir = os.path.join(processed_dir, 'softsub')
    if os.path.isdir(softsub_dir):
        for f in sorted(os.listdir(softsub_dir)):
            if f.lower().endswith(video_exts):
                filepath = os.path.join(softsub_dir, f)
                try:
                    success = uploader.upload_file_tus(filepath, softsub_folder)
                    if success:
                        results['softsub'].append({'file': f, 'status': 'uploaded'})
                except Exception as e:
                    log(f"  Failed to upload {f}: {e}")
                    results['softsub'].append({'file': f, 'status': 'failed', 'error': str(e)})
                time.sleep(2)

    log(f"\n[3/3] Uploading hard sub files...")
    hardsub_dir = os.path.join(processed_dir, 'hardsub')
    if os.path.isdir(hardsub_dir):
        for f in sorted(os.listdir(hardsub_dir)):
            if f.lower().endswith(video_exts):
                filepath = os.path.join(hardsub_dir, f)
                try:
                    success = uploader.upload_file_tus(filepath, hardsub_folder)
                    if success:
                        results['hardsub'].append({'file': f, 'status': 'uploaded'})
                except Exception as e:
                    log(f"  Failed to upload {f}: {e}")
                    results['hardsub'].append({'file': f, 'status': 'failed', 'error': str(e)})
                time.sleep(2)

    dub_dir = os.path.join(processed_dir, 'dub')
    if os.path.isdir(dub_dir):
        log(f"\n  Uploading dub files...")
        for f in sorted(os.listdir(dub_dir)):
            if f.lower().endswith(video_exts):
                filepath = os.path.join(dub_dir, f)
                try:
                    success = uploader.upload_file_tus(filepath, dub_folder)
                    if success:
                        results['dub'].append({'file': f, 'status': 'uploaded'})
                except Exception as e:
                    log(f"  Failed to upload {f}: {e}")
                    results['dub'].append({'file': f, 'status': 'failed', 'error': str(e)})
                time.sleep(2)

    return results


def upload_magnets_directly(uploader, magnet_data, anime_name):
    """
    Alternative: Upload magnets directly to StreamP2P
    Uses the advance-upload API with magnet URLs
    """
    results = {
        'anime_name': anime_name,
        'tasks': [],
    }

    anime_folder_id = uploader.ensure_folder_structure(anime_name)

    for batch in magnet_data.get('batch_magnets', []):
        task_id = uploader.create_advance_upload(
            url=batch['magnet'],
            name=batch['title'],
            folder_id=anime_folder_id,
        )
        if task_id:
            results['tasks'].append({
                'type': 'batch',
                'task_id': task_id,
                'title': batch['title'],
            })

    for item in magnet_data.get('magnets', []):
        if not item.get('magnet'):
            continue

        task_id = uploader.create_advance_upload(
            url=item['magnet'],
            name=item['title'],
            folder_id=anime_folder_id,
        )
        if task_id:
            results['tasks'].append({
                'type': item['type'],
                'task_id': task_id,
                'title': item['title'],
            })

        time.sleep(2)

    log(f"\n  Waiting for {len(results['tasks'])} upload tasks to process...")
    for task in results['tasks']:
        max_wait = 60
        waited = 0
        while waited < max_wait:
            try:
                status = uploader.check_advance_upload(task['task_id'])
                task_status = status.get('status', 'unknown')
                log(f"    Task {task['task_id']}: {task_status}")
                if task_status in ('completed', 'failed', 'error', 'done', 'finished'):
                    task['status'] = task_status
                    break
            except Exception as e:
                log(f"    Task check error: {e}")
            time.sleep(30)
            waited += 1

        if waited >= max_wait:
            task['status'] = 'timeout'
            log(f"    Task {task['task_id']}: timed out waiting")

    return results


def main():
    if len(sys.argv) < 3:
        log("Usage: python upload_streamp2p.py <processed_dir> <anime_name>")
        sys.exit(1)

    processed_dir = sys.argv[1]
    anime_name = sys.argv[2]

    if not STREAMP2P_API_KEY:
        log("Error: STREAMP2P_API_KEY not set!")
        sys.exit(1)

    log(f"\n{'='*60}")
    log(f"  StreamP2P Upload: {anime_name}")
    log(f"{'='*60}\n")

    uploader = StreamP2PUploader(STREAMP2P_API_KEY)

    # Check if we have processed files or should use direct magnet upload
    has_processed = os.path.isdir(processed_dir) and any(
        os.path.isdir(os.path.join(processed_dir, d))
        for d in ('softsub', 'hardsub', 'dub')
    )

    if has_processed:
        results = upload_processed_anime(uploader, processed_dir, anime_name)
    else:
        magnet_file = os.path.join(processed_dir, '..', 'magnet_links.json')
        if not os.path.exists(magnet_file):
            magnet_file = 'magnet_links.json'
        if os.path.exists(magnet_file):
            with open(magnet_file, 'r') as f:
                magnet_data = json.load(f)
            results = upload_magnets_directly(uploader, magnet_data, anime_name)
        else:
            log("No processed files or magnet links found!")
            sys.exit(1)

    # Save results
    results_file = 'upload_results.json'
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    # Summary
    log(f"\n{'='*60}")
    log(f"  Upload Summary")
    log(f"  Soft sub: {len(results.get('softsub', []))} uploaded")
    log(f"  Hard sub: {len(results.get('hardsub', []))} uploaded")
    log(f"  Dub: {len(results.get('dub', []))} uploaded")
    log(f"  Tasks: {len(results.get('tasks', []))}")
    log(f"{'='*60}\n")

    # Output ONLY pure JSON to stdout
    print(json.dumps(results, indent=2, default=str))

if __name__ == '__main__':
    main()
