"""Content-addressed verified VOICEVOX WAV reuse; no network or synthesis."""
from __future__ import annotations
import hashlib
import io
import json
import os
import tempfile
import wave
from pathlib import Path

VERSION = 'voicevox-wav-cache-v1'


def voice_cache_key(*, text, engine_version, style_id, speed_scale,
                    intonation_scale=1.0, sample_rate=48000, channels=2, dictionary_revision="UNSPECIFIED", speaker_uuid="UNSPECIFIED"):
    if not text or not engine_version:
        raise ValueError('voice text and engine version required')
    identity = dict(version=VERSION, text=text, engine_version=engine_version,
                    style_id=style_id, speed_scale=speed_scale,
                    intonation_scale=intonation_scale, sample_rate=sample_rate,
                    channels=channels, codec='pcm_s16le', dictionary_revision=dictionary_revision, speaker_uuid=speaker_uuid)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def _wav_receipt(path):
    return _wav_receipt_bytes(Path(path).read_bytes())


def _wav_receipt_bytes(data):
    with wave.open(io.BytesIO(data),'rb') as w:
        if (w.getcomptype()!='NONE' or w.getsampwidth()!=2 or w.getframerate()!=48000
                or w.getnchannels()!=2 or w.getnframes()<=0):
            raise ValueError('nonempty 48kHz stereo PCM16 WAV required')
        count=w.getnframes()
        if len(w.readframes(count))!=count*4:
            raise ValueError('truncated cached WAV')
        duration=count/w.getframerate()
    return {'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'duration_s':duration}


def _paths(cache_dir,key):
    if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('invalid content address')
    folder=Path(cache_dir)/key[:2]
    return folder/key,folder/(key+'.json')


def _atomic_bytes(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.partial-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def restore_voice(cache_dir,key,destination):
    """Return receipt on verified hit, None on missing/stale/corrupt cache."""
    audio,receipt=_paths(cache_dir,key)
    try:
        stored=json.loads(receipt.read_text())
        data=audio.read_bytes()
        actual=_wav_receipt_bytes(data)
        if stored!={'version':VERSION,'key':key,**actual}:return None
        # Copy verified bytes atomically; finished output is never replaced by a partial file.
        _atomic_bytes(Path(destination),data)
        return actual
    except (OSError,ValueError,EOFError,wave.Error):
        return None


def store_voice(cache_dir,key,source):
    audio,receipt=_paths(cache_dir,key)
    data=Path(source).read_bytes()
    actual=_wav_receipt_bytes(data)
    _atomic_bytes(audio,data)
    _atomic_bytes(receipt,json.dumps({'version':VERSION,'key':key,**actual},sort_keys=True).encode())
    return actual
