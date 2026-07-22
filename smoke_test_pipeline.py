"""
Standalone smoke test for Pipeline -- no server, no browser, no network.

Simulates a presenter saying one sentence, with two attendees subscribed to
different languages, and prints exactly what Pipeline would hand off to
broadcast_caption for each. Run directly:

    python smoke_test_pipeline.py
"""

import asyncio

from pipeline import FakeASRBackend, FakeTranslationBackend, Pipeline, make_silence, make_tone


async def fake_broadcast(lang: str, text: str, is_final: bool) -> None:
    print(f"  -> broadcast to [{lang}] subscribers: {text!r} (final={is_final})")


async def main() -> None:
    asr = FakeASRBackend(default_transcript="welcome everyone to the seminar")
    translator = FakeTranslationBackend()
    subscribed = lambda: ["hi", "fr"]  # two attendees, two languages

    pipeline = Pipeline(asr, translator, fake_broadcast, subscribed)

    print("Feeding simulated speech (tone) + a pause (silence)...")
    pipeline.segmenter.push(make_tone(0.5))
    transcript = await pipeline.handle_audio_chunk(make_silence(1.0))
    print(f"Segment closed. Transcript: {transcript!r}")

    print("\nFlushing any trailing audio at session end...")
    trailing = await pipeline.flush()
    print(f"Nothing buffered, flush returned: {trailing!r}")


if __name__ == "__main__":
    asyncio.run(main())