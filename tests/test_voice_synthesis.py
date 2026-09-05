"""Test suite: Voice nudge synthesis via Gemini native TTS (E.8).

POST /api/cases/{case_id}/voice-nudge must:
  (a) synthesize the case's voice_script draft (en/hi-en) into a playable
      WAV through a (mocked) TTS client — raw Gemini PCM is wrapped into a
      WAV container for the browser <audio> player;
  (b) persist the nudge and expose an audio_url that
      GET /api/cases/{case_id}/voice-nudge/audio/{nudge_id} streams back as
      audio/wav;
  (c) record a SIMULATED outbound "call" in the append-only audit trail
      (status SUCCESS, audio ref retrievable via entry.details);
  (d) fail cleanly — 400 for an unknown register, 404 for an unknown case or
      missing audio, 502 when the TTS backend returns no inline audio.

The TTS backend is injected as a fake client so no network / API key is
ever needed. Real-path wiring (shared GEMINI_API_KEY, Gemini native-TTS
model from the current speech-generation docs) is covered in voice_agent.
"""

import base64
import io
import wave

from fastapi.testclient import TestClient

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.contracts import Action, CandidateAction
from app.dashboard.case_inspector import configure
from app.executor.voice_agent import (
    TTS_MODEL_DEFAULT,
    VoiceSynthesis,
    VoiceSynthesisError,
)
from app.main import app

client = TestClient(app)

PCM_ONE_SECOND = b"\x00\x00" * (24_000)  # 1s of 24kHz mono 16-bit silence


class FakeInlineData:
    def __init__(self, data: bytes, mime_type: str):
        self.data = base64.b64encode(data).decode()
        self.mime_type = mime_type


class FakePart:
    def __init__(self, data: bytes, mime_type: str):
        self.inline_data = FakeInlineData(data, mime_type)


class FakeContent:
    def __init__(self, parts):
        self.parts = parts


class FakeCandidate:
    def __init__(self, parts):
        self.content = FakeContent(parts)


class FakeTtsResponse:
    def __init__(self, data: bytes, mime_type: str = "audio/pcm"):
        self.candidates = [FakeCandidate([FakePart(data, mime_type)])]


class FakeTtsClient:
    """Records the call and returns a canned Gemini-style audio response."""

    def __init__(self, data: bytes = PCM_ONE_SECOND, mime_type: str = "audio/pcm"):
        self._data = data
        self._mime_type = mime_type
        self.calls: list[dict] = []

    def generate_content(self, text: str, *, generation_config: dict | None = None):
        self.calls.append({"text": text, "generation_config": generation_config})
        return FakeTtsResponse(self._data, self._mime_type)


class NoAudioTtsClient:
    """A TTS backend that returns a response with no usable inline audio."""

    def generate_content(self, text: str, *, generation_config: dict | None = None):
        return FakeTtsResponseNoParts()


class FakeTtsResponseNoParts:
    def __init__(self):
        self.candidates = [FakeCandidate([])]


def _build_trace(tracer: DecisionTracer, case_id: str) -> None:
    tracer.build(
        case_id=case_id,
        trigger_event="payment.failed:insufficient_funds",
        state="RISK_ASSESSED",
        root_cause="insufficient_funds",
        natural_payment_probability=0.28,
        uplift_segment="PERSUADABLE",
        revenue_at_risk_paise=1_500_000,  # ₹15,000
        candidate_actions=[
            CandidateAction(
                action=Action.SEND_SMS,
                expected_recovery_paise=900_000,
                communication_cost_paise=2500,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=0.4,
            )
        ],
        selected_action=Action.SEND_SMS,
        selected_economic_score=0.4,
        selection_reasoning="SMS is the highest positive incremental channel",
        policy_gate_result="APPROVED",
    )


class TestVoiceSynthesisUnit:
    """The TTS layer: PCM wrapping, wav passthrough, empty-guard."""

    def test_pcm_is_wrapped_into_wav_container(self):
        fake = FakeTtsClient(data=PCM_ONE_SECOND, mime_type="audio/pcm")
        nudge = VoiceSynthesis(client=fake).synthesize("Namaste, aapka payment pending hai.")

        assert nudge.media_type == "audio/wav"
        assert nudge.wav_bytes[:4] == b"RIFF"
        assert nudge.duration_seconds == 1.0
        assert nudge.text == "Namaste, aapka payment pending hai."
        assert nudge.voice == "Kore"
        assert nudge.model == TTS_MODEL_DEFAULT

        with wave.open(io.BytesIO(nudge.wav_bytes), "rb") as wav:
            assert wav.getframerate() == 24_000
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == 24_000

    def test_wav_payload_is_passed_through_unchanged(self):
        existing = bytes.fromhex("52494646") + b"\x00" * 64  # RIFF + padding
        fake = FakeTtsClient(data=existing, mime_type="audio/wav")
        nudge = VoiceSynthesis(client=fake).synthesize("Hello there.")

        assert nudge.media_type == "audio/wav"
        assert nudge.wav_bytes == existing

    def test_empty_script_is_rejected(self):
        fake = FakeTtsClient()
        try:
            VoiceSynthesis(client=fake).synthesize("   ")
        except VoiceSynthesisError:
            pass
        else:
            raise AssertionError("expected VoiceSynthesisError for empty script")
        assert fake.calls == []


