"""Saved routing settings and shared private file writes."""
import fcntl
import json
import math
import os
import tempfile
from pathlib import Path

DEFAULTS = {'usage_policy': 'balanced', 'reserve_percent': 10, 'show_usage': True,
            'usage_limit': 'codex', 'allow_model': None, 'jev_model': 'jev-latest'}


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'jev-codex/config.json'


def data_dir():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'jev-codex'


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), mode)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def private_write(path, value):
    atomic_write(path, json.dumps(value, indent=2) + '\n')


def validate(values):
    if not isinstance(values, dict) or values.keys() - DEFAULTS.keys():
        raise ValueError('Settings must be an object containing only supported setting names.')
    result = {**DEFAULTS, **values}
    if result['usage_policy'] not in ('quality', 'balanced', 'conserve'):
        raise ValueError('usage_policy must be quality, balanced, or conserve.')
    reserve = result['reserve_percent']
    if type(reserve) not in (int, float) or not math.isfinite(reserve) or not 0 <= reserve < 100:
        raise ValueError('reserve_percent must be a number from 0 to less than 100.')
    if type(result['show_usage']) is not bool:
        raise ValueError('show_usage must be true or false.')
    for name in ('usage_limit', 'jev_model'):
        if not isinstance(result[name], str) or not result[name].strip():
            raise ValueError(f'{name} must be a nonempty string.')
    allowed = result['allow_model']
    if allowed is not None and (not isinstance(allowed, list) or not allowed or
                               any(not isinstance(v, str) or not v.strip() for v in allowed)):
        raise ValueError('allow_model must be null or a nonempty array of model names.')
    return result


def load_settings(path=None, overrides=None):
    path = path or config_path()
    values = json.loads(path.read_text()) if path.exists() else {}
    # Validate saved values even when a command-line option would hide the error.
    return validate({**validate(values), **(overrides or {})})


def set_setting(name, value, path=None):
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path.with_suffix('.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        values = load_settings(path)
        values[name] = value
        private_write(path, validate(values))


def default_env_file():
    saved = config_path().with_name('credentials.env')
    return saved if saved.exists() else Path(__file__).with_name('.env')
