"""Small TypeSafe HTTP client shared by the demo and router."""
import json
import math
import os
import shlex
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class JevError(RuntimeError):
    pass


def load_key(path):
    if os.environ.get('JEV_API_KEY'):
        return os.environ['JEV_API_KEY']
    for line in Path(path).read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'export':
            parts.pop(0)
        value = ' '.join(parts)
        name, sep, key = value.partition('=')
        if sep and name.strip() == 'JEV_API_KEY' and key.strip():
            return key.strip()
    raise JevError('Set JEV_API_KEY in the environment or .env file.')


def ask(key, state, questions, model='jev-latest'):
    request = Request('https://api.typesafe.ai/v1/systemone',
                      data=json.dumps(dict(model=model, state=state, questions=questions)).encode(),
                      headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                result = json.load(response)
            if not isinstance(result, dict) or not isinstance(result.get('answers'), dict):
                raise JevError('TypeSafe returned an invalid response.')
            return result
        except HTTPError as error:
            error.close()
            if error.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise JevError(f'TypeSafe returned HTTP {error.code}. No Codex turn was started.') from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise JevError('TypeSafe request failed. No Codex turn was started.') from None


def validate_choice(answer, choices):
    if not isinstance(answer, dict) or answer.get('type') != 'choice':
        raise JevError('Invalid routing answer type.')
    selected = answer.get('choice')
    probabilities = answer.get('probabilities')
    if not isinstance(selected, str) or selected not in choices or not isinstance(probabilities, dict) or set(probabilities) != set(choices):
        raise JevError('TypeSafe returned an unknown route or incomplete probabilities.')
    values = [answer.get('confidence'), *probabilities.values()]
    if any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        raise JevError('TypeSafe returned invalid probabilities.')
    if abs(sum(probabilities.values()) - 1) > 0.03:
        raise JevError('Route probabilities do not sum to one.')
    return selected
