"""Plan character cues from measured PCM WAV; never synthesize or render.

Expressions are selected semantic beats, not fixed-period changes. Emphasis is
an explicit Python Unicode code-point range for one occurrence of a phrase or
clause. The existing YMM4 importer only reads speaker/text CSV columns: this
sidecar requires a separately verified template, plugin, or renderer adapter.
Audio energy is an inexpensive mouth-activity proxy, not phoneme alignment.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import wave
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

VERSION = "media-performance-plan-v1"
SPEAKERS = ("ずんだもん", "四国めたん")


class Expression(str, Enum):
    NORMAL = "NORMAL"
    HAPPY = "HAPPY"
    SURPRISED = "SURPRISED"
    SERIOUS = "SERIOUS"
    SAD = "SAD"


class Mouth(str, Enum):
    CLOSED = "CLOSED"
    HALF = "HALF"
    OPEN = "OPEN"


@dataclass(frozen=True)
class MouthSettings:
    window_ms: int = 40
    min_hold_ms: int = 80
    silence_rms: float = 0.006
    half_enter_rms: float = 0.015
    half_exit_rms: float = 0.010
    open_enter_rms: float = 0.070
    open_exit_rms: float = 0.045

    def validate(self) -> None:
        if (type(self.window_ms) is not int or type(self.min_hold_ms) is not int
                or self.window_ms < 10 or self.window_ms > 200
                or self.min_hold_ms < self.window_ms):
            raise ValueError("invalid mouth window/hold settings")
        values = (self.silence_rms, self.half_exit_rms, self.half_enter_rms,
                  self.open_exit_rms, self.open_enter_rms)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values) or not (0 <= values[0] < values[1] < values[2] < values[3] < values[4] <= 1):
            raise ValueError("mouth thresholds must be finite and strictly ordered")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def audio_cache_key(audio: bytes, settings: MouthSettings, *, engine_version: str,
                    style_id: str | int) -> str:
    """Cache identity includes measured audio, synthesis identity and algorithm."""
    settings.validate()
    if (not isinstance(engine_version, str) or not engine_version.strip()
            or type(style_id) not in (str, int) or not str(style_id).strip()):
        raise ValueError("engine_version and style_id are required")
    return _digest({"version": VERSION, "audio_sha256": hashlib.sha256(audio).hexdigest(),
                    "settings": asdict(settings), "engine_version": engine_version,
                    "style_id": str(style_id)})


def measure_mouth(path: str | Path, settings: MouthSettings = MouthSettings()) -> dict[str, Any]:
    """Return merged activity intervals in seconds, measured from PCM samples.

    Silence immediately closes the mouth even inside the minimum dwell period.
    A new non-silent state must persist for min_hold_ms; threshold hysteresis
    prevents jitter. Stereo energy is averaged per sample, never downmixed by
    signed averaging (which can incorrectly cancel antiphase channels).
    """
    settings.validate()
    with wave.open(str(path), "rb") as wav:
        channels, width, rate, count = (wav.getnchannels(), wav.getsampwidth(),
                                        wav.getframerate(), wav.getnframes())
        if wav.getcomptype() != "NONE" or width not in (1, 2, 3, 4) or rate <= 0 or count <= 0:
            raise ValueError("nonempty uncompressed 8/16/24/32-bit PCM WAV required")
        step = max(1, round(rate * settings.window_ms / 1000))
        state, candidate, pending_frames = Mouth.CLOSED, Mouth.CLOSED, 0
        intervals: list[dict[str, Any]] = []
        frame = 0
        while frame < count:
            raw = wav.readframes(min(step, count - frame))
            frame_bytes = channels * width
            if len(raw) % frame_bytes or not raw:
                raise ValueError("truncated PCM WAV")
            frames = len(raw) // frame_bytes
            if frames < min(step, count - frame):
                raise ValueError("truncated PCM WAV")
            if width == 1:
                values = [int(value) - 128 for value in raw]
            else:
                values = [int.from_bytes(raw[pos:pos + width], "little", signed=True)
                          for pos in range(0, len(raw), width)]
            rms = math.sqrt(sum(v * v for v in values) / len(values)) / (2 ** (width * 8 - 1))
            if rms <= settings.silence_rms:
                state, candidate, pending_frames = Mouth.CLOSED, Mouth.CLOSED, 0
            else:
                if state == Mouth.OPEN and rms >= settings.open_exit_rms:
                    desired = Mouth.OPEN
                elif rms >= settings.open_enter_rms:
                    desired = Mouth.OPEN
                elif state != Mouth.CLOSED and rms >= settings.half_exit_rms:
                    desired = Mouth.HALF
                elif rms >= settings.half_enter_rms:
                    desired = Mouth.HALF
                else:
                    desired = Mouth.CLOSED
                if desired == state:
                    candidate, pending_frames = state, 0
                else:
                    if candidate != desired:
                        candidate, pending_frames = desired, 0
                    pending_frames += frames
                    if pending_frames >= rate * settings.min_hold_ms / 1000:
                        state, pending_frames = desired, 0
            start, end = frame / rate, (frame + frames) / rate
            if intervals and intervals[-1]["state"] == state.value:
                intervals[-1]["end_s"] = end
            else:
                intervals.append({"start_s": start, "end_s": end, "state": state.value})
            frame += frames
    return {"duration_s": count / rate, "sample_rate": rate, "channels": channels,
            "intervals": intervals, "at_end_state": Mouth.CLOSED.value,
            "method": "PCM_RMS_ACTIVITY_PROXY_NOT_PHONEME_ALIGNMENT"}



def _verified_mouth_payload(payload: Any) -> bool:
    """Cheap structural verification before reusing a checksummed derived result."""
    if not isinstance(payload, dict):
        return False
    duration = payload.get("duration_s")
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return False
    if (type(payload.get("sample_rate")) is not int or payload["sample_rate"] <= 0
            or type(payload.get("channels")) is not int or payload["channels"] <= 0
            or payload.get("at_end_state") != "CLOSED"
            or payload.get("method") != "PCM_RMS_ACTIVITY_PROXY_NOT_PHONEME_ALIGNMENT"):
        return False
    intervals = payload.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        return False
    previous = 0.0
    for interval in intervals:
        if not isinstance(interval, dict) or interval.get("state") not in {state.value for state in Mouth}:
            return False
        start, end = interval.get("start_s"), interval.get("end_s")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or start != previous or not start < end <= duration):
            return False
        previous = end
    return previous == duration


def measure_mouth_cached(path: str | Path, cache_dir: str | Path, *,
                         engine_version: str, style_id: str | int,
                         settings: MouthSettings = MouthSettings()) -> dict[str, Any]:
    """Reuse verified local derived cues across runs; corrupted records are misses.

    A cache is derived acceleration, never authoritative source audio. Writes use
    unique same-directory temporary files and atomic replacement. Independent
    workers may duplicate one analysis but cannot expose partially written JSON.
    """
    audio_path = Path(path)
    key = audio_cache_key(audio_path.read_bytes(), settings,
                          engine_version=engine_version, style_id=style_id)
    directory = Path(cache_dir)
    record_path = directory / f"{key}.mouth.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        payload = record.get("payload") if isinstance(record, dict) else None
        if (isinstance(record, dict) and record.get("schema_version") == VERSION
                and record.get("dependency_key") == key
                and record.get("payload_sha256") == _digest(payload)
                and _verified_mouth_payload(payload)):
            return payload
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    payload = measure_mouth(audio_path, settings)
    # Prevent caching under the earlier dependency key if source audio was
    # replaced during analysis; the caller can retry with the new file identity.
    current_key = audio_cache_key(audio_path.read_bytes(), settings,
                                  engine_version=engine_version, style_id=style_id)
    if current_key != key:
        raise ValueError("source audio changed during mouth analysis")
    record = {"schema_version": VERSION, "dependency_key": key,
              "payload_sha256": _digest(payload), "payload": payload}
    directory.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=f".{key}.", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(record, temporary, ensure_ascii=False, sort_keys=True, allow_nan=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, record_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return payload


def validate_emphasis(text: str, spans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate explicit semantic spans; never search or replace keywords."""
    if not isinstance(text, str) or not isinstance(spans, (list, tuple)):
        raise ValueError("emphasis requires caption text and a list of spans")
    result: list[dict[str, Any]] = []
    for span in spans:
        if not isinstance(span, Mapping):
            raise ValueError("emphasis span must be an object")
        start, end = span.get("start"), span.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError("emphasis requires valid Unicode code-point offsets")
        if (span.get("unit") not in ("PHRASE", "CLAUSE")
                or not isinstance(span.get("reason"), str) or not span["reason"].strip()):
            raise ValueError("emphasis must be an explicitly justified phrase/clause")
        selected = text[start:end]
        if not selected.strip() or span.get("text") != selected:
            raise ValueError("emphasis text must match exactly one explicit span")
        if any(start < old["end"] and end > old["start"] for old in result):
            raise ValueError("overlapping emphasis spans")
        result.append({"start": start, "end": end, "text": selected,
                       "unit": span["unit"], "reason": span["reason"],
                       "style": "IMPORTANT_PHRASE_COLOR_ONLY"})
    return result


