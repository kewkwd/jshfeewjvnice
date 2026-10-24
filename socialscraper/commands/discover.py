"""Befehl `discover`: neue Video-Links zu Hashtag, Suchbegriff oder Profil finden (nichts wird gespeichert)."""
import contextlib
import logging

NAME = 'discover'
HELP = 'Video-Links zu einem Hashtag, Suchbegriff oder Profil auflisten (ohne zu speichern)'


def add_arguments(p):
    p.add_argument('kind', choices=['hashtag', 'search', 'profile'])
    p.add_argument('value')
    p.add_argument('--platform', choices=['youtube', 'tiktok', 'instagram', 'reddit', 'x'], default='youtube')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--identity', default=None, help='Identität (Standard: youtube_main bzw. tiktok_main)')
    p.add_argument('--country', default='de')


def run(args, cfg):
    from .. import cli, discovery, runlock
    from ..adapters.base import ScrapeContext
    from ..net.session_guard import SessionExpired
    from ..store import Store
    from ..repo import normalize_target_value
    if args.limit < 1:
        raise ValueError('--limit muss mindestens 1 sein')
    if args.platform == 'instagram':
        raise discovery.DiscoveryError('Bei Instagram kann Socialscraper keine Videos suchen. '
                                       'Bitte einzelne Video-Links verwenden.')
    value = normalize_target_value(args.kind, args.value, args.platform)
    identity = args.identity or ('youtube_main' if args.platform == 'youtube' else f'{args.platform}_main')
    from ..net import proxymode
    if proxymode.is_anonymous(cfg):
        proxymode.assert_fetch_allowed(cfg, args.platform, 0, args.kind)
        identity, args.country = proxymode.anon_run(cfg)
    im, backend, sessions = cli.make_runtime(cfg, args.platform in ('tiktok', 'reddit', 'x'))
    try:
        with runlock.acquire(runlock.root_for_db(cfg.db_path), 'cli') if backend is not None else contextlib.nullcontext():
            ctx = ScrapeContext(cfg=cfg, store=None, im=im, sessions=sessions, identity=identity,
                                country=args.country.lower(), mobile=False, authenticated=False,
                                require_auth=cfg.require_auth)
            if args.platform in ('tiktok', 'x') and not proxymode.is_anonymous(cfg):
                from ..adapters import ALL
                adapter = next(a for a in ALL if a.platform == args.platform)
                try:
                    ctx.authenticated, _ = cli.auth_state(adapter, cfg, identity, ctx.country, False, backend, im)
                except SessionExpired as error:
                    logging.getLogger('discover').error('%s', error)
                    return 3
            urls = discovery.discover(args.kind, args.platform, value, ctx, args.limit)
    finally:
        cli.shutdown_runtime(backend, sessions)
    for url in urls:
        print(url)
    if not urls:
        logging.getLogger('discover').warning('Keine Videos gefunden')
    return 0

