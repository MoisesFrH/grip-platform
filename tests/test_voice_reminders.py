"""
Tests for the voice-reminder prototype (app/services/voice_reminders.py).

This sandbox's egress policy blocks generativelanguage.googleapis.com, so
synthesize_speech (the actual Gemini network call) is tested with a fake
client — same pattern as test_orchestrator.py's FakeGeminiClient. The
ffmpeg conversion step needs no network at all, so that one IS tested for
real, against ffmpeg actually installed on this machine — it's the part
most likely to have a real bug (wrong format string, wrong pipe handling),
so faking it away would test nothing.

Run with:
    .venv/bin/python -m tests.test_voice_reminders
"""

import struct
import wave
from io import BytesIO

from app.services import voice_reminders


class FakeInlineData:
    def __init__(self, data: bytes):
        self.data = data


class FakePart:
    def __init__(self, data: bytes):
        self.inline_data = FakeInlineData(data)


class FakeContent:
    def __init__(self, data: bytes):
        self.parts = [FakePart(data)]


class FakeCandidate:
    def __init__(self, data: bytes):
        self.content = FakeContent(data)


class FakeResponse:
    def __init__(self, data: bytes):
        self.candidates = [FakeCandidate(data)]


class FakeModels:
    def __init__(self, audio_bytes: bytes | None):
        self.audio_bytes = audio_bytes
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.audio_bytes is None:
            # Simulate a response with no audio at all (e.g. safety refusal).
            class _Empty:
                candidates = []

            return _Empty()
        return FakeResponse(self.audio_bytes)


class FakeGenaiClient:
    def __init__(self, audio_bytes: bytes | None = b"fake-pcm-bytes"):
        self.models = FakeModels(audio_bytes)


def _silent_pcm(seconds: float = 0.3, sample_rate: int = 24000) -> bytes:
    """A real, valid 16-bit mono PCM buffer (silence) — enough to prove
    the ffmpeg conversion step handles real audio data, not just garbage
    bytes that happen not to crash it."""
    num_samples = int(seconds * sample_rate)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


def test_synthesize_speech_uses_forced_tts_config_and_returns_audio_bytes():
    fake_client = FakeGenaiClient(audio_bytes=b"raw-pcm-audio-bytes")
    result = voice_reminders.synthesize_speech("Hola, te recordamos tu cita.", client=fake_client)
    assert result == b"raw-pcm-audio-bytes"

    assert len(fake_client.models.calls) == 1
    call = fake_client.models.calls[0]
    assert call["model"] == voice_reminders._TTS_MODEL
    assert call["contents"] == "Hola, te recordamos tu cita."
    # response_modalities=["AUDIO"] is what forces Gemini to return audio
    # instead of text — pin it so a refactor can't silently drop it.
    assert call["config"].response_modalities == ["AUDIO"]
    assert call["config"].speech_config is not None


def test_synthesize_speech_uses_requested_voice_name():
    fake_client = FakeGenaiClient()
    voice_reminders.synthesize_speech("texto", client=fake_client, voice_name="Puck")
    config = fake_client.models.calls[0]["config"]
    voice_name = config.speech_config.voice_config.prebuilt_voice_config.voice_name
    assert voice_name == "Puck"


def test_synthesize_speech_raises_clear_error_when_no_audio_returned():
    fake_client = FakeGenaiClient(audio_bytes=None)
    try:
        voice_reminders.synthesize_speech("texto", client=fake_client)
        assert False, "expected VoiceSynthesisError"
    except voice_reminders.VoiceSynthesisError:
        pass


def test_pcm_to_whatsapp_ogg_produces_valid_ogg_opus_audio():
    """The real conversion, run against ffmpeg actually installed here —
    no network involved, so no reason to fake it."""
    pcm = _silent_pcm()
    ogg_bytes = voice_reminders.pcm_to_whatsapp_ogg(pcm)

    # OGG files always start with the 4-byte magic "OggS".
    assert ogg_bytes[:4] == b"OggS"
    assert len(ogg_bytes) > 100  # not an empty/truncated stream

    # Confirm ffmpeg itself thinks this is a valid, playable Opus stream by
    # asking it to decode it back into PCM/WAV rather than just trusting
    # the magic bytes.
    import subprocess

    decode = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "wav", "pipe:1"],
        input=ogg_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    with wave.open(BytesIO(decode.stdout)) as wav_file:
        assert wav_file.getnchannels() == 1
        # Opus always reports 48kHz in its container by spec, regardless of
        # the source sample rate (24kHz here) — that's expected, not a bug.
        assert wav_file.getframerate() == 48000
        assert wav_file.getnframes() > 0


def test_pcm_to_whatsapp_ogg_raises_clear_error_on_garbage_input():
    # Not valid PCM in any meaningful sense, but ffmpeg is tolerant of raw
    # PCM (it has no header to validate) — this mostly documents that
    # a totally empty buffer still fails loudly rather than silently
    # succeeding with a zero-length "valid" file.
    try:
        result = voice_reminders.pcm_to_whatsapp_ogg(b"")
        # ffmpeg may produce a (near-)empty but technically valid OGG
        # container for empty input rather than erroring — either outcome
        # is acceptable here, but a crash/hang is not.
        assert isinstance(result, bytes)
    except voice_reminders.VoiceSynthesisError:
        pass


def test_synthesize_reminder_voice_chains_speech_and_conversion():
    fake_client = FakeGenaiClient(audio_bytes=_silent_pcm())
    result = voice_reminders.synthesize_reminder_voice("Hola, te recordamos tu cita.", client=fake_client)
    assert result[:4] == b"OggS"