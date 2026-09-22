"""Saved routing settings and shared private file writes."""
import fcntl
import json
import math
import os
import re
import tempfile
from pathlib import Path

VERSION = '0.4.1'
DEFAULTS = {'routing_preference': 'balanced', 'maximum_effort': 'automatic',
            'routing_mode': 'thread',
            'usage_policy': 'balanced', 'reserve_percent': 10, 'show_usage': True,
            'usage_limit': 'codex', 'allow_model': None, 'allow_effort': None,
            'jev_model': 'jev-latest'}
LEGACY_ECONOMY = {'xhigh': 'lowest_usage', 'high': 'lower_usage',
                  'medium': 'balanced', 'low': 'higher_quality'}


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'jev-codex/config.json'


def data_dir():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'jev-codex'


def state_dir():
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'jev-codex'


def thread_state_path(root, thread_id, suffix):
    if not isinstance(thread_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', thread_id):
        raise ValueError('Provide a Codex thread ID.')
    return Path(root) / f'{thread_id}{suffix}'


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


def load_route_pin(root, thread_id):
    path = thread_state_path(root, thread_id, '.pin')
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {'model', 'effort', 'source'} or
            any(not isinstance(value.get(name), str) or not value[name]
                for name in ('model', 'effort')) or value.get('source') not in ('jev', 'manual', 'existing')):
        raise ValueError(f'Invalid route pin: {path}. Run jev-codex auto on {thread_id} to reset it.')
    return value


def save_route_pin(root, thread_id, model, effort, source):
    if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
        raise ValueError('A route pin needs a model and effort.')
    if source not in ('jev', 'manual', 'existing'):
        raise ValueError('A route pin source must be jev, manual, or existing.')
    private_write(thread_state_path(root, thread_id, '.pin'), {
        'model': model, 'effort': effort, 'source': source})
    thread_state_path(root, thread_id, '.auto').unlink(missing_ok=True)


def clear_route_pin(root, thread_id):
    thread_state_path(root, thread_id, '.pin').unlink(missing_ok=True)


def request_auto_route(root, thread_id):
    clear_route_pin(root, thread_id)
    atomic_write(thread_state_path(root, thread_id, '.auto'), 'select\n')


def auto_route_requested(root, thread_id):
    return thread_state_path(root, thread_id, '.auto').exists()


def validate(values):
    if not isinstance(values, dict):
        raise ValueError('Settings must be an object containing only supported setting names.')
    values = dict(values)
    legacy = values.pop('economy', None)
    if legacy is not None:
        if legacy not in LEGACY_ECONOMY:
            raise ValueError('Legacy economy must be low, medium, high, or xhigh.')
        values.setdefault('routing_preference', LEGACY_ECONOMY[legacy])
    if values.keys() - DEFAULTS.keys():
        raise ValueError('Settings must be an object containing only supported setting names.')
    result = {**DEFAULTS, **values}
    if result['routing_preference'] not in ('lowest_usage', 'lower_usage', 'balanced',
                                             'higher_quality', 'highest_quality'):
        raise ValueError('routing_preference must be lowest_usage, lower_usage, balanced, higher_quality, or highest_quality.')
    if result['routing_mode'] not in ('thread', 'turn'):
        raise ValueError('routing_mode must be thread or turn.')
    if result['maximum_effort'] not in ('automatic', 'high', 'xhigh', 'max'):
        raise ValueError('maximum_effort must be automatic, high, xhigh, or max.')
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
                               len(set(allowed)) != len(allowed) or
                               any(not isinstance(v, str) or not v.strip() for v in allowed)):
        raise ValueError('allow_model must be null or a nonempty array of model names.')
    efforts = result['allow_effort']
    known_efforts = {'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}
    if efforts is not None and (not isinstance(efforts, list) or not efforts or
                                len(set(efforts)) != len(efforts) or set(efforts) - known_efforts):
        raise ValueError('allow_effort must be null or a nonempty array of supported effort names.')
    return result


def load_settings(path=None, overrides=None):
    path = path or config_path()
    values = json.loads(path.read_text()) if path.exists() else {}
    # Validate saved values even when a command-line option would hide the error.
    return validate({**validate(values), **(overrides or {})})


def set_setting(name, value, path=None):
    if name == 'economy':
        if value not in LEGACY_ECONOMY:
            raise ValueError('Legacy economy must be low, medium, high, or xhigh.')
        name, value = 'routing_preference', LEGACY_ECONOMY[value]
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
