"""
PROTOTYPE — not wired into the real reminder pipeline. appointments.send_reminder
still only sends text; nothing here changes that. This exists so GRIP can
listen to a sample and decide whether a voice-note reminder is worth
pursuing before any of that work happens.

Turns an appointment reminder into a WhatsApp-playable voice note, using
the same Gemini account/API key the bot already calls for everything else
(no new TTS vendor to integrate or bill separately), then re-encodes the
result into the exact audio format WhatsApp requires for a message to
render as a voice-note bubble (with the waveform and play button) instead
of a generic file attachment.

Two independent steps, kept separate so each is inspectable/testable on
its own while prototyping:

  1. synthesize_speech(text)   -> raw PCM bytes, straight from Gemini's TTS
  2. pcm_to_whatsapp_ogg(pcm)  -> OGG/Opus bytes, via ffmpeg

Same safety posture as gemini_client.py: this module never decides what
the message SAYS. The text is still produced by
appointments.build_reminder_message's fixed template — Gemini here only
renders already-fixed text as audio, with no freedom over content.

Requires network access to Google's API and a real GEMINI_API_KEY to
actually run — this sandbox's egress policy blocks
generativelanguage.googleapis.com, so this was built and tested with a
fake client (tests/test_voice_reminders.py) plus a real, local run of the
ffmpeg conversion step (which needs no network). GRIP needs to try the
full path themselves, e.g. by opening a patient's voice-reminder preview
in /crm once this is deployed to Railway (which does have outbound
network access).
"""

import re
import subprocess

from google import genai
from google.genai import types

from app.core.config import get_settings

# Gemini's TTS models return raw audio as 16-bit signed little-endian PCM,
# mono, 24kHz (per Google's docs) — ffmpeg needs to be told this explicitly
# since raw PCM carries no header describing its own format.
_PCM_SAMPLE_RATE = 24000
_PCM_CHANNELS = 1
_PCM_FFMPEG_FORMAT = "s16le"

_TTS_MODEL = "gemini-2.5-flash-preview-tts"

# One of Gemini's prebuilt voices — "Kore" reads as calm/neutral, which
# fits an appointment reminder better than an upbeat or dramatic voice.
# Swap this (or make it a tenant setting) if GRIP wants a different tone;
# see Gemini's docs for the full prebuilt-voice list.
DEFAULT_VOICE_NAME = "Kore"

# Gemini's native TTS models are "controllable": a natural-language style
# instruction placed before the actual content steers HOW it's read
# (accent, tone, pace) without being read aloud itself. Without this, the
# model defaults to a generic/Mexican-leaning Spanish accent, which isn't
# what GRIP's Dominican patients are used to hearing. This is a prompt,
# not a guarantee — worth listening to a few samples after any wording
# change to confirm it's still landing as intended.
_ACCENT_STYLE_INSTRUCTION = (
    "Lee el siguiente mensaje en voz alta con acento dominicano natural, "
    "cálido y cotidiano, como lo hablaría alguien de Santo Domingo, "
    "República Dominicana. Evita un acento mexicano o neutro. "
    "La palabra \"Grip\" es el nombre del centro y se pronuncia como una "
    "sola palabra en español (rima con \"chip\"), nunca deletreada letra "
    "por letra y nunca en inglés. "
    "El mensaje es: "
)

# Gemini's TTS tends to treat an all-caps word as an acronym and spell it
# out letter by letter ("G-R-I-P") instead of reading it as a word — which
# is wrong here, since GRIP is the center's name, not an acronym. Mixed
# case reads as an ordinary word instead. This ONLY changes what's sent to
# the TTS model — the actual WhatsApp text message (build_reminder_message)
# keeps "GRIP" exactly as written; this rewrite never touches that.
_ALL_CAPS_GRIP_PATTERN = re.compile(r"\bGRIP\b")


def _text_for_speech(text: str) -> str:
    return _ALL_CAPS_GRIP_PATTERN.sub("Grip", text)


class VoiceSynthesisError(Exception):
    """Raised when Gemini's response doesn't contain the audio we asked
    for — fail loudly rather than silently returning empty/garbage audio."""


def synthesize_speech(
    text: str,
    *,
    client: "genai.Client | None" = None,
    voice_name: str = DEFAULT_VOICE_NAME,
) -> bytes:
    """Calls Gemini's text-to-speech model and returns raw PCM audio bytes.
    `client` is injectable so this can be tested with a fake instead of a
    live network call — same pattern as GeminiClient in gemini_client.py."""
    settings = get_settings()
    try:
        genai_client = client or genai.Client(api_key=settings.gemini_api_key)

        response = genai_client.models.generate_content(
            model=_TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                    )
                ),
            ),
        )
    except VoiceSynthesisError:
        raise
    except Exception as exc:
        # Covers everything that isn't "Gemini answered but without audio"
        # below: missing/invalid API key, no network route to Google's API
        # (true in this sandbox — see module docstring), rate limits, etc.
        # All of these are "this environment/config can't reach Gemini
        # right now", not a bug in this request, so the route surfaces
        # them as 502 rather than a raw 500.
        raise VoiceSynthesisError(f"Could not reach Gemini's TTS API: {exc}") from exc

    try:
        return response.candidates[0].content.parts[0].inline_data.data
    except (AttributeError, IndexError, TypeError) as exc:
        raise VoiceSynthesisError(f"Gemini TTS response did not contain audio data: {response!r}") from exc


def pcm_to_whatsapp_ogg(pcm_bytes: bytes) -> bytes:
    """Re-encodes raw PCM into OGG-container/Opus-codec audio — the one
    combination WhatsApp renders as an actual voice-note bubble rather
    than a downloadable file. Uses ffmpeg (already on Railway's build
    image and this sandbox; no new dependency) as a subprocess, piping
    bytes in and out rather than touching disk."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", _PCM_FFMPEG_FORMAT, "-ar", str(_PCM_SAMPLE_RATE), "-ac", str(_PCM_CHANNELS),
                "-i", "pipe:0",
                "-c:a", "libopus",
                "-f", "ogg",
                "pipe:1",
            ],
            input=pcm_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except FileNotFoundError as exc:
        raise VoiceSynthesisError("ffmpeg is not installed/available on this machine.") from exc
    except subprocess.CalledProcessError as exc:
        raise VoiceSynthesisError(f"ffmpeg failed converting PCM to OGG/Opus: {exc.stderr.decode('utf-8', 'replace')}") from exc
    return result.stdout


def synthesize_reminder_voice(text: str, *, client: "genai.Client | None" = None) -> bytes:
    """The full prototype pipeline for one reminder message: fixed text
    (from appointments.build_reminder_message) -> speech -> the exact
    audio format WhatsApp needs to show it as a voice note."""
    styled_text = _ACCENT_STYLE_INSTRUCTION + _text_for_speech(text)
    pcm = synthesize_speech(styled_text, client=client)
    return pcm_to_whatsapp_ogg(pcm)