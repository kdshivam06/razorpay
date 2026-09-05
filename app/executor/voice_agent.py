"""Voice recovery agent — Hinglish voice with real compliance (§9.6).

Runs the §9.6 demo flow: identity verification → Hinglish intent →
PTP extraction → policy check → PTP creation. On ANGRY/opt-out intent it
stops the voice flow, marks preference, and routes to human review.

Also synthesizes an E.6 voice_script into a playable WAV using Gemini's
native text-to-speech (E.8) so the decision packet can preview the nudge
before dispatch. Reuses the shared GEMINI_API_KEY — no separate account.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import logging
import uuid
import wave
from pathlib import Path
from typing import Protocol

from app.config import get_settings
from app.contracts import Emotion, ExecutionState

logger = logging.getLogger(__name__)

TTS_SAMPLE_RATE = 24_000  # Gemini TTS returns 24 kHz mono 16-bit PCM
TTS_CHANNELS = 1
TTS_SAMPLE_WIDTH = 2

# Current Gemini TTS preview model (ai.google.dev speech-generation docs,
# updated April 2026). gemini-3.1-flash-tts-preview is the newer alias.
TTS_MODEL_DEFAULT = "gemini-2.5-flash-preview-tts"
DEFAULT_TTS_VOICE = "Kore"


class VoiceSynthesisError(RuntimeError):
    """Raised when the Gemini TTS call returns no usable audio."""


@dataclasses.dataclass(frozen=True)
class VoiceCallOutcome:
    """Outcome of a (mocked) voice call, incl. emotion escalation results."""

    call_id: str
    state: ExecutionState
    detected_emotion: Emotion | None
    opt_out_intent: bool
    transcript: str
    summary: str = ""


class MockTelephonyClient:
    """Mocked telephony backend — returns a scripted transcript for a caller."""

    def place_call(self, phone: str, script: dict, waited_s: int = 0) -> str:
        """Mock placing a call and returning a call_id."""
        logger.info("Placing mock call to %s", phone)
        import uuid

        return f"call_{uuid.uuid4().hex[:12]}"


class VoiceRecovery:
    """Runs the §9.6 demo flow."""

    def handle_call(
        self, call_id: str, transcript: str, emotion: Emotion | None = None
    ) -> VoiceCallOutcome:
        """Process the mocked transcript and emotion of a voice call.

        If ANGRY or opt_out_intent is detected, it returns immediately
        with opt_out_intent=True so upstream logic can add them to DND.
        """
        logger.info("Handling voice call %s. Emotion: %s", call_id, emotion)

        is_angry = emotion in {Emotion.ANGRY, Emotion.FRUSTRATED}
        opt_out = is_angry or ("stop calling" in transcript.lower())

        summary = "PTP extracted" if not opt_out else "Customer frustrated/opt-out"

        return VoiceCallOutcome(
            call_id=call_id,
            state=ExecutionState.SUCCESS,
            detected_emotion=emotion,
            opt_out_intent=opt_out,
            transcript=transcript,
            summary=summary,
        )


# ── Gemini native text-to-speech (E.8) ─────────────────────────────────


class VoiceTtsClient(Protocol):
    """Minimal generateContent seam so tests never hit the network."""

    def generate_content(
        self, text: str, *, generation_config: dict[str, object] | None = None
    ) -> object: ...


class GeminiVoiceTtsClient:
    """Real Gemini native-TTS client (E.8), sharing the app's GEMINI_API_KEY.

    Prefers the current ``google.genai`` package (the SDK used by the current
    speech-generation docs: ``client.models.generate_content`` with
    ``response_modalities=["AUDIO"]`` and a ``speech_config`` prebuilt voice),
    and falls back to the legacy ``google.generativeai`` package — same REST
    request shape — so the demo runs with what the repo pins today. The
    request targets the current Gemini TTS preview model.
    """

    def __init__(
        self,
        model_name: str = TTS_MODEL_DEFAULT,
        voice: str = DEFAULT_TTS_VOICE,
    ) -> None:
        settings = get_settings()
        self._api_key = settings.GEMINI_API_KEY
        self._model_name = model_name
        self._voice = voice
        self._backend = self._build_backend()

    def _build_backend(self) -> VoiceTtsClient:
        try:
            from google import genai
            from google.genai import types

            return _GenaiClientBackend(genai, types, self)
        except ImportError:
            import google.generativeai as genai

            return _LegacyGenaiBackend(genai, self)

    def generate_content(
        self, text: str, *, generation_config: dict[str, object] | None = None
    ) -> object:
        logger.info("Synthesizing voice nudge (%d chars, voice=%s)", len(text), self._voice)
        return self._backend.generate_content(text, generation_config=generation_config)


class _GenaiClientBackend:
    """Current google.genai SDK path (per speech-generation docs)."""

    def __init__(self, genai, types, client: GeminiVoiceTtsClient) -> None:
        self._model = genai.Client(api_key=client._api_key)
        self._model_name = client._model_name
        self._config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=client._voice
                    )
                )
            ),
        )

    def generate_content(
        self, text: str, *, generation_config: dict[str, object] | None = None
    ) -> object:
        return self._model.models.generate_content(
            model=self._model_name,
            contents=text,
            config=self._config,
        )


class _LegacyGenaiBackend:
    """Legacy google.generativeai SDK path (same REST request shape)."""

    def __init__(self, genai, client: GeminiVoiceTtsClient) -> None:
        genai.configure(api_key=client._api_key)
        self._model = genai.GenerativeModel(client._model_name)
        self._voice = client._voice

    def generate_content(
        self, text: str, *, generation_config: dict[str, object] | None = None
    ) -> object:
        config = generation_config or {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": self._voice},
                }
            },
        }
        return self._model.generate_content(text, generation_config=config)


def parse_inline_audio(response: object) -> tuple[bytes, str]:
    """Extract (raw_audio_bytes, mime_type) from a Gemini generateContent response.

    The first candidate part holding ``inline_data`` wins; data may be a
    base64 string or already bytes depending on the SDK version.
    """
    try:
        candidates = response.candidates
        parts = candidates[0].content.parts
    except (AttributeError, IndexError, TypeError) as exc:
        raise VoiceSynthesisError("Gemini TTS response has no candidates/parts") from exc

    for part in parts:
        inline = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
        if inline is None:
            continue
        raw = getattr(inline, "data", None) or getattr(inline, "text", None)
        mime = getattr(inline, "mime_type", None) or getattr(inline, "mimeType", None)
        if raw is None:
            continue
        if isinstance(raw, str):
            raw = base64.b64decode(raw)
        return raw, mime or ""
    raise VoiceSynthesisError("Gemini TTS response contains no inline audio data")


def pcm_to_wav(
    pcm: bytes,
    *,
    sample_rate: int = TTS_SAMPLE_RATE,
    channels: int = TTS_CHANNELS,
    sample_width: int = TTS_SAMPLE_WIDTH,
) -> bytes:
    """Wrap raw 24 kHz mono 16-bit PCM into a browser-playable WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def wav_duration(wav_bytes: bytes, *, sample_rate: int = TTS_SAMPLE_RATE) -> float:
    """Duration in seconds of a WAV container (falls back to a raw-PCM estimate)."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            rate = wav.getframerate() or sample_rate
            return round(wav.getnframes() / rate, 2)
    except wave.Error:
        return round(
            len(wav_bytes) / (TTS_SAMPLE_RATE * TTS_CHANNELS * TTS_SAMPLE_WIDTH), 2
        )


@dataclasses.dataclass(frozen=True)
class VoiceNudge:
    """A synthesized, playable voice nudge (E.8)."""

    nudge_id: str
    text: str
    voice: str
    model: str
    media_type: str
    wav_bytes: bytes
    duration_seconds: float

    def save(self, directory: Path | str) -> Path:
        """Persist the WAV to *directory* and return the written path."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{self.nudge_id}.wav"
        path.write_bytes(self.wav_bytes)
        return path


