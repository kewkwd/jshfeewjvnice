"""Bounded preview data and input consumption, independent of native windows."""
import json,struct,zlib
from ..net.errors import FinalError

class InputRejected(ValueError):
    """Invalid user input; the running form must remain available."""

def screen_size(frame):
    invalid='Android-Bildschirm konnte nicht vollständig gelesen werden.'
    if not isinstance(frame,bytes) or len(frame)<57 or len(frame)>16_000_000 or not frame.startswith(b'\x89PNG\r\n\x1a\n'):
        raise FinalError(invalid)
    offset=8;size=None;data_present=False
    while offset+12<=len(frame):
        length=struct.unpack_from('>I',frame,offset)[0];kind=frame[offset+4:offset+8];end=offset+12+length
        if end>len(frame):raise FinalError(invalid)
        data=frame[offset+8:offset+8+length]
        crc=struct.unpack_from('>I',frame,offset+8+length)[0]
        if zlib.crc32(kind+data)&0xffffffff!=crc:raise FinalError(invalid)
        if size is None:
            if kind!=b'IHDR' or length!=13:raise FinalError(invalid)
            width,height,depth,colour,compression,filtering,interlace=struct.unpack('>IIBBBBB',data)
            depths={0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}
            if width<100 or height<100 or width>8192 or height>8192 or width*height>20_000_000:
                raise FinalError('Android-Grafik liefert keine unterstützte Bildschirmgröße.')
            if depth not in depths.get(colour,()) or compression or filtering or interlace not in (0,1):raise FinalError(invalid)
            size=width,height
        elif kind==b'IHDR':raise FinalError(invalid)
        if kind==b'IDAT':data_present=data_present or bool(length)
        if kind==b'IEND':
            if length or not data_present or end!=len(frame):raise FinalError(invalid)
            return size
        offset=end
    raise FinalError(invalid)

def consume_inputs(inputs,apply,notify):
    accepted=0
    for path in sorted(inputs.glob('*.json')):
        try:
            if path.is_symlink() or path.stat().st_size>4096:raise InputRejected('Ungültige Eingabedatei.')
            try:command=json.loads(path.read_text(encoding='utf8'))
            except (ValueError,UnicodeError):raise InputRejected('Ungültiges Eingabeformat.') from None
            apply(command);accepted+=1
        except InputRejected:
            notify('Android-Eingabe konnte nicht verarbeitet werden. Der Auftrag bleibt geöffnet; Eingabe prüfen und erneut versuchen.')
        finally:path.unlink(missing_ok=True)
    return accepted
