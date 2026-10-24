"""Keep failed browser storage; start the next attempt in a separate empty directory."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
from ..net.identity import validate_name, _write


def _read(path):
    try:
        data = json.loads(path.read_text(encoding='utf8'))
    except FileNotFoundError:
        return {'version': 1, 'generation': '', 'status': 'clean'}
    except (OSError, ValueError):
        raise ValueError('Der Browser-Profilstatus ist nicht lesbar. Kein alter Profilordner gestartet.') from None
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('generation'), str)
            or not re.fullmatch(r'(?:[a-f0-9]{16})?', data['generation'])
            or data.get('status') not in ('clean', 'in_use', 'failed')):
        raise ValueError('Der Browser-Profilstatus ist beschädigt. Kein alter Profilordner gestartet.')
    return data


@dataclass
class ProfileSelection:
    path: Path
    state: Path
    generation: str
    failed: bool = False
    closed: bool = False

    @property
    def isolated(self):
        return bool(self.generation)

    def finish(self, *, success):
        if self.closed:return
        self.failed = self.failed or not success
        data = _read(self.state)
        # An older handle cannot mark a newer attempt clean or failed.
        if data['generation'] == self.generation:
            data['status'] = 'failed' if self.failed else 'clean'
            _write(self.state, data)
        self.closed = True


def begin(root, name, platform=None, *, firefox=False):
    root = Path(root)
    validate_name(name)
    if platform is not None:validate_name(platform)
    engine = 'firefox' if firefox else 'chromium'
    slot = platform or '_general'
    state = root / '_profile_status' / engine / name / (slot + '.json')
    data = _read(state)
    if data['status'] in ('in_use', 'failed'):
        previous = data['generation']
        data['generation'] = secrets.token_hex(8)
        while data['generation'] == previous:
            data['generation'] = secrets.token_hex(8)
    if data['generation']:
        path = root / '_retries' / engine / name / slot / data['generation']
    elif firefox:
        path = root / '_firefox' / name / slot
    else:
        path = root / name
        if platform:path /= platform
    state.parent.mkdir(parents=True, exist_ok=True)
    data['status'] = 'in_use'
    _write(state, data)  # Failure to persist must stop before launching a browser.
    return ProfileSelection(path, state, data['generation'])