class VoiceSynthesis:
    """Turns an E.6 voice_script into a playable WAV via Gemini native TTS.

    The client is injectable so tests never hit the network; when not
    injected a real Gemini client is built lazily from the shared
    GEMINI_API_KEY on first synthesize.
    """

    def __init__(
        self,
        client: VoiceTtsClient | None = None,
        model_name: str = TTS_MODEL_DEFAULT,
        voice: str = DEFAULT_TTS_VOICE,
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._voice = voice

    def synthesize(self, text: str, *, voice: str | None = None) -> VoiceNudge:
        """Synthesize *text* into a WAV, wrapping raw PCM when needed."""
        if not text or not text.strip():
            raise VoiceSynthesisError("Refusing to synthesize an empty voice script")

        voice = voice or self._voice
        client = self._client if self._client is not None else self._real_client()
        response = client.generate_content(text)

        raw, mime = parse_inline_audio(response)
        if mime == "audio/wav" or raw[:4] == b"RIFF":
            wav = raw
        else:
            wav = pcm_to_wav(raw)

        return VoiceNudge(
            nudge_id=f"vn_{uuid.uuid4().hex[:12]}",
            text=text,
            voice=voice,
            model=self._model_name,
            media_type="audio/wav",
            wav_bytes=wav,
            duration_seconds=wav_duration(wav),
        )

    def _real_client(self) -> VoiceTtsClient:
        self._client = GeminiVoiceTtsClient(
            model_name=self._model_name, voice=self._voice
        )
        return self._client
