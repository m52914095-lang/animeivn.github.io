#!/usr/bin/env python3
"""
Video Processing Script
- Extracts soft sub (original file with subtitles)
- Creates hard sub (burn subtitles into video)
- Extracts English audio track if present

IMPORTANT: Only the final JSON is printed to stdout (for pipeline capture).
All progress/info is printed to stderr.
"""

import sys
import os
import json
import subprocess
import shutil
import glob
import re

def log(msg):
    """Print to stderr so it doesn't corrupt stdout JSON output"""
    print(msg, file=sys.stderr)

def find_video_files(directory):
    """Find all video files recursively"""
    video_exts = ('.mkv', '.mp4', '.avi', '.wmv', '.flv', '.webm')
    files = []
    for root, dirs, filenames in os.walk(directory):
        for f in filenames:
            if f.lower().endswith(video_exts):
                files.append(os.path.join(root, f))
    return sorted(files)

def get_video_info(filepath):
    """Get video info using ffprobe"""
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        '-show_format',
        filepath,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        log(f"  ffprobe error for {filepath}: {e}")
    return None

def extract_subtitles(input_file, output_dir):
    """Extract subtitle tracks from video file"""
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    extracted = []

    info = get_video_info(input_file)
    if not info:
        return extracted

    subtitle_streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'subtitle']

    for idx, stream in enumerate(subtitle_streams):
        lang = stream.get('tags', {}).get('language', 'und')
        codec = stream.get('codec_name', '')
        stream_idx = stream.get('index', idx)

        if codec == 'ass':
            ext = 'ass'
        elif codec == 'subrip':
            ext = 'srt'
        elif codec == 'webvtt':
            ext = 'vtt'
        else:
            ext = 'srt'

        output_file = os.path.join(output_dir, f"{base_name}.{lang}.{ext}")

        cmd = [
            'ffmpeg', '-y',
            '-i', input_file,
            '-map', f'0:{stream_idx}',
            output_file,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and os.path.exists(output_file):
                extracted.append({
                    'file': output_file,
                    'language': lang,
                    'codec': codec,
                    'stream_index': stream_idx,
                })
                log(f"    Extracted subtitle: {lang} ({codec}) -> {os.path.basename(output_file)}")
        except Exception as e:
            log(f"    Failed to extract subtitle stream {stream_idx}: {e}")

    return extracted

def extract_audio(input_file, output_dir, language='eng'):
    """Extract English audio track if present"""
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_file))[0]

    info = get_video_info(input_file)
    if not info:
        return None

    audio_streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']

    eng_stream = None
    for stream in audio_streams:
        lang = stream.get('tags', {}).get('language', '').lower()
        title = stream.get('tags', {}).get('title', '').lower()
        if lang == 'eng' or lang == 'en' or 'english' in title:
            eng_stream = stream
            break

    if not eng_stream:
        return None

    stream_idx = eng_stream.get('index')
    codec = eng_stream.get('codec_name', 'aac')
    ext = 'mka' if codec in ('flac', 'pcm') else 'm4a'
    output_file = os.path.join(output_dir, f"{base_name}.eng.{ext}")

    cmd = [
        'ffmpeg', '-y',
        '-i', input_file,
        '-map', f'0:{stream_idx}',
        '-c:a', 'copy',
        output_file,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_file):
            log(f"    Extracted English audio -> {os.path.basename(output_file)}")
            return output_file
    except Exception as e:
        log(f"    Failed to extract English audio: {e}")

    return None

