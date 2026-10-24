"""Zwischenablage-Hinweis im Tab „Abrufen“ (ohne Tk): erkennt, ob die Zwischenablage nur aus Links besteht.

Es wird nichts gespeichert und nichts weitergegeben: der Text wird nur geprüft, im Speicher gehalten, solange der Hinweis steht,
und danach verworfen. Gemerkt wird höchstens ein Prüfwert (CRC), damit ein weggeklickter Hinweis nicht sofort wiederkommt.
"""
import re
import zlib

MAX_CHARS = 20_000
MAX_LINKS = 25
_URL = re.compile(r'https?://[^\s<>"\'|]+', re.I)

NAMES = {'tiktok': 'TikTok', 'instagram': 'Instagram', 'youtube': 'YouTube', 'reddit': 'Reddit', 'x': 'X'}


def fingerprint(text):
    return zlib.crc32((text or '').encode('utf-8', 'ignore'))


def split_links(text):
    """Alle Web-Adressen in einem Text (in Reihenfolge, ohne Doppelte, ohne Satzzeichen am Ende)."""
    out = []
    for match in _URL.findall((text or '')[:MAX_CHARS]):
        url = match.rstrip('.,;:!?)]}»›')
        if url and url not in out:
            out.append(url)
    return out[:MAX_LINKS]


def inspect(text):
    """Was steckt in der Zwischenablage? Gibt None zurück, wenn es nicht nur aus erkannten Links besteht, sonst
    {'kind': 'video' | 'channel', 'links': [...], 'platforms': [...], 'text': Satz für den Hinweis}.

    Streng: Steht neben den Links anderer Text, wird nichts angeboten (kein Raten, kein Mitlesen)."""
    from . import channels, guicore
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
        return None
    lines = [l.strip() for l in re.split(r'[\r\n]+', text) if l.strip()]
    if not lines or len(lines) > MAX_LINKS:
        return None
    links, kinds, platforms = [], set(), []
    for line in lines:
        if not re.fullmatch(r'https?://\S+', line):
            return None
        platform = guicore.detect_platform(line)
        if platform is None:
            return None
        try:
            channels.parse_channel_link(line)
            kind = 'channel'
        except ValueError:
            kind = 'video'
        links.append(line)
        kinds.add(kind)
        if platform not in platforms:
            platforms.append(platform)
    if len(kinds) != 1:
        return None
    kind = kinds.pop()
    names = ' und '.join(NAMES[p] for p in platforms) if len(platforms) <= 2 else 'verschiedenen Plattformen'
    if kind == 'channel':
        if len(links) != 1:
            return None
        text_out = f'In der Zwischenablage ist ein {NAMES[platforms[0]]}-Kanal-Link.'
    elif len(links) == 1:
        text_out = f'In der Zwischenablage ist ein {NAMES[platforms[0]]}-Link.'
    else:
        text_out = f'In der Zwischenablage sind {len(links)} Links ({names}).'
    return {'kind': kind, 'links': links, 'platforms': platforms, 'text': text_out}


def merge_into(existing_text, links):
    """Links an den vorhandenen Text anhängen: je Link eine Zeile, schon vorhandene nicht noch einmal. Gibt den neuen Text zurück."""
    have = {l.strip() for l in (existing_text or '').splitlines()}
    new = [l for l in links if l not in have]
    if not new:
        return existing_text or ''
    base = (existing_text or '').rstrip('\n')
    return (base + '\n' if base.strip() else '') + '\n'.join(new)


def all_present(existing_text, links):
    have = {l.strip() for l in (existing_text or '').splitlines()}
    return all(l in have for l in links)
