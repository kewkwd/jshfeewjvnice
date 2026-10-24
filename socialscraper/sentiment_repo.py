"""Speicher der KI-Stimmungseinschätzung: Tabelle `comment_sentiment` (getrennt von `comments`).

Eine Einstufung gehört zu einem Kommentar (`comment_uid` = `comments.uid`) und merkt sich den Hash des eingestuften
Textes. Ändert sich der Text eines Kommentars, passt der Hash nicht mehr: die alte Einstufung zählt dann nicht mehr
(weder in der Verteilung noch als „erledigt“), der Kommentar gilt wieder als offen und wird neu eingestuft.

Alles hier ist eine **KI-Einschätzung** (Schätzung, nicht gemessen). Die Funktionen bekommen eine offene
sqlite3-Verbindung (auch eine schreibgeschützte genügt zum Lesen) und committen beim Schreiben selbst.

Umfang (`scope`): ein Wörterbuch mit beliebigen dieser Schlüssel (fehlende Schlüssel = keine Einschränkung)
    videos      Liste von Video-Schlüsseln (`plattform:video_id`)
    group       Gruppen-Nummer
    platform    'tiktok' | 'instagram' | 'youtube' | 'reddit' | 'x'
    since/until Datum `JJJJ-MM-TT` (Tag des Kommentars, sonst Tag der ersten Sichtung; beide Tage eingeschlossen)
    seen_since  Zeit `JJJJ-MM-TTTHH:MM:SS`: nur Kommentare, die seitdem zum ersten Mal gesehen wurden
    people      Liste von (Plattform, author_key): nur Kommentare dieser Kommentarschreiber
    commenter_group  Gruppen-Nummer: nur Kommentare von Kommentarschreibern, die dieser Gruppe angehören
"""
import datetime
import hashlib

LABELS = ('positiv', 'neutral', 'negativ')
ENGLISH = {'positiv': 'positive', 'neutral': 'neutral', 'negativ': 'negative'}
KIND = 'ki_einschaetzung'
NOTE = ('KI-Einschätzung: eine Schätzung durch ein Sprachmodell, nicht gemessen und keine bewiesene Ursache. '
        'Die Qualität der Einstufung ist nicht geprüft.')

# Tag eines Kommentars: das Datum der Plattform, wenn es wie JJJJ-MM-TT beginnt, sonst der Tag der ersten Sichtung.
DAY_EXPR = ("CASE WHEN c.created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' THEN substr(c.created_at,1,10) "
            "ELSE substr(c.first_seen_at,1,10) END")
_BLANK = "COALESCE(TRIM(c.text, ' ' || char(9) || char(10) || char(13)), '') = ''"
_JOIN = ('comments c LEFT JOIN comment_sentiment s ON s.comment_uid = c.uid AND s.text_hash = ss_th(c.text)')
_CHUNK = 400


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def normalize_text(text):
    """Leerraum vereinheitlichen: gleiche Wörter mit anderen Zeilenumbrüchen sind derselbe Text."""
    return ' '.join(str(text or '').split())


def text_hash(text):
    return hashlib.sha256(normalize_text(text).encode('utf-8')).hexdigest()


def prepare(db):
    """Meldet die SQL-Funktionen `ss_th(text)` (Hash des Kommentartextes) und `ss_ak(author_id, author)` (Personenschlüssel) an."""
    from .people_repo import author_key
    db.create_function('ss_th', 1, text_hash, deterministic=True)
    db.create_function('ss_ak', 2, author_key, deterministic=True)


def has_table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='comment_sentiment'").fetchone() is not None


def _day(value):
    value = (value or '').strip()
    if len(value) >= 10 and value[4] == '-' and value[7] == '-':
        return value[:10]
    raise ValueError('Ein Datum muss so aussehen: 2026-10-31')


def _where(scope):
    scope = scope or {}
    clauses, args = [], []
    videos = scope.get('videos')
    if videos is not None:
        videos = list(dict.fromkeys(videos))
        if not videos:
            clauses.append('0')
        else:
            clauses.append("(c.platform || ':' || c.video_id) IN (%s)" % ','.join('?' for _ in videos))
            args += videos
    if scope.get('group') is not None:
        clauses.append("EXISTS (SELECT 1 FROM group_videos gv WHERE gv.group_id=? "
                       "AND gv.video_uid = c.platform || ':' || c.video_id)")
        args.append(int(scope['group']))
    if scope.get('platform'):
        clauses.append('c.platform=?')
        args.append(scope['platform'])
    if scope.get('since'):
        clauses.append(f'({DAY_EXPR}) >= ?')
        args.append(_day(scope['since']))
    if scope.get('until'):
        clauses.append(f'({DAY_EXPR}) <= ?')
        args.append(_day(scope['until']))
    people = scope.get('people')
    if people is not None:
        people = list(dict.fromkeys(tuple(p) for p in people))
        if not people:
            clauses.append('0')
        else:
            clauses.append('(' + ' OR '.join('(c.platform=? AND ss_ak(c.author_id, c.author)=?)' for _ in people) + ')')
            for platform, key in people:
                args += [platform, key]
    if scope.get('commenter_group') is not None:
        from .people_repo import commenter_member_sql
        clauses.append(commenter_member_sql('c'))
        args.append(int(scope['commenter_group']))
    if scope.get('seen_since'):
        clauses.append("substr(replace(c.first_seen_at,' ','T'),1,19) >= ?")
        args.append(str(scope['seen_since']).replace(' ', 'T')[:19])
    return (' AND '.join(clauses) if clauses else '1'), args


