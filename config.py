#!/usr/bin/env python3
"""
Configuration file for the anime pipeline.
API keys are hardcoded as defaults but can be overridden via environment variables.
"""

import os

# ===== AI Configuration =====
# Groq is PRIMARY, Cerebras is SECONDARY, Gemini is BACKUP
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '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')
GROQ_MODEL = 'llama-3.1-8b-instant'
GROQ_FALLBACK_MODEL = 'llama-3.3-70b-versatile'

CEREBRAS_API_KEY = os.environ.get('CEREBRAS_API_KEY', '')
CEREBRAS_MODEL = 'gpt-oss-120b'

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')  # Set by workflow from secrets.ai
GEMINI_MODEL = 'gemini-2.0-flash'

# ===== StreamP2P Configuration =====
STREAMP2P_API_KEY = os.environ.get('STREAMP2P_API_KEY', '46d3af3546d3931092a5b078')
STREAMP2P_API_BASE = os.environ.get('STREAMP2P_API_BASE', 'https://streamp2p.com/api/v1')
STREAMP2P_UPLOAD_CHUNK_SIZE = 52_428_800  # 50MB

# ===== Folder Structure =====
PARENT_FOLDER_NAME = 'Anime'

# ===== Download Configuration =====
MAX_CONCURRENT_DOWNLOADS = 3
DOWNLOAD_TIMEOUT = 7200
LARGE_FILE_THRESHOLD_GB = 30
ARIA2C_MAX_CONNECTIONS = 4

# ===== Video Processing =====
HARD_SUB_CRF = 20
HARD_SUB_PRESET = 'medium'
AUDIO_BITRATE = '192k'
VIDEO_CODEC = 'libx264'
AUDIO_CODEC = 'aac'

# ===== Nyaa Search =====
NYAA_BASE_URL = 'https://nyaa.si'
NYAA_CATEGORY = '1_2'
NYAA_MIN_SEEDERS = 1
NYAA_PREFER_TRUSTED = True
NYAA_RATE_LIMIT_SECONDS = 1.5

# ===== MyAnimeList / AniList =====
JIKAN_BASE_URL = 'https://api.jikan.moe/v4'
ANILIST_BASE_URL = 'https://graphql.anilist.co'

# ===== Auto-Monitor =====
MONITOR_CHECK_INTERVAL_HOURS = 6
MONITOR_FILE = 'monitored_anime.json'
MONITOR_STATE_FILE = 'monitor_state.json'
