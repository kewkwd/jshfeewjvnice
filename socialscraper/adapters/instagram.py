import logging, re, time
from functools import wraps
from urllib.parse import urlsplit
from .base import Adapter, checkpoint
from ..fetch_result import FetchResult, comments_failure, received_comments
from ..models import Video, Comment, iso
from ..net.retry import PermanentFetchError, with_retry
from ..net.proxymode import ytdlp_proxy_url as remote_proxy_url
from ..util import comment_id, comment_limit, matches_host, parse_short_number

log = logging.getLogger(__name__)

def _apply_proxy(context, proxy):
    """Keep this context's route on anonymous and copied library sessions."""
    from requests.exceptions import RequestException
    from ..net.errors import wrap
    route = remote_proxy_url(proxy)

    def bind(session):
        session.proxies = {"http": route, "https": route}
        # Requests otherwise prefers environment proxies over Session.proxies.
        session.trust_env = False
        return session

    bind(context._session)
    originals = getattr(context, '_socialscraper_transport', None)
    if originals is None:
        originals = context.get_json, context.get_anonymous_session
        context._socialscraper_transport = originals
    get_json, anonymous_session = originals

    @wraps(get_json)
    def routed_json(path, params, host='www.instagram.com', session=None, *args, **kwargs):
        bind(context._session)
        if session is not None:
            bind(session)
        try:
            return get_json(path, params, host, session, *args, **kwargs)
        except Exception as error:
            # test_login swallows this library exception and returns None.
            # A transport failure must not expire a still valid account.
            cause = error
            seen = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, RequestException):
                    raise wrap(cause) from error
                cause = cause.__cause__
            raise

    @wraps(anonymous_session)
    def routed_anonymous():
        return bind(anonymous_session())

    context.get_json = routed_json
    context.get_anonymous_session = routed_anonymous