def build_plan(lines: Sequence[Mapping[str, Any]], *, engine_version: str,
               style_ids: Mapping[str, str | int],
               settings: MouthSettings = MouthSettings(),
               mouth_cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Build a sequential sidecar, preserving voice/caption text exactly.

    Each line requires id, speaker, voice_text, caption_text, audio_path. Optional
    expression_beats contain measured local at_s and a typed expression; optional
    emphasis_spans use explicit offsets and require semantic_beat_id (one span
    per beat across turns). pause_after preserves existing measured-turn gaps
    of 0..0.45 seconds; mouths remain closed throughout these gaps. No provider,
    model or network calls.
    """
    if not lines:
        raise ValueError("nonempty dialogue required")
    settings.validate()
    cues, seen_ids, offset, emphasis_count = [], set(), 0.0, 0
    measured_cache: dict[str, dict[str, Any]] = {}
    emphasis_by_beat: dict[str, int] = {}
    for line in lines:
        line_id, speaker = line.get("id"), line.get("speaker")
        if not isinstance(line_id, str) or not line_id or line_id in seen_ids:
            raise ValueError("unique nonempty string line id required")
        seen_ids.add(line_id)
        if speaker not in SPEAKERS or speaker not in style_ids:
            raise ValueError("registered speaker/style required")
        voice, caption = line.get("voice_text"), line.get("caption_text")
        if not isinstance(voice, str) or not voice.strip() or not isinstance(caption, str) or not caption.strip():
            raise ValueError("voice and caption text required")
        spans = validate_emphasis(caption, line.get("emphasis_spans", []))
        beat_id = line.get("semantic_beat_id")
        if spans:
            if not isinstance(beat_id, str) or not beat_id.strip():
                raise ValueError("semantic_beat_id required for emphasis spans")
            beat_id = beat_id.strip()
            emphasis_by_beat[beat_id] = emphasis_by_beat.get(beat_id, 0) + len(spans)
            if emphasis_by_beat[beat_id] > 1:
                raise ValueError("at most 1 semantic emphasis span per beat")
        pause = line.get("pause_after", 0.0)
        if type(pause) not in (int, float) or not math.isfinite(pause) or not 0 <= pause <= .45:
            raise ValueError("pause_after must be finite and between 0 and 0.45 seconds")
        emphasis_count += len(spans)
        if emphasis_count > 3:
            raise ValueError("short video allows at most 3 semantic emphasis spans")
        audio_path = Path(line["audio_path"])
        key = audio_cache_key(audio_path.read_bytes(), settings,
                              engine_version=engine_version, style_id=style_ids[speaker])
        if key not in measured_cache:
            measured_cache[key] = (
                measure_mouth_cached(audio_path, mouth_cache_dir,
                                     engine_version=engine_version,
                                     style_id=style_ids[speaker], settings=settings)
                if mouth_cache_dir is not None else measure_mouth(audio_path, settings))
        mouth = measured_cache[key]
        beats, previous = [], -1.0
        for beat in line.get("expression_beats", []):
            at = beat.get("at_s")
            if type(at) not in (int, float) or not math.isfinite(at) or not 0 <= at < mouth["duration_s"] or at <= previous:
                raise ValueError("expression beats must be ordered inside measured audio")
            expression = Expression(beat.get("expression"))
            if not isinstance(beat.get("reason"), str) or not beat["reason"].strip():
                raise ValueError("semantic expression reason required")
            beats.append({"at_s": at, "expression": expression.value, "reason": beat["reason"]})
            previous = at
        if not beats or beats[0]["at_s"] != 0:
            beats.insert(0, {"at_s": 0.0, "expression": Expression.NORMAL.value,
                             "reason": "default neutral before first semantic beat"})
        listener = next(name for name in SPEAKERS if name != speaker)
        cues.append({"id": line_id, "speaker": speaker, "voice_text": voice,
                     "caption_text": caption, "start_s": offset,
                     "end_s": offset + mouth["duration_s"], "audio_cache_key": key,
                     "pause_after": pause, "turn_end_s": offset + mouth["duration_s"] + pause,
                     "pause_mouth": "CLOSED", "semantic_beat_id": beat_id,
                     "mouth": mouth, "expression_beats": beats, "emphasis_spans": spans,
                     "speaker_focus": {"scale": 1.08, "opacity": 1.0, "front": True},
                     "listener": {"speaker": listener, "mouth": "CLOSED", "scale": 1.0,
                                  "opacity": 0.55, "front": False}})
        offset += mouth["duration_s"] + pause
    plan = {"schema_version": VERSION, "duration_s": offset, "cues": cues,
            "mouth_settings": asdict(settings), "engine_version": engine_version,
            "emphasis_count": emphasis_count,
            "integration": {"ymm4_csv_columns": ["speaker", "voice_text"],
                            "sidecar_automatically_applied_by_csv": False,
                            "requires": "verified template/plugin or deterministic renderer adapter",
                            "rendered_or_visually_verified": False,
                            "asset_geometry": "verified expression/mouth layers required; no synthetic face patches"}}
    plan["plan_cache_key"] = _digest(plan)
    return plan
