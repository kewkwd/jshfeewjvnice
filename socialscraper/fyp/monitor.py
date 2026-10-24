"""Neue Videos der Influencer/Kanäle erkennen (ersetzt die manuelle Freunde-Gruppe)."""
import logging, re

from . import ensure_schema

log = logging.getLogger(__name__)
_VIDEO_UID = re.compile(r'/video/(\d+)')


def neue_videos(cfg, kanaele, *, limit=30, melden=log.info, stop=None):
    """Holt je Kanal das Profil (Profil-URLs) und legt unbekannte Video-Kennungen ab."""
    from .. import cli, repo
    from ..adapters.tiktok import TikTokAdapter
    from ..adapters.base import ScrapeContext
    from ..store import Store
    neu = []
    with Store(cfg.db_path) as store:
        ensure_schema(store.db)
        im, backend, sessions = cli.make_runtime(cfg, True)
        try:
            adapter = TikTokAdapter()
            ctx = ScrapeContext(cfg=cfg, store=store, im=im, sessions=sessions, identity='default', country='de')
            for kanal in kanaele:
                if stop is not None and stop():
                    break
                handle = kanal.lstrip('@')
                try:
                    urls = adapter.profile_urls(handle, ctx, limit)
                except Exception as fehler:
                    log.debug('Kanal %s nicht abrufbar: %s', kanal, fehler)
                    continue
                for url in urls:
                    treffer = _VIDEO_UID.search(str(url))
                    if not treffer:
                        continue
                    uid = treffer.group(1)
                    eingefuegt = store.db.execute('INSERT OR IGNORE INTO fyp_videos (kanal, uid, autor, erstellt_am) '
                                                 'VALUES (?,?,?,?)', (handle, uid, handle, repo.iso(repo.now())))
                    if eingefuegt.rowcount:
                        neu.append(uid)
                store.db.commit()
        finally:
            cli.shutdown_runtime(backend, sessions)
    if neu:
        melden(f'{len(neu)} neue Videos erkannt.')
    return neu
