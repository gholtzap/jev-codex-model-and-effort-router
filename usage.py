"""Read account limits and derive an explicit routing preference."""
import math
import time
from datetime import datetime

from appserver import RpcError


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def usage_budget(snapshot, mode, reserve, limit_id='codex', now=None):
    now = time.time() if now is None else now
    buckets = snapshot.get('rateLimitsByLimitId') or {}
    bucket = buckets.get(limit_id)
    if bucket is None:
        candidate = snapshot.get('rateLimits') or {}
        if candidate.get('limitId') == limit_id:
            bucket = candidate
    if not isinstance(bucket, dict):
        raise ValueError(f'No usage data for limit {limit_id}.')
    windows = []
    for name in ('primary', 'secondary'):
        window = bucket.get(name)
        if window is None:
            continue
        used = window.get('usedPercent')
        minutes = window.get('windowDurationMins')
        reset = window.get('resetsAt')
        if not all(finite_number(v) for v in (used, minutes, reset)) or not 0 <= used <= 100 or minutes <= 0:
            raise ValueError('Codex returned invalid or incomplete usage data.')
        seconds = reset - now
        if seconds <= 0:
            raise ValueError('Codex usage data has passed its reset time. Try again shortly.')
        remaining = 100 - used
        usable = max(0, remaining - reserve)
        fraction_left = min(1, seconds / (minutes * 60))
        # This is a pacing rule, not a model price or a forecast of task cost.
        pressure = max(0, min(1, 1 - usable / (100 * fraction_left)))
        windows.append({'window': name, 'remaining_percent': remaining,
                        'window_minutes': minutes, 'resets_at': reset,
                        'daily_allowance_percent': usable / (seconds / 86400),
                        'pressure': pressure})
    if not windows:
        raise ValueError('Codex did not return a usage window.')
    pressure = max(w['pressure'] for w in windows)
    if mode == 'quality':
        pressure = 0
    elif mode == 'conserve':
        pressure = max(0.5, pressure)
    preference = 'normal' if pressure < 0.25 else 'economical' if pressure < 0.65 else 'strongly economical'
    return {'mode': mode, 'limit_id': limit_id, 'reserve_percent': reserve,
            'pressure': pressure, 'preference': preference, 'windows': windows,
            'observed_at': now,
            'usage_blocked': snapshot.get('ordinaryUsageAllowed') is False or
                bucket.get('spendControlReached') is True or bool(bucket.get('rateLimitReachedType'))}


def read_budget(server, mode, reserve, limit_id, required=False):
    try:
        return usage_budget(server.call('account/rateLimits/read', {}), mode, reserve, limit_id)
    except (RpcError, TimeoutError, ValueError, TypeError, AttributeError) as error:
        if required:
            raise RuntimeError(f'Cannot apply usage policy: {error}') from None
        return {'mode': mode, 'unavailable': str(error)}


def show_budget(budget):
    if 'unavailable' in budget:
        print('Usage unavailable: ' + budget['unavailable'], flush=True)
        return
    for window in budget['windows']:
        reset = datetime.fromtimestamp(window['resets_at']).astimezone().strftime('%b %d, %H:%M %Z')
        print(f"Usage ({budget['limit_id']}, {window['window_minutes']:g} min): "
              f"{window['remaining_percent']:g}% left; resets {reset}; "
              f"{window['daily_allowance_percent']:.1f} percentage points/day after reserve.", flush=True)
    print(f"Policy: {budget['mode']}; preference: {budget['preference']}.", flush=True)