class TestVoiceNudgeApi:
    """The HTTP endpoints: generate, stream, audit trail, error codes."""

    def setup_method(self):
        self.case_id = "RC_VOICE_SYNTH_001"
        self.tracer = DecisionTracer()
        _build_trace(self.tracer, self.case_id)
        self.audit = AuditLogger()
        self.fake_tts = FakeTtsClient(data=PCM_ONE_SECOND, mime_type="audio/pcm")
        configure(
            tracer=self.tracer,
            audit=self.audit,
            prevention=PreventionLog(),
            outbox=[],
            voice_synthesis=VoiceSynthesis(client=self.fake_tts),
        )

    def test_generate_records_call_and_streams_audio(self):
        resp = client.post(
            f"/api/cases/{self.case_id}/voice-nudge",
            json={"register": "hi-en", "actor": "ops.shivam"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["nudge_id"].startswith("vn_")
        assert body["case_id"] == self.case_id
        assert body["register"] == "hi-en"
        assert body["media_type"] == "audio/wav"
        assert body["duration_seconds"] == 1.0
        assert body["call_state"] == "SUCCESS"
        assert "payment" in body["text"]
        assert body["audio_url"].startswith(
            f"/api/cases/{self.case_id}/voice-nudge/audio/"
        )

        assert len(self.fake_tts.calls) == 1
        assert "payment" in self.fake_tts.calls[0]["text"]

        audio = client.get(body["audio_url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.content[:4] == b"RIFF"

    def test_call_is_logged_in_append_only_audit_trail(self):
        resp = client.post(
            f"/api/cases/{self.case_id}/voice-nudge",
            json={"register": "en"},
        )
        assert resp.status_code == 200
        nudge_id = resp.json()["nudge_id"]

        calls = [
            e for e in self.audit.entries_for_case(self.case_id)
            if e.trigger_type == "VOICE_CALL_RECORDED"
        ]
        assert len(calls) == 1
        entry = calls[0]
        assert entry.trigger_event == "simulated_outbound_call_en"
        assert entry.details["audio_id"] == nudge_id
        assert entry.details["call_state"] == "SUCCESS"
        assert entry.details["voice"] == "Kore"
        assert entry.details["register"] == "en"
        assert entry.details["duration_seconds"] == 1.0
        assert entry.payload_hash
        assert self.audit.verify_chain() == []

    def test_unknown_case_returns_404(self):
        resp = client.post(
            "/api/cases/RC_NO_SUCH_CASE/voice-nudge",
            json={"register": "en"},
        )
        assert resp.status_code == 404
        assert "No decision packet" in resp.json()["detail"]

    def test_unknown_register_returns_400(self):
        resp = client.post(
            f"/api/cases/{self.case_id}/voice-nudge",
            json={"register": "fr"},
        )
        assert resp.status_code == 400
        assert "Unknown register" in resp.json()["detail"]

    def test_missing_audio_returns_404(self):
        resp = client.get(f"/api/cases/{self.case_id}/voice-nudge/audio/vn_does_not_exist")
        assert resp.status_code == 404
        assert "No voice nudge audio" in resp.json()["detail"]


class TestVoiceSynthesisFailure:
    """TTS backend failures must surface cleanly (502), not crash."""

    def setup_method(self):
        self.case_id = "RC_VOICE_FAIL_001"
        self.tracer = DecisionTracer()
        _build_trace(self.tracer, self.case_id)
        configure(
            tracer=self.tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=[],
            voice_synthesis=VoiceSynthesis(client=NoAudioTtsClient()),
        )

    def test_no_inline_audio_returns_502(self):
        resp = client.post(
            f"/api/cases/{self.case_id}/voice-nudge",
            json={"register": "en"},
        )
        assert resp.status_code == 502
        assert "Voice synthesis failed" in resp.json()["detail"]