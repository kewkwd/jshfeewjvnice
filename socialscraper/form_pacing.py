"""Global, explicit pauses before mailbox text fields; no profile personas."""
import json
from pathlib import Path
from . import accountvault

FILE_NAME='registration_pacing.json'
OPTIONS={0:'Zügig – ohne Zusatzpause',750:'Ruhig – 0,75 Sekunden',1500:'Langsam – 1,5 Sekunden'}

def validate(value):
    if type(value) is not int or value not in OPTIONS:
        raise ValueError('Bitte ein angebotenes Ausfülltempo auswählen.')
    return value

def load(db_path):
    try:data=json.loads((Path(db_path).parent/FILE_NAME).read_text(encoding='utf8'))
    except FileNotFoundError:return 0
    except (OSError,ValueError):
        raise ValueError('Das gespeicherte Ausfülltempo ist nicht lesbar.') from None
    if not isinstance(data,dict) or data.get('version')!=1:
        raise ValueError('Das gespeicherte Ausfülltempo hat ein unbekanntes Format.')
    return validate(data.get('pause_ms'))

def save(db_path,value):
    accountvault._write(Path(db_path).parent/FILE_NAME,{'version':1,'pause_ms':validate(value)})

def before_input(ctx):
    remaining=validate(ctx.input_pause_ms)
    ctx.check_cancelled()
    while remaining:
        duration=min(100,remaining)
        ctx.warte(duration)
        ctx.check_cancelled()
        remaining-=duration
