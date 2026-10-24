import logging
log = logging.getLogger(__name__)

def _configured(backend,cfg):
    backend.profile_proxy_cfg=cfg
    return backend

def build(cfg, im):
    if cfg.backend == "sphere":
        from ..backends.sphere import SphereBackend
        from ..net.sphere import LinkenSphere
        ls = LinkenSphere(cfg.sphere_api_port)
        if not ls.ping():
            log.warning("Linken Sphere API auf Port %s nicht erreichbar", cfg.sphere_api_port)
        return _configured(SphereBackend(ls, cfg.sphere_presets),cfg)
    if cfg.engine == "nodriver":
        from ..backends.nodriver_engine import NodriverBackend
        return _configured(NodriverBackend(cfg.profiles_dir),cfg)
    if cfg.engine == "firefox":
        from ..backends.firefox import FirefoxBackend
        return _configured(FirefoxBackend(cfg.profiles_dir, identity_manager=im),cfg)
    from ..backends.local import LocalBackend
    return _configured(LocalBackend(cfg.engine, cfg.profiles_dir),cfg)


def build_identity_manager(cfg):
    """Use the same proxy/account binding for mailbox and account workflows."""
    from . import proxymode
    from .accountproxy import Resolver
    from .identity import IdentityManager
    from .proxy import ProxyPool
    pool = proxymode.make_pool(cfg) if hasattr(proxymode, 'make_pool') else ProxyPool([])
    im = IdentityManager(pool, cfg.profiles_dir)
    if not proxymode.is_anonymous(cfg):
        im.account_proxies = Resolver(cfg.db_path)
    return im
