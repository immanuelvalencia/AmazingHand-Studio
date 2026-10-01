"""Validated settings for text-only local chat."""
import json
import math
from pathlib import Path
from urllib.parse import urlparse

CONFIG_PATH = Path(__file__).resolve().parents[2] / 'configs' / 'assistant.json'
DEFAULTS = {
    'ollama_url': 'http://127.0.0.1:11434', 'model': 'qwen2.5:7b',
    'timeout_seconds': 120, 'conversation_history_limit': 20,
}


def validate_settings(values):
    if not isinstance(values, dict):
        raise ValueError('Settings must be a JSON object.')
    settings = {key: values.get(key, value) for key, value in DEFAULTS.items()}
    for key in ('ollama_url', 'model'):
        if not isinstance(settings[key], str) or not settings[key].strip():
            raise ValueError(f'Set {key} in the settings.')
        settings[key] = settings[key].strip()
    url = urlparse(settings['ollama_url'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.query or url.fragment or url.username:
        raise ValueError('Ollama URL must be an HTTP(S) server URL without credentials or query parameters.')
    timeout = settings['timeout_seconds']
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 5 <= timeout <= 600:
        raise ValueError('timeout_seconds must be between 5 and 600.')
    limit = settings['conversation_history_limit']
    if type(limit) is not int or not 2 <= limit <= 100:
        raise ValueError('conversation_history_limit must be an integer between 2 and 100.')
    return settings


def load_settings(path=CONFIG_PATH):
    try:
        return validate_settings(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    except FileNotFoundError:
        return DEFAULTS.copy()