def counts(db, scope=None):
    """{'total', 'analyzable', 'analyzed', 'open'}: Kommentare im Umfang; mit Text; mit gültiger Einstufung; offen."""
    prepare(db)
    where, args = _where(scope)
    if not has_table(db):
        total = db.execute(f'SELECT COUNT(*) FROM comments c WHERE {where}', args).fetchone()[0]
        usable = db.execute(f'SELECT COUNT(*) FROM comments c WHERE {where} AND NOT {_BLANK}', args).fetchone()[0]
        return {'total': total, 'analyzable': usable, 'analyzed': 0, 'open': usable}
    row = db.execute(f'SELECT COUNT(*), COALESCE(SUM(NOT {_BLANK}), 0), '
                     f'COALESCE(SUM(s.comment_uid IS NOT NULL), 0), '
                     f'COALESCE(SUM(NOT {_BLANK} AND s.comment_uid IS NULL), 0) FROM {_JOIN} WHERE {where}', args).fetchone()
    return {'total': row[0], 'analyzable': row[1], 'analyzed': row[2], 'open': row[3]}


def open_comments(db, scope=None, limit=None, redo=False):
    """Kommentare mit Text, die noch keine gültige Einstufung haben: [{'uid', 'text', 'hash'}], in Speicherreihenfolge.

    redo=True: alle Kommentare mit Text (zum Neu-Einstufen); die ohne Einstufung zuerst, dann die ältesten Einstufungen."""
    prepare(db)
    where, args = _where(scope)
    if not has_table(db):
        sql = f'SELECT c.uid, c.text FROM comments c WHERE {where} AND NOT {_BLANK} ORDER BY c.rowid'
    elif redo:
        sql = (f"SELECT c.uid, c.text FROM {_JOIN} WHERE {where} AND NOT {_BLANK} "
               f"ORDER BY COALESCE((SELECT x.analyzed_at FROM comment_sentiment x WHERE x.comment_uid=c.uid), ''), c.rowid")
    else:
        sql = f'SELECT c.uid, c.text FROM {_JOIN} WHERE {where} AND NOT {_BLANK} AND s.comment_uid IS NULL ORDER BY c.rowid'
    if limit is not None:
        sql += ' LIMIT ?'
        args = [*args, int(limit)]
    return [{'uid': uid, 'text': text, 'hash': text_hash(text)} for uid, text in db.execute(sql, args)]


def save_results(db, rows, model, prompt_version, when=None):
    """Speichert Einstufungen idempotent. rows: [(comment_uid, text_hash, label, score)]. Gibt die Anzahl zurück."""
    when = when or now_iso()
    data = []
    for uid, digest, label, score in rows:
        if label not in LABELS:
            raise ValueError('Unbekannte Stimmung')
        score = max(-1.0, min(1.0, float(score)))
        data.append((uid, label, score, model, int(prompt_version), when, digest))
    with db:
        db.executemany(
            'INSERT INTO comment_sentiment (comment_uid, label, score, model, prompt_version, analyzed_at, text_hash) '
            'VALUES (?,?,?,?,?,?,?) ON CONFLICT(comment_uid) DO UPDATE SET label=excluded.label, score=excluded.score, '
            'model=excluded.model, prompt_version=excluded.prompt_version, analyzed_at=excluded.analyzed_at, '
            'text_hash=excluded.text_hash', data)
    return len(data)


def reset(db, scope=None):
    """Löscht die Einstufungen im Umfang (die Kommentare bleiben): so lassen sie sich neu bestimmen. Gibt die Anzahl zurück."""
    prepare(db)   # ss_ak / ss_th für die Bereiche „Personen“ und „Kommentarschreiber-Gruppe“
    where, args = _where(scope)
    with db:
        cur = db.execute(f'DELETE FROM comment_sentiment WHERE comment_uid IN (SELECT c.uid FROM comments c WHERE {where})', args)
    return cur.rowcount


