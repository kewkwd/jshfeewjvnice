"""Explicit, checked Evomi sessions at profile start; credentials stay in the vault."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import secrets
import time

from . import tunnel
from .identity import validate_name
from ..accountvault import _write
from ..runlock import OsLock

POLICY_FILE = 'proxy_session_policy.json'
GEO_URL = 'https://ipwho.is/?fields=success,ip,country,country_code,region,city'
IP_URL = 'https://api.ipify.org'

class StartError(ValueError):
    pass

def root(cfg):return Path(cfg.db_path).resolve().parent

def policy(cfg):
    try:
        data=json.loads((root(cfg)/POLICY_FILE).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return {'enabled':False,'seconds':300,'require_new_ip':True}
    except (OSError,ValueError):raise ValueError('Die Proxy-Sitzungseinstellung ist nicht lesbar.') from None
    if (not isinstance(data,dict) or data.get('version')!=1 or type(data.get('enabled')) is not bool
            or data.get('seconds')!=300 or type(data.get('require_new_ip')) is not bool):
        raise ValueError('Die Proxy-Sitzungseinstellung ist ungültig.')
    return data

def supported(proxy):return proxy is not None and proxy.host.lower()=='mp.evomi.com'

def _base(proxy):
    if not proxy.password:raise ValueError('Für die Evomi-Sitzung fehlen Zugangsdaten.')
    password=proxy.password
    for name in ('session','hardsession','lockedsession','lifetime'):
        pattern=r'_'+name+r'-([A-Za-z0-9]+)(?=_|$)'
        if '_'+name+'-' in password and not re.search(pattern,password):
            raise ValueError('Ein vorhandener Evomi-Sitzungsparameter ist nicht eindeutig lesbar.')
        password=re.sub(pattern,'',password)
    return replace(proxy,password=password)

def _hash(proxy):return hashlib.sha256(_base(proxy).url.encode()).hexdigest()

def _path(cfg,identity):
    validate_name(identity)
    return root(cfg)/'proxy_sessions'/(identity+'.json')

def load(cfg,identity):
    try:
        data=json.loads(_path(cfg,identity).read_text(encoding='utf-8-sig'))
        if (not isinstance(data,dict) or data.get('identity')!=identity or data.get('version')!=1
                or not re.fullmatch('[a-z0-9]{8}',data['session_id'])
                or not re.fullmatch('[0-9a-f]{64}',data['source_hash'])
                or type(data['started_at']) not in (int,float) or not math.isfinite(data['started_at'])
                or data['seconds']!=300):return None
        if not ipaddress.ip_address(data['ip']).is_global:return None
        return data
    except (OSError,ValueError,KeyError,TypeError):return None

def _with_session(proxy,session):
    base=_base(proxy)
    return replace(base,password=base.password+'_session-'+session+'_lifetime-5')

def current(cfg,identity,proxy):
    """Use the profile's last checked session when probing or resolving a proxy."""
    if not supported(proxy) or not policy(cfg)['enabled']:return proxy
    saved=load(cfg,identity)
    if saved and saved['source_hash']==_hash(proxy):return _with_session(proxy,saved['session_id'])
    return proxy

def _ip(proxy):
    status,body,_=tunnel.https_get(proxy,IP_URL,timeout=10,max_bytes=128)
    if status!=200:raise ValueError('Die öffentliche Proxy-IP konnte nicht bestätigt werden.')
    ip=ipaddress.ip_address(body.strip())
    if not ip.is_global:raise ValueError('Der Proxy liefert keine öffentliche IP.')
    return str(ip)

def prepare_start(cfg,identity,proxy):
    """One new binding, no IP-shopping retry; do not launch after a failed check."""
    rules=policy(cfg)
    if not rules['enabled'] or not supported(proxy):return proxy
    path=_path(cfg,identity)
    lock=OsLock(path.parent.with_name('proxy_session_locks')/(identity+'.lock'))
    if not lock.try_acquire({},tries=101):raise RuntimeError('Die Proxy-Sitzung dieses Profils wird bereits vorbereitet.')
    try:
        baseline=_ip(current(cfg,identity,proxy))
        session=secrets.token_hex(4)
        candidate=_with_session(proxy,session)
        started=time.time()
        status,body,_=tunnel.https_get(candidate,GEO_URL,timeout=10,max_bytes=8192)
        if status!=200:raise StartError('Die neue Proxy-Sitzung konnte nicht geprüft werden.')
        data=json.loads(body)
        if not isinstance(data,dict) or data.get('success') is not True:raise ValueError('Die neue Proxy-Sitzung liefert keinen bestätigten Standort.')
        ip=str(ipaddress.ip_address(data['ip']))
        if not ipaddress.ip_address(ip).is_global:raise ValueError('Die neue Proxy-IP ist nicht öffentlich.')
        if _ip(candidate)!=ip:raise StartError('Die IP wechselte bereits während der Startprüfung. Profilstart angehalten.')
        if rules['require_new_ip'] and ip==baseline:
            raise StartError('Evomi hat dieselbe Ausgangs-IP geliefert. Ein IP-Wechsel ist nicht bestätigt; Profilstart angehalten.')
        geo={}
        for key in ('country','country_code','region','city'):
            value=data.get(key) or ''
            if not isinstance(value,str) or len(value)>200 or any(ord(c)<32 for c in value):raise ValueError('Ungültiger Proxy-Standort.')
            geo[key]=value
        if time.time()-started>30:raise ValueError('Die neue Proxy-Sitzung ist für den Profilstart bereits zu alt.')
        report={'version':1,'identity':identity,'source_hash':_hash(proxy),'session_id':session,
                'started_at':started,'checked_at':datetime.now(timezone.utc).isoformat(),
                'seconds':300,'ip':ip,'previous_ip':baseline,'ip_changed':ip!=baseline,**geo}
        _write(path,report)
        proxy.password=candidate.password
        return proxy
    except StartError:raise
    except (tunnel.TunnelError,OSError,KeyError,TypeError,ValueError):
        raise ValueError('Die frische 300-Sekunden-Proxy-Sitzung konnte nicht sicher bestätigt werden. '
                         'Profilstart angehalten; keine Ersatzverbindung verwendet.') from None
    finally:lock.release()

def summary(cfg,identity):
    if not policy(cfg)['enabled']:return ''
    saved=load(cfg,identity)
    if saved is None:return 'Proxy-Sitzung: 300 Sekunden; neue Sitzung und IP-Prüfung beim nächsten Profilstart.'
    remaining=min(300,max(0,300-int(time.time()-saved['started_at'])))
    return ('Proxy-Sitzung: '+str(remaining)+' von 300 Sekunden geschätzt verbleibend · letzte Start-IP '+saved['ip']+
            '. Der Anbieter kann die IP bei Verbindungsproblemen früher wechseln.')