def create_soft_sub(input_file, output_dir, subtitles=None):
    """
    Create soft sub version:
    - Keep original video/audio
    - Ensure subtitles are embedded (MKV container)
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    output_file = os.path.join(output_dir, f"{base_name}.softsub.mkv")

    # If already MKV with subtitles, just copy
    if input_file.lower().endswith('.mkv'):
        info = get_video_info(input_file)
        if info:
            has_subs = any(s.get('codec_type') == 'subtitle' for s in info.get('streams', []))
            if has_subs:
                cmd = [
                    'ffmpeg', '-y',
                    '-i', input_file,
                    '-c', 'copy',
                    '-map', '0',
                    output_file,
                ]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    if result.returncode == 0 and os.path.exists(output_file):
                        log(f"    Soft sub (copy): {os.path.basename(output_file)}")
                        return output_file
                except Exception as e:
                    log(f"    Soft sub remux failed: {e}")

    # Otherwise, mux with external subtitles
    cmd = [
        'ffmpeg', '-y',
        '-i', input_file,
    ]

    sub_idx = 1
    if subtitles:
        for sub in subtitles:
            cmd.extend(['-i', sub['file']])

    cmd.extend([
        '-c', 'copy',
        '-map', '0:v',
        '-map', '0:a',
    ])

    if subtitles:
        for i, sub in enumerate(subtitles):
            cmd.extend(['-map', f'{i+1}:s'])
            lang = sub.get('language', 'und')
            cmd.extend([f'-metadata:s:s:{i}', f'language={lang}'])
    else:
        cmd.extend(['-map', '0:s?'])

    cmd.append(output_file)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and os.path.exists(output_file):
            log(f"    Soft sub: {os.path.basename(output_file)}")
            return output_file
    except Exception as e:
        log(f"    Soft sub creation failed: {e}")

    return None

def create_hard_sub(input_file, output_dir, subtitle_file=None):
    """
    Create hard sub version:
    - Burn subtitles into the video
    - Re-encode video with subtitles
    - If no subtitles found, create a direct copy as fallback
      (so every episode has a hardsub version even if subs are missing)
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    output_file = os.path.join(output_dir, f"{base_name}.hardsub.mp4")

    info = get_video_info(input_file)
    if not info:
        log(f"    Cannot get video info, creating direct copy for hardsub")
        return create_hardsub_copy(input_file, output_dir)

    has_subs = any(s.get('codec_type') == 'subtitle' for s in info.get('streams', []))

    if subtitle_file:
        # Use external subtitle file - escape special chars for ffmpeg
        sub_path = subtitle_file.replace('\\', '/').replace(':', '\\:')
        filter_complex = f"subtitles='{sub_path}'"
    elif has_subs:
        # Use embedded subtitles (first subtitle track)
        escaped_path = input_file.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
        filter_complex = f"subtitles='{escaped_path}':si=0"
    else:
        # NO SUBTITLES FOUND - Create a copy instead of skipping
        # This ensures every episode has a hardsub version
        log(f"    No subtitles found for hardsub - creating re-encoded copy (no subs burned)")
        return create_hardsub_copy(input_file, output_dir)

    cmd = [
        'ffmpeg', '-y',
        '-i', input_file,
        '-vf', filter_complex,
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '20',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        output_file,
    ]

    try:
        log(f"    Hard sub encoding (this may take a while)...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0 and os.path.exists(output_file):
            log(f"    Hard sub: {os.path.basename(output_file)}")
            return output_file
        else:
            log(f"    Hard sub encode failed, falling back to copy: {result.stderr[:300]}")
            return create_hardsub_copy(input_file, output_dir)
    except subprocess.TimeoutExpired:
        log(f"    Hard sub timed out, falling back to copy")
        return create_hardsub_copy(input_file, output_dir)
    except Exception as e:
        log(f"    Hard sub error, falling back to copy: {e}")
        return create_hardsub_copy(input_file, output_dir)

def create_hardsub_copy(input_file, output_dir):
    """
    Fallback: Create a re-encoded copy for hardsub when no subtitles are available.
    This ensures every episode gets a hardsub entry (even if it's just a copy).
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    output_file = os.path.join(output_dir, f"{base_name}.hardsub.mp4")

    # If input is already mp4, just copy; otherwise re-encode
    if input_file.lower().endswith('.mp4'):
        cmd = [
            'ffmpeg', '-y',
            '-i', input_file,
            '-c', 'copy',
            '-movflags', '+faststart',
            output_file,
        ]
    else:
        cmd = [
            'ffmpeg', '-y',
            '-i', input_file,
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '20',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-movflags', '+faststart',
            output_file,
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and os.path.exists(output_file):
            log(f"    Hard sub (no-subs copy): {os.path.basename(output_file)}")
            return output_file
        else:
            log(f"    Hard sub copy failed: {result.stderr[:200]}")
    except Exception as e:
        log(f"    Hard sub copy error: {e}")

    return None

def create_dub_version(input_file, audio_file, output_dir):
    """
    Create English dub version:
    - Replace Japanese audio with English audio track
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    output_file = os.path.join(output_dir, f"{base_name}.dub.mp4")

    cmd = [
        'ffmpeg', '-y',
        '-i', input_file,
        '-i', audio_file,
        '-map', '0:v',
        '-map', '1:a',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        output_file,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and os.path.exists(output_file):
            log(f"    Dub version: {os.path.basename(output_file)}")
            return output_file
    except Exception as e:
        log(f"    Dub version failed: {e}")

    return None

def process_directory(input_dir, output_dir):
    """Process all video files in a directory"""
    video_files = find_video_files(input_dir)

    if not video_files:
        log("  No video files found!")
        return

    log(f"  Found {len(video_files)} video file(s)")

    results = {
        'softsub': [],
        'hardsub': [],
        'dub': [],
    }

    for idx, video in enumerate(video_files):
        log(f"\n  [{idx+1}/{len(video_files)}] Processing: {os.path.basename(video)}")

        # Step 1: Extract subtitles
        sub_dir = os.path.join(output_dir, '_subtitles', str(idx))
        subtitles = extract_subtitles(video, sub_dir)

        # Step 2: Create soft sub version
        soft_dir = os.path.join(output_dir, 'softsub')
        soft_file = create_soft_sub(video, soft_dir, subtitles if subtitles else None)
        if soft_file:
            results['softsub'].append(soft_file)

        # Step 3: Create hard sub version
        # FIXED: Now always creates a hardsub entry even if no subtitles found
        hard_dir = os.path.join(output_dir, 'hardsub')
        sub_file = subtitles[0]['file'] if subtitles else None
        hard_file = create_hard_sub(video, hard_dir, sub_file)
        if hard_file:
            results['hardsub'].append(hard_file)

        # Step 4: Extract English audio
        audio_dir = os.path.join(output_dir, '_audio')
        eng_audio = extract_audio(video, audio_dir)

        # Step 5: Create dub version if English audio exists
        if eng_audio:
            dub_dir = os.path.join(output_dir, 'dub')
            dub_file = create_dub_version(video, eng_audio, dub_dir)
            if dub_file:
                results['dub'].append(dub_file)

    # Summary (to stderr)
    log(f"\n  Processing Summary:")
    log(f"    Soft sub: {len(results['softsub'])} files")
    log(f"    Hard sub: {len(results['hardsub'])} files")
    log(f"    Dub: {len(results['dub'])} files")

    # Save results
    results_file = os.path.join(output_dir, 'process_results.json')
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)

    # Output ONLY pure JSON to stdout
    print(json.dumps(results, indent=2))

if __name__ == '__main__':
    if len(sys.argv) < 3:
        log("Usage: python process_video.py <input_dir> <output_dir>")
        sys.exit(1)

    input_dir = sys.argv[1]
    output_dir = sys.argv[2]
    process_directory(input_dir, output_dir)
