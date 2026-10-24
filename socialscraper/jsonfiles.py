"""Atomic non-secret JSON reports; secret storage retains its stricter writer."""
import json
import os
from pathlib import Path
import tempfile


def write_report(path, report, *, prefix):
    """Replace only after a complete write; always release the temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    try:
        stream = os.fdopen(fd, 'w', encoding='utf8')
        fd = None
        with stream:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.exists(temporary):
            os.unlink(temporary)