class InstagramAdapter(Adapter):
    platform = "instagram"
    domains = ("instagram.com",)
    needs_auth = True

    @staticmethod
    def shortcode(url):
        if not matches_host(url, "instagram.com"):
            raise ValueError("Ungültige Instagram-URL")
        parts = [p for p in urlsplit(url).path.split("/") if p]
        if len(parts) != 2 or parts[0] not in ("p", "reel", "reels", "tv"):
            raise ValueError("Instagram-URL muss einen Beitrag oder ein Reel bezeichnen")
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]):
            raise ValueError("Ungültiger Instagram-Shortcode")
        return parts[1]

    @with_retry(times=3, backoff=3.0)
    def fetch(self, url, ctx):
        cfg = ctx.cfg
        limit = comment_limit(cfg)
        sc = self.shortcode(url)
        import instaloader
        from ..net.cookies import CookieJar
        from ..net.session_guard import SessionExpired

        ctx.authenticated = False
        from ..net import proxymode
        proxymode.assert_fetch_allowed(cfg, "instagram")  # kostenlose Proxys/Tor: Instagram braucht immer ein Konto
        L = instaloader.Instaloader(quiet=True, download_videos=False,
                                    save_metadata=False, max_connection_attempts=3)
        try:
            proxy = ctx.im.proxy_for(ctx.im.get(ctx.identity, ctx.country, ctx.mobile)) if ctx.im else None

            def apply_proxy():
                if proxy:
                    _apply_proxy(L.context, proxy)

            def check_user(user):
                if user and cfg.ig_session_user and user.casefold() != cfg.ig_session_user.casefold():
                    raise SessionExpired("Instagram-Session gehört zu einem anderen Benutzer")

            login_user = None
            if cfg.ig_session_user:
                try:
                    L.load_session_from_file(cfg.ig_session_user, cfg.ig_session_file)
                except FileNotFoundError:
                    pass
                else:
                    apply_proxy()
                    login_user = L.test_login()
                    check_user(login_user)

            if not login_user:
                cookies = CookieJar.load(ctx.identity, "instagram")
                if not CookieJar.authenticated(cookies, "instagram"):
                    raise SessionExpired("Keine bestätigte Instagram-Session; erneut anmelden")
                session_data = {cookie["name"]: cookie["value"] for cookie in cookies}
                if not session_data.get("csrftoken"):
                    raise SessionExpired("Instagram-Cookies enthalten keinen CSRF-Token; erneut anmelden")
                # Public API, available since Instaloader 4.10. The username is
                # confirmed below before the context is used to read any post.
                L.load_session(cfg.ig_session_user or "", session_data)
                apply_proxy()
                login_user = L.test_login()
                check_user(login_user)
            if not login_user:
                raise SessionExpired("Instagram-Anmeldung konnte nicht bestätigt werden; Session erneuern")
            L.context.username = login_user
            ctx.authenticated = True
            try:
                return self._read_post(instaloader.Post.from_shortcode(L.context, sc), url, limit, ctx)
            except (instaloader.exceptions.QueryReturnedNotFoundException,
                    instaloader.exceptions.PrivateProfileNotFollowedException,
                    instaloader.exceptions.ProfileNotExistsException) as error:
                raise PermanentFetchError(f"Instagram-Beitrag nicht abrufbar ({type(error).__name__})") from None
            except instaloader.exceptions.LoginRequiredException:
                raise SessionExpired("Instagram verlangt eine erneute Anmeldung") from None
        finally:
            try:
                L.close()
            except Exception:
                log.warning("Instagram-Session konnte nicht sauber geschlossen werden")

    def _login(self, L, ctx):
        """Dieselbe Anmeldung wie in `fetch` (gespeicherte Sitzung oder Cookies des Kontos, Benutzer wird bestätigt)."""
        from ..net.cookies import CookieJar
        from ..net.session_guard import SessionExpired
        cfg = ctx.cfg
        proxy = ctx.im.proxy_for(ctx.im.get(ctx.identity, ctx.country, ctx.mobile)) if ctx.im else None

        def apply_proxy():
            if proxy:
                _apply_proxy(L.context, proxy)

        def check_user(user):
            if user and cfg.ig_session_user and user.casefold() != cfg.ig_session_user.casefold():
                raise SessionExpired("Instagram-Session gehört zu einem anderen Benutzer")

        login_user = None
        if cfg.ig_session_user:
            try:
                L.load_session_from_file(cfg.ig_session_user, cfg.ig_session_file)
            except FileNotFoundError:
                pass
            else:
                apply_proxy()
                login_user = L.test_login()
                check_user(login_user)
        if not login_user:
            cookies = CookieJar.load(ctx.identity, "instagram")
            if not CookieJar.authenticated(cookies, "instagram"):
                raise SessionExpired("Keine bestätigte Instagram-Session; erneut anmelden")
            session_data = {cookie["name"]: cookie["value"] for cookie in cookies}
            if not session_data.get("csrftoken"):
                raise SessionExpired("Instagram-Cookies enthalten keinen CSRF-Token; erneut anmelden")
            L.load_session(cfg.ig_session_user or "", session_data)
            apply_proxy()
            login_user = L.test_login()
            check_user(login_user)
        if not login_user:
            raise SessionExpired("Instagram-Anmeldung konnte nicht bestätigt werden; Session erneuern")
        L.context.username = login_user
        ctx.authenticated = True

    def channel_items(self, value, ctx, opts):
        """Videos (Reels und Videobeiträge) eines Profils über instaloader, neueste zuerst, seitenweise mit Pausen.
        Beiträge ohne Video werden gezählt, aber nicht aufgenommen. Bei „zu viele Anfragen“ endet der Aufruf; was bis dahin
        gefunden wurde, ist schon gespeichert."""
        from .. import channel_list as cl
        import instaloader
        from ..net import proxymode
        from ..net.session_guard import SessionExpired
        name = str(value).lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", name):
            raise ValueError("Ungültiger Instagram-Benutzername")
        proxymode.assert_fetch_allowed(ctx.cfg, "instagram")
        L = instaloader.Instaloader(quiet=True, download_videos=False, save_metadata=False, max_connection_attempts=3)
        collector, result = cl.Collector(opts), cl.ListResult()
        scanned = 0
        try:
            self._login(L, ctx)
            try:
                profile = instaloader.Profile.from_username(L.context, name)
                result.platform_total = int(profile.mediacount) if profile.mediacount else None
                result.name = getattr(profile, "full_name", None) or name
                if getattr(profile, "is_private", False) and not getattr(profile, "followed_by_viewer", True):
                    raise PermanentFetchError("Dieses Instagram-Profil ist privat und du folgst ihm nicht.")
                posts = iter(profile.get_posts())
                while True:
                    if opts.stop():
                        collector.done = True
                        break
                    try:
                        post = next(posts)
                    except StopIteration:
                        result.exhausted = True
                        break
                    scanned += 1
                    if scanned % cl.PAGE == 0:
                        opts.pause(1.5, 4.0)
                    if not post.is_video:
                        continue
                    shortcode = post.shortcode
                    kind = "reel" if getattr(post, "typename", "") == "GraphVideo" else "p"
                    if not collector.add(cl.item(f"https://www.instagram.com/{kind}/{shortcode}/", f"instagram:{shortcode}",
                                                 (post.caption or "")[:120] or None, cl.to_iso(post.date_utc),
                                                 post.video_duration, "instagram"), str(scanned)):
                        break
            except instaloader.exceptions.TooManyRequestsException as error:
                collector.flush()
                raise RuntimeError("Instagram: zu viele Anfragen (HTTP 429, too many requests); die Liste wird später fortgesetzt") from error
            except instaloader.exceptions.LoginRequiredException:
                collector.flush()
                raise SessionExpired("Instagram verlangt eine erneute Anmeldung") from None
            except (instaloader.exceptions.ProfileNotExistsException, instaloader.exceptions.PrivateProfileNotFollowedException) as error:
                raise PermanentFetchError(f"Instagram-Profil nicht abrufbar ({type(error).__name__})") from None
        finally:
            try:
                L.close()
            except Exception:
                log.warning("Instagram-Session konnte nicht sauber geschlossen werden")
        collector.flush()
        result.found = collector.count
        result.reached_known = collector.reached_known
        if collector.hit_since:
            result.exhausted = True
        if result.exhausted:
            result.note = f"{scanned} Beiträge geprüft, davon {collector.count} Videos."
        return result

    def _read_post(self, p, url, limit, ctx=None):
        v = Video.from_dict({
            "platform": "instagram", "video_id": p.shortcode,
            "url": urlsplit(url)._replace(query="", fragment="").geturl(),
            "author": p.owner_username, "author_id": str(p.owner_id),
            "caption": p.caption, "created_at": iso(p.date_utc),
            "duration_s": p.video_duration if p.is_video else None,
            "view_count": parse_short_number(p.video_view_count) if p.is_video else None,
            "like_count": parse_short_number(p.likes), "comment_count": parse_short_number(p.comments),
            "hashtags": list(p.caption_hashtags or []),
            "mentions": list(p.caption_mentions or []),
            "thumbnail": p.url,
            "raw": {"typename": p.typename, "is_video": p.is_video,
                    "is_sponsored": p.is_sponsored},
        })
        checkpoint(ctx, v, [])

        comments, seen = [], set()
        if not limit:
            return FetchResult(v, [], "not_requested", "Nur Videodaten angefordert.")
        if v.comment_count == 0:
            return FetchResult(v, [], "empty", "Instagram meldet keine Kommentare.")

        def failed(error):
            if type(error).__name__ == "LoginRequiredException":
                from ..net.session_guard import SessionExpired, invalidate
                if ctx is not None:
                    ctx.authenticated = False
                    invalidate(ctx.identity, "instagram")
                error = SessionExpired("Instagram verlangt eine erneute Anmeldung. Danach den Abruf wiederholen.")
            # A few replies can still be pending since the last persistence batch.
            if comments:
                checkpoint(ctx, v, comments)
            return comments_failure(v, comments, error)

        def add_comment(c, parent=None):
            cid = comment_id(p.shortcode, str(c.owner.id), c.text, c.id)
            if cid in seen:
                return
            seen.add(cid)
            comments.append(Comment.from_dict({
                "platform": "instagram", "video_id": p.shortcode,
                "comment_id": cid, "text": c.text or "",
                "author": c.owner.username, "author_id": str(c.owner.id),
                "created_at": iso(c.created_at_utc),
                "like_count": parse_short_number(c.likes_count), "parent_id": parent,
            }))

        try:
            parents = iter(p.get_comments())
        except Exception as error:
            return failed(error)
        while len(comments) < limit:
            before = len(comments)
            try:
                c = next(parents)
                add_comment(c)
            except StopIteration:
                break
            except Exception as error:
                return failed(error)
            if len(comments) > before:
                checkpoint(ctx, v, comments)
            if len(comments) >= limit:
                break
            try:
                answers = iter(c.answers)
            except Exception as error:
                return failed(error)
            saved_count = len(comments)
            while len(comments) < limit:
                try:
                    a = next(answers)
                    add_comment(a, str(c.id))
                except StopIteration:
                    break
                except Exception as error:
                    return failed(error)
                if len(comments) - saved_count >= 10:
                    checkpoint(ctx, v, comments)
                    saved_count = len(comments)
                if len(comments) >= limit:
                    break
            if len(comments) > saved_count:
                checkpoint(ctx, v, comments)
            if len(comments) >= limit:
                break
            time.sleep(1.5)
        log.info("Instagram %s: %s Kommentare", p.shortcode, len(comments))
        if not comments:
            return FetchResult(v, [], "unavailable", "Instagram hat keine Kommentare geliefert. "
                               "Das belegt keine leere Kommentarliste. Anmeldung prüfen und erneut versuchen.")
        return received_comments(v, comments, limit)
