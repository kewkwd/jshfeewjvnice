"""Read explicitly configured credentials without exposing their values."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from .cookies import _key


@dataclass
class Credentials:
    user: str = field(repr=False)
    password: str = field(repr=False)


class MissingCreds(RuntimeError):
    pass


def get(platform, identity, path="data/credentials.json"):
    _key(platform, "Plattform-Name")
    _key(identity, "Identity-Name")
    configured_path = Path(path)
    if configured_path.is_file():
        try:
            data = json.loads(configured_path.read_text(encoding="utf-8-sig"))
            platform_node = data.get(platform) if isinstance(data, dict) else None
            node = platform_node.get(identity) if isinstance(platform_node, dict) else None
        except (OSError, UnicodeError, ValueError):
            node = None
        if isinstance(node, dict):
            user, password = node.get("user"), node.get("password")
            if isinstance(user, str) and user.strip() and isinstance(password, str) and password:
                return Credentials(user, password)
    prefix = f"SS_{platform.upper()}_{identity.upper()}_"
    user, password = os.getenv(prefix + "USER"), os.getenv(prefix + "PASS")
    if user and user.strip() and password:
        return Credentials(user, password)
    raise MissingCreds(f"Keine Zugangsdaten für {platform}/{identity}; konfigurierte Credentials-Datei "
                       f"oder ENV {prefix}USER / {prefix}PASS verwenden")
