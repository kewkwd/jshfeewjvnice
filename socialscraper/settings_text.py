"""Pure TOML editing and value validation, separate from settings file I/O."""
import re

EDITABLE = {
    'max_comments': int, 'headless': bool, 'require_auth': bool, 'snapshot': bool,
    'max_retries': int, 'request_timeout': int, 'session_max_age': 'floatpos', 'retry_backoff': 'floatpos',
    'rotation_every': int, 'length_short_s': int, 'length_long_s': int,
    'log_level': 'str', 'engine': 'str', 'notify_file': 'str', 'notify_webhook': 'str',
    'proxies': 'strlist', 'proxy_mode': 'mode',
}

_NONEMPTY = {'notify_file', 'log_level', 'engine'}

def _toml_string(text):
    out = []
    for ch in text:
        if ch == '\\': out.append('\\\\')
        elif ch == '"': out.append('\\"')
        elif ch == '\n': out.append('\\n')
        elif ch == '\r': out.append('\\r')
        elif ch == '\t': out.append('\\t')
        elif ord(ch) < 0x20 or ord(ch) in (0x7f, 0x85, 0x2028, 0x2029): out.append('\\u%04x' % ord(ch))
        else: out.append(ch)
    return '"' + ''.join(out) + '"'

def _toml_value(value):
    if value is True: return 'true'
    if value is False: return 'false'
    if isinstance(value, str): return _toml_string(value)
    if isinstance(value, (list, tuple)): return '[' + ', '.join(_toml_value(v) for v in value) + ']'
    if isinstance(value, float): return repr(value)
    return str(int(value))

def _check_setting(key, value):
    kind = EDITABLE[key]
    if kind is int and (type(value) is not int or value < 0):
        raise ValueError(f'{key} muss eine ganze Zahl ab 0 sein')
    if kind is bool and type(value) is not bool:
        raise ValueError(f'{key} muss Ja oder Nein sein')
    if kind == 'floatpos':
        import math
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{key} muss eine Zahl größer 0 sein')
    if kind == 'str' and (not isinstance(value, str) or (key in _NONEMPTY and not value.strip())):
        raise ValueError(f'{key} muss Text sein' + (' und darf nicht leer sein' if key in _NONEMPTY else ''))
    if kind == 'mode':
        from .config import PROXY_MODES
        if not isinstance(value, str) or value not in PROXY_MODES:
            raise ValueError('proxy_mode muss aus, liste, frei oder tor sein')
    if kind == 'strlist' and (not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value)):
        raise ValueError(f'{key} muss eine Liste von Texten sein')

def _value_end(lines, i, col):
    """Ende des TOML-Werts, der in lines[i] ab col beginnt: (Zeile, Spalte hinter dem Wert)."""
    line = lines[i]
    while col < len(line) and line[col] in ' \t':
        col += 1
    if line.startswith(('"""', "'''"), col):
        delim, pos, j = line[col:col + 3], col + 3, i
        while True:
            text = lines[j]
            while True:
                hit = text.find(delim, pos)
                if hit < 0: break
                if delim == '"""' and text[:hit].endswith('\\') and not text[:hit].endswith('\\\\'):
                    pos = hit + 1; continue
                return j, hit + 3
            j += 1; pos = 0
            if j >= len(lines): return i, len(line)
    depth, j, pos, started = 0, i, col, False
    while j < len(lines):
        text = lines[j]
        while pos < len(text):
            ch = text[pos]
            if ch in '"\'':
                end = pos + 1
                while end < len(text) and text[end] != ch:
                    end += 2 if (ch == '"' and text[end] == '\\') else 1
                pos = end + 1; started = True; continue
            if ch == '#':
                pos = len(text); break
            if ch in '[{':
                depth += 1; started = True
            elif ch in ']}':
                depth -= 1
                if depth <= 0: return j, pos + 1
            elif not started and ch in ' \t':
                pass
            elif not started:
                started = True
            elif depth == 0 and ch in ' \t':
                return j, pos
            pos += 1
        if depth == 0:
            return j, len(text.rstrip())
        j += 1; pos = 0
    return i, len(line)

_KEY_LINE = re.compile(r'^\s*(?:"[^"]*"|\x27[^\x27]*\x27|[A-Za-z0-9_.\-]+)\s*=\s*')

def _first_table_line(lines):
    '''Index der ersten echten Tabellenzeile `[name]` (nicht in mehrzeiligen Texten oder Listen), sonst len(lines).'''
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith('['):
            return i
        m = _KEY_LINE.match(lines[i])
        if m:
            i = _value_end(lines, i, m.end())[0]
        i += 1
    return len(lines)

def render_settings(raw, values):
    """Setzt `values` in den Text `raw` (nur Kopfteil vor der ersten [Tabelle]); gibt den neuen Text zurück.
    Kommentare, Reihenfolge und Zeilenenden bleiben; ein Kommentar hinter einem Wert bleibt stehen."""
    newline = '\r\n' if '\r\n' in raw else '\n'
    # Nur echte Zeilenumbrüche trennen (nicht U+2028/U+2029/U+0085, die str.splitlines() auch trennt)
    lines = re.split(r'\r?\n', raw)
    if lines and lines[-1] == '':
        lines.pop()
    first_table = _first_table_line(lines)
    done = set()
    i = 0
    while i < min(first_table, len(lines)):
        m = re.match(r'^(\s*)([A-Za-z_]\w*)(\s*=\s*)', lines[i])
        if m:
            end_line, end_col = _value_end(lines, i, m.end())
            if m.group(2) in values and m.group(2) not in done:
                tail = lines[end_line][end_col:]
                lines[i:end_line + 1] = [f'{m.group(1)}{m.group(2)} = {_toml_value(values[m.group(2)])}{tail}']
                first_table -= end_line - i
                done.add(m.group(2))
            else:
                i = end_line   # den Wert (auch mehrzeilig) überspringen: Inhalt zählt nicht als Schlüssel
        i += 1
    missing = [k for k in values if k not in done]
    if missing:
        last = max((j for j in range(min(first_table, len(lines))) if lines[j].strip()), default=-1)
        add = [f'{k} = {_toml_value(values[k])}' for k in missing]
        lines[last + 1:last + 1] = add
        if first_table < len(lines) and last + 1 == first_table:
            lines.insert(last + 1 + len(add), '')
    return newline.join(lines) + newline