def _empty():
    return {'positiv': 0, 'neutral': 0, 'negativ': 0}


def _share(counts_, analyzed):
    if not analyzed:
        return {'positive': None, 'neutral': None, 'negative': None}
    return {ENGLISH[k]: round(counts_[k] / analyzed, 4) for k in LABELS}


def distribution(db, scope=None):
    """Verteilung im Umfang. Zahlen mit englischen Schlüsseln (für die Schnittstelle), Anteile 0 bis 1."""
    prepare(db)
    total = counts(db, scope)
    got, models, versions, score_sum = _empty(), {}, set(), 0.0
    if has_table(db):
        where, args = _where(scope)
        for label, n, score_total, model, version in db.execute(
                f'SELECT s.label, COUNT(*), COALESCE(SUM(s.score), 0), s.model, s.prompt_version FROM {_JOIN} '
                f'WHERE {where} AND s.comment_uid IS NOT NULL GROUP BY s.label, s.model, s.prompt_version', args):
            got[label] += n
            score_sum += score_total
            models[model or '?'] = models.get(model or '?', 0) + n
            versions.add(version)
    analyzed = sum(got.values())
    return {'kind': KIND, 'positive': got['positiv'], 'neutral': got['neutral'], 'negative': got['negativ'],
            'analyzed': analyzed, 'total_comments': total['total'], 'open': total['open'],
            'coverage': round(analyzed / total['total'], 4) if total['total'] else None,
            'share': _share(got, analyzed), 'score_avg': round(score_sum / analyzed, 4) if analyzed else None,
            'model': max(models, key=models.get) if models else None, 'models': models,
            'prompt_versions': sorted(v for v in versions if v is not None)}


def series(db, scope=None, interval='day'):
    """Verlauf: eine Zeile je Tag (oder Woche, Montag) mit Zahlen, Anteilen und mittlerem Wert, nach Datum sortiert."""
    if interval not in ('day', 'week'):
        raise ValueError('interval muss day oder week sein')
    prepare(db)
    if not has_table(db):
        return []
    where, args = _where(scope)
    buckets = {}
    for day, label, n, score_total in db.execute(
            f'SELECT {DAY_EXPR}, s.label, COUNT(*), COALESCE(SUM(s.score), 0) FROM {_JOIN} '
            f'WHERE {where} AND s.comment_uid IS NOT NULL GROUP BY 1, 2', args):
        if not day or len(day) != 10:
            continue
        if interval == 'week':
            try:
                moment = datetime.date.fromisoformat(day)
            except ValueError:
                continue
            day = (moment - datetime.timedelta(days=moment.weekday())).isoformat()
        b = buckets.setdefault(day, {'counts': _empty(), 'score': 0.0})
        b['counts'][label] += n
        b['score'] += score_total
    out = []
    for day in sorted(buckets):
        c = buckets[day]['counts']
        analyzed = sum(c.values())
        out.append({'date': day, 'positive': c['positiv'], 'neutral': c['neutral'], 'negative': c['negativ'],
                    'analyzed': analyzed, 'share': _share(c, analyzed),
                    'score_avg': round(buckets[day]['score'] / analyzed, 4) if analyzed else None})
    return out


def group_distributions(db):
    """{Gruppen-Nummer: {'positiv', 'neutral', 'negativ'}} über alle Gruppen (ein Video kann in mehreren Gruppen sein)."""
    prepare(db)
    out = {}
    if not has_table(db):
        return out
    for gid, label, n in db.execute(
            "SELECT gv.group_id, s.label, COUNT(*) FROM group_videos gv "
            "JOIN comments c ON gv.video_uid = c.platform || ':' || c.video_id "
            "JOIN comment_sentiment s ON s.comment_uid = c.uid AND s.text_hash = ss_th(c.text) GROUP BY gv.group_id, s.label"):
        out.setdefault(gid, _empty())[label] = n
    return out


def labels_for(db, comments):
    """Gültige Einstufungen zu Kommentaren [{'uid', 'text'}]: {uid: {'label', 'score', 'model', 'analyzed_at', 'prompt_version'}}."""
    if not comments or not has_table(db):
        return {}
    texts = {c['uid']: c.get('text') for c in comments}
    uids = list(texts)
    out = {}
    for start in range(0, len(uids), _CHUNK):
        chunk = uids[start:start + _CHUNK]
        marks = ','.join('?' for _ in chunk)
        for uid, label, score, model, version, when, digest in db.execute(
                f'SELECT comment_uid, label, score, model, prompt_version, analyzed_at, text_hash FROM comment_sentiment '
                f'WHERE comment_uid IN ({marks})', chunk):
            if digest == text_hash(texts[uid]):
                out[uid] = {'label': label, 'score': score, 'model': model, 'prompt_version': version, 'analyzed_at': when}
    return out
