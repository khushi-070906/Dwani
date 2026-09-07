"""
evaluate_accuracy.py

End-to-end (audio-in) accuracy AND latency evaluation for the LDST pipeline
-- referenced from benchmark_semantic_cache.py's module docstring as "a
separate concern (WER/BLEU/chrF) from what this script measures".

Where benchmark_semantic_cache.py isolates the translation step alone (text
in, cache vs no-cache), this script runs a real presenter recording through
the *actual* Pipeline -- AudioSegmenter's real VAD boundaries, the real (or
Fake) ASR backend, the real (or Fake) translation backend, glossary/cache/
ITDE wiring if you want it -- exactly as server.py would, and reports:

    ACCURACY
        - WER (word error rate) of the concatenated ASR transcript against
          a reference transcript you supply
        - BLEU and chrF (via sacrebleu, if installed) of the concatenated
          captions against a reference translation, per target language

    LATENCY ("delay")
        - ASR latency per segment (model-only: time inside asr.transcribe())
        - Translator latency per call (model-only: time inside
          translator.translate() -- shorter than reality on a cache/ITDE
          hit, since those skip the real model call entirely)
        - End-to-end per-language delay: wall-clock time from the moment a
          segment's audio closes (i.e. the presenter stops talking) to the
          moment that language's caption is broadcast. This is measured at
          the broadcast() call itself, so it is correct regardless of
          whether a cache, glossary, or ITDE sits in between -- it's the
          number that actually matters to an attendee reading captions.

-----------------------------------------------------------------------------
1. Prepare inputs
-----------------------------------------------------------------------------

    - A WAV recording of the presenter (mono or stereo, any sample rate --
      resampled to 16kHz automatically). 3-5 minutes of REAL talk audio
      (not read-aloud script) is enough to be meaningful; keep silence at
      the start/end natural, don't trim it, since that's part of what
      AudioSegmenter has to handle too.
    - A reference transcript: run the clip through Whisper once, then
      hand-correct the output. That corrected text is ASR ground truth.
    - Reference translation(s), one text file per target language you care
      about: either a fluent speaker's translation of the reference
      transcript (stricter -- measures true translation quality) or NLLB's
      own output hand-corrected by a fluent speaker (measures whether your
      pipeline matches its own model's best-case output). Pick one
      convention and stay consistent across clips so numbers are
      comparable over time.

-----------------------------------------------------------------------------
2. Install
-----------------------------------------------------------------------------

    pip install sacrebleu --break-system-packages

Optional: without sacrebleu, WER (dependency-free, computed here directly)
still works; BLEU/chrF are skipped with a warning.

--semantic-cache / --itde additionally require sentence-transformers
(pip install sentence-transformers --break-system-packages) -- same
dependency SemanticCache itself needs, see translation_cache.py.

Add --whisper-model / --nllb-model-dir to run the real backends (see
backends.py's Setup section for the one-time NLLB conversion). Omitting
either runs FakeASRBackend / FakeTranslationBackend instead -- useful for
smoke-testing the harness itself, but the accuracy and latency numbers in
that mode are meaningless (this script says so loudly).

-----------------------------------------------------------------------------
3. Run
-----------------------------------------------------------------------------

    python evaluate_accuracy.py \
        --audio-file talk.wav \
        --reference-transcript talk_reference.txt \
        --reference-translation hi=talk_reference_hi.txt \
        --reference-translation fr=talk_reference_fr.txt \
        --whisper-model small \
        --nllb-model-dir nllb-200-ct2 \
        --presenter-language en

Add --glossary-file glossary.json, --semantic-cache, or --itde to evaluate
those configurations instead of the plain pipeline -- run the same clip
once per configuration you want to compare (plain vs +cache vs +ITDE) and
diff the output JSON files; that A/B is more informative than any single
run.

-----------------------------------------------------------------------------
Output
-----------------------------------------------------------------------------

Prints a summary table and writes --output (default
evaluate_accuracy_results.json) with full per-segment detail for
reproducing tables/plots later.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

from pipeline import AudioSegmenter, FakeASRBackend, FakeTranslationBackend, Pipeline


# ---------------------------------------------------------------------------
# Audio loading (dependency-free: stdlib `wave`, PCM only -- matches
# backends.py's resample_linear philosophy of trading a little flexibility
# for zero extra dependencies. If your recorder produces mp3/m4a, convert
# to WAV once with any tool you already have, e.g. `ffmpeg -i in.m4a out.wav`.)
# ---------------------------------------------------------------------------

def load_wav_mono_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_width == 1:
        # 8-bit WAV is unsigned
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise SystemExit(
            f"{path}: unsupported sample width {sample_width * 8}-bit. "
            "Re-export as 16-bit PCM WAV (the overwhelmingly common case)."
        )

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1).astype(np.float32)

    if frame_rate != 16_000:
        # Same linear-interpolation resampler backends.py uses at inference
        # time, reused here rather than adding scipy/librosa as a dependency
        # just for this script.
        from backends import resample_linear

        samples = resample_linear(samples, frame_rate, 16_000)

    return samples


# ---------------------------------------------------------------------------
# Word Error Rate -- dependency-free (Levenshtein over word tokens)
# ---------------------------------------------------------------------------

def _tokenize(text: str, case_sensitive: bool) -> list[str]:
    import re

    if not case_sensitive:
        text = text.lower()
    return re.findall(r"\w+(?:'\w+)?", text, re.UNICODE)


def word_error_rate(reference: str, hypothesis: str, case_sensitive: bool = False) -> dict:
    """Standard WER: (substitutions + deletions + insertions) / len(reference).
    Returns a dict with the rate and the raw op counts, so a low/high WER
    can be broken down (mostly deletions -> ASR is dropping words; mostly
    insertions -> ASR is hallucinating/repeating) rather than just reported
    as one opaque number.
    """
    ref = _tokenize(reference, case_sensitive)
    hyp = _tokenize(hypothesis, case_sensitive)
    n, m = len(ref), len(hyp)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    i, j = n, m
    subs = dels = ins = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            subs += 1
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1

    rate = (subs + dels + ins) / n if n else (1.0 if hyp else 0.0)
    return {
        "wer": rate,
        "substitutions": subs,
        "deletions": dels,
        "insertions": ins,
        "reference_words": n,
        "hypothesis_words": m,
    }


# ---------------------------------------------------------------------------
# Timing wrappers -- transparent decorators around the real ASR/translator
# so this script measures actual model call latency without pipeline.py or
# backends.py needing any instrumentation hooks added for it.
# ---------------------------------------------------------------------------

class _TimedASR:
    def __init__(self, inner):
        self._inner = inner
        self.records: list[dict] = []
        # Wall-clock timestamp of the most recent transcribe() call start.
        # _process_segment (pipeline.py) always calls asr.transcribe(segment)
        # as the very first thing it does for a newly closed segment, so
        # this timestamp is, for all practical purposes, "the moment the
        # presenter's utterance ended" -- the natural zero point for
        # end-to-end delay below.
        self.last_start: float | None = None

    async def transcribe(self, segment) -> str:
        t0 = time.monotonic()
        self.last_start = t0
        text = await self._inner.transcribe(segment)
        self.records.append({
            "segment_duration_seconds": segment.duration_seconds,
            "elapsed_seconds": time.monotonic() - t0,
        })
        return text


class _TimedTranslator:
    def __init__(self, inner):
        self._inner = inner
        self.records: list[dict] = []

    async def translate(self, text: str, target_lang: str) -> str:
        t0 = time.monotonic()
        translated = await self._inner.translate(text, target_lang)
        self.records.append({"lang": target_lang, "elapsed_seconds": time.monotonic() - t0})
        return translated


# ---------------------------------------------------------------------------
# Backend / config construction -- mirrors server.py's --itde/--glossary/
# --semantic-cache wiring so the configuration you evaluate here is the
# same shape as what you'd actually deploy.
# ---------------------------------------------------------------------------

def build_asr(whisper_model: str | None, presenter_language: str, beam_size: int, initial_prompt: str | None):
    if whisper_model:
        from backends import RealWhisperBackend

        return RealWhisperBackend(
            model_size=whisper_model, language=presenter_language,
            beam_size=beam_size, initial_prompt=initial_prompt,
        )
    print(
        "WARNING: no --whisper-model given -- using FakeASRBackend. WER and ASR-latency "
        "numbers below are meaningless (see module docstring); this run only smoke-tests "
        "the harness.",
        file=sys.stderr,
    )
    return FakeASRBackend(default_transcript="[no --whisper-model given]")


def build_translator_stack(nllb_model_dir, presenter_language, glossary_file, semantic_cache,
                            semantic_cache_threshold, itde, beam_size):
    if nllb_model_dir:
        from backends import RealNLLBBackend

        base = RealNLLBBackend(model_dir=nllb_model_dir, source_lang=presenter_language, beam_size=beam_size)
    else:
        print(
            "WARNING: no --nllb-model-dir given -- using FakeTranslationBackend. BLEU/chrF and "
            "MT-latency numbers below are meaningless (see module docstring); this run only "
            "smoke-tests the harness.",
            file=sys.stderr,
        )
        base = FakeTranslationBackend()

    glossary = None
    if glossary_file:
        from glossary import Glossary

        glossary = Glossary.load(glossary_file)

    if itde:
        from decision_engine import IntelligentTranslationDecisionEngine
        from translation_cache import SemanticCache
        from glossary import Glossary

        cache = SemanticCache(similarity_threshold=semantic_cache_threshold)
        translator = IntelligentTranslationDecisionEngine(
            inner=base, cache=cache, glossary=glossary or Glossary()
        )
        return translator, None, cache  # ITDE owns caching -- Pipeline's own cache stays None

    if glossary is not None:
        from glossary import GlossaryAwareTranslationBackend

        translator = GlossaryAwareTranslationBackend(base, glossary)
    else:
        translator = base

    pipeline_cache = None
    if semantic_cache:
        from translation_cache import SemanticCache

        pipeline_cache = SemanticCache(similarity_threshold=semantic_cache_threshold)

    return translator, pipeline_cache, pipeline_cache


# ---------------------------------------------------------------------------
# Main evaluation run
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _latency_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": _percentile(values, 0.95),
        "max": max(values),
    }


async def run_evaluation(args) -> dict:
    samples = load_wav_mono_16k(Path(args.audio_file))
    audio_duration = len(samples) / 16_000
    print(f"Loaded {args.audio_file}: {audio_duration:.1f}s of audio at 16kHz mono.\n")

    ref_translation_paths = {}
    for item in args.reference_translation:
        if "=" not in item:
            raise SystemExit(f"--reference-translation must be LANG=PATH, got: {item!r}")
        lang, path = item.split("=", 1)
        ref_translation_paths[lang.strip()] = Path(path)

    target_langs = list(dict.fromkeys(
        ([l.strip() for l in args.target_langs.split(",") if l.strip()] if args.target_langs else [])
        + list(ref_translation_paths.keys())
    ))
    if not target_langs:
        raise SystemExit(
            "No target languages given -- pass --target-langs and/or one or more "
            "--reference-translation lang=path."
        )

    reference_transcript = Path(args.reference_transcript).read_text(encoding="utf-8").strip()
    reference_translations = {
        lang: path.read_text(encoding="utf-8").strip() for lang, path in ref_translation_paths.items()
    }

    asr_initial_prompt = None
    if args.asr_glossary_prompt and args.glossary_file:
        from glossary import Glossary

        asr_initial_prompt = Glossary.load(args.glossary_file).as_whisper_prompt()
    elif args.asr_glossary_prompt:
        print("WARNING: --asr-glossary-prompt given without --glossary-file -- nothing to bias with.", file=sys.stderr)

    asr = build_asr(args.whisper_model, args.presenter_language, args.asr_beam_size, asr_initial_prompt)
    translator, pipeline_cache, stats_source = build_translator_stack(
        args.nllb_model_dir, args.presenter_language, args.glossary_file,
        args.semantic_cache, args.semantic_cache_threshold, args.itde, args.nllb_beam_size,
    )

    timed_asr = _TimedASR(asr)
    timed_translator = _TimedTranslator(translator)

    translations_by_lang: dict[str, list[str]] = {lang: [] for lang in target_langs}
    delay_by_lang: dict[str, list[float]] = {lang: [] for lang in target_langs}

    async def capture_broadcast(lang: str, text: str, is_final: bool = True):
        now = time.monotonic()
        if timed_asr.last_start is not None:
            delay_by_lang.setdefault(lang, []).append(now - timed_asr.last_start)
        translations_by_lang.setdefault(lang, []).append(text)

    segmenter = AudioSegmenter(
        sample_rate=16_000,
        energy_threshold=args.energy_threshold,
        min_voiced_seconds=args.min_voiced_seconds,
        min_silence_seconds=args.min_silence_seconds,
        max_segment_seconds=args.max_segment_seconds,
    )
    pipeline = Pipeline(
        asr=timed_asr,
        translator=timed_translator,
        broadcast=capture_broadcast,
        subscribed_languages=lambda: target_langs,
        segmenter=segmenter,
        cache=pipeline_cache,
    )

    chunk_size = max(1, int(16_000 * args.chunk_ms / 1000))
    hypothesis_segments: list[str] = []
    for i in range(0, len(samples), chunk_size):
        chunk = samples[i:i + chunk_size]
        result = await pipeline.handle_audio_chunk(chunk)
        if result is not None:
            hypothesis_segments.append(result)
    trailing = await pipeline.flush()
    if trailing is not None:
        hypothesis_segments.append(trailing)

    if not hypothesis_segments:
        print(
            "WARNING: AudioSegmenter never closed a single segment -- either the clip is "
            "silent/too quiet for --energy-threshold, or it's shorter than --min-voiced-seconds. "
            "Try a louder/longer clip or lower --energy-threshold.",
            file=sys.stderr,
        )

    hypothesis_transcript = " ".join(hypothesis_segments).strip()

    # -- accuracy: ASR ----------------------------------------------------
    wer_result = word_error_rate(reference_transcript, hypothesis_transcript, args.case_sensitive)

    # -- accuracy: translation ---------------------------------------------
    try:
        import sacrebleu

        have_sacrebleu = True
    except ImportError:
        have_sacrebleu = False
        print(
            "\nNOTE: sacrebleu not installed -- BLEU/chrF skipped (pip install sacrebleu "
            "--break-system-packages). WER above is unaffected.",
            file=sys.stderr,
        )

    translation_scores = {}
    for lang in target_langs:
        hyp_text = " ".join(translations_by_lang.get(lang, [])).strip()
        entry = {"hypothesis_text": hyp_text}
        if lang in reference_translations:
            ref_text = reference_translations[lang]
            entry["reference_text"] = ref_text
            if have_sacrebleu and hyp_text:
                entry["bleu"] = sacrebleu.corpus_bleu([hyp_text], [[ref_text]]).score
                entry["chrf"] = sacrebleu.corpus_chrf([hyp_text], [[ref_text]]).score
            elif have_sacrebleu:
                entry["bleu"] = None
                entry["chrf"] = None
        translation_scores[lang] = entry

    # -- latency ------------------------------------------------------------
    asr_latencies = [r["elapsed_seconds"] for r in timed_asr.records]
    asr_summary = _latency_summary(asr_latencies)
    realtime_factors = [
        r["segment_duration_seconds"] / r["elapsed_seconds"]
        for r in timed_asr.records if r["elapsed_seconds"] > 0
    ]
    asr_summary["mean_realtime_factor"] = statistics.mean(realtime_factors) if realtime_factors else 0.0

    mt_model_only_summary = {}
    for lang in target_langs:
        lang_latencies = [r["elapsed_seconds"] for r in timed_translator.records if r["lang"] == lang]
        mt_model_only_summary[lang] = _latency_summary(lang_latencies)

    end_to_end_delay_summary = {lang: _latency_summary(delay_by_lang.get(lang, [])) for lang in target_langs}

    cache_stats = None
    if stats_source is not None and hasattr(stats_source, "stats"):
        s = stats_source.stats
        cache_stats = {
            "hit_rate": s.hit_rate,
            "hits": s.hits,
            "misses": s.misses,
            "cross_session_hits": s.cross_session_hits,
        }
    elif args.itde:
        cache_stats = {"note": "ITDE veto/threshold stats -- see itde_veto_rate/itde_threshold_by_lang below"}

    results = {
        "audio_file": str(args.audio_file),
        "audio_duration_seconds": audio_duration,
        "segments_detected": len(hypothesis_segments),
        "config": {
            "whisper_model": args.whisper_model,
            "nllb_model_dir": args.nllb_model_dir,
            "presenter_language": args.presenter_language,
            "target_langs": target_langs,
            "glossary_file": args.glossary_file,
            "asr_beam_size": args.asr_beam_size,
            "nllb_beam_size": args.nllb_beam_size,
            "asr_glossary_prompt": args.asr_glossary_prompt,
            "semantic_cache": args.semantic_cache,
            "semantic_cache_threshold": args.semantic_cache_threshold,
            "itde": args.itde,
            "chunk_ms": args.chunk_ms,
            "segmenter": {
                "energy_threshold": args.energy_threshold,
                "min_voiced_seconds": args.min_voiced_seconds,
                "min_silence_seconds": args.min_silence_seconds,
                "max_segment_seconds": args.max_segment_seconds,
            },
        },
        "asr": {
            "wer": wer_result,
            "hypothesis_transcript": hypothesis_transcript,
            "reference_transcript": reference_transcript,
            "latency_seconds": asr_summary,
        },
        "translation": translation_scores,
        "latency": {
            "asr_model_only_seconds": asr_summary,
            "mt_model_only_seconds": mt_model_only_summary,
            "end_to_end_delay_seconds": end_to_end_delay_summary,
        },
        "cache_stats": cache_stats,
        "sacrebleu_available": have_sacrebleu,
        "alignment_note": (
            "end_to_end_delay_seconds is measured at the broadcast() call itself, so it is "
            "accurate regardless of cache/glossary/ITDE config. mt_model_only_seconds only "
            "includes calls that actually reached translator.translate() -- fewer than "
            "segments_detected whenever a cache/ITDE hit skipped the real model call."
        ),
    }

    if args.itde:
        results["itde_veto_rate"] = stats_source.veto_rate() if hasattr(stats_source, "veto_rate") else None

    return results


def _print_summary(results: dict) -> None:
    print("\n--- Accuracy ---")
    wer = results["asr"]["wer"]
    print(f"ASR WER: {wer['wer']:.2%}  "
          f"(S={wer['substitutions']} D={wer['deletions']} I={wer['insertions']} / N={wer['reference_words']})")

    for lang, entry in results["translation"].items():
        if "bleu" in entry and entry["bleu"] is not None:
            print(f"  [{lang}] BLEU={entry['bleu']:.2f}  chrF={entry['chrf']:.2f}")
        elif "reference_text" in entry:
            print(f"  [{lang}] (BLEU/chrF unavailable -- install sacrebleu)")
        else:
            print(f"  [{lang}] (no reference translation given -- latency only)")

    print("\n--- Latency (delay) ---")
    asr_lat = results["latency"]["asr_model_only_seconds"]
    print(f"ASR latency:  mean={asr_lat['mean']:.3f}s  median={asr_lat['median']:.3f}s  "
          f"p95={asr_lat['p95']:.3f}s  realtime factor={asr_lat['mean_realtime_factor']:.2f}x  "
          f"(n={asr_lat['count']} segments)")

    for lang in results["config"]["target_langs"]:
        mt = results["latency"]["mt_model_only_seconds"][lang]
        e2e = results["latency"]["end_to_end_delay_seconds"][lang]
        print(f"  [{lang}] translator-only: mean={mt['mean']:.3f}s p95={mt['p95']:.3f}s (n={mt['count']})   "
              f"end-to-end delay: mean={e2e['mean']:.3f}s median={e2e['median']:.3f}s "
              f"p95={e2e['p95']:.3f}s (n={e2e['count']})")

    if results["cache_stats"] and "hit_rate" in results["cache_stats"]:
        cs = results["cache_stats"]
        print(f"\nCache hit rate: {cs['hit_rate']:.2%} ({cs['hits']} hits / {cs['misses']} misses, "
              f"{cs['cross_session_hits']} cross-session)")
    if results.get("itde_veto_rate") is not None:
        print(f"ITDE veto rate: {results['itde_veto_rate']:.2%}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end (audio-in) accuracy (WER/BLEU/chrF) and latency evaluation for the LDST pipeline."
    )
    parser.add_argument("--audio-file", required=True, help="WAV recording of the presenter (any sample rate/channels; resampled to 16kHz mono)")
    parser.add_argument("--reference-transcript", required=True, help="Text file with the hand-corrected correct transcript for --audio-file")
    parser.add_argument("--reference-translation", action="append", default=[], metavar="LANG=PATH",
                         help="Reference translation file for a target language, e.g. hi=ref_hi.txt. Repeatable.")
    parser.add_argument("--target-langs", default=None,
                         help="Extra comma-separated target languages to translate+time without a reference "
                              "(latency-only). Languages from --reference-translation are always included.")
    parser.add_argument("--presenter-language", default="en")
    parser.add_argument("--whisper-model", default=None, help="Omit to use FakeASRBackend (harness smoke-test only, see docstring)")
    parser.add_argument("--nllb-model-dir", default=None, help="Omit to use FakeTranslationBackend (harness smoke-test only, see docstring)")
    parser.add_argument("--glossary-file", default=None)
    parser.add_argument("--asr-beam-size", type=int, default=5, help="faster-whisper beam size (default: 5)")
    parser.add_argument("--nllb-beam-size", type=int, default=4, help="ctranslate2 translation beam size (default: 4)")
    parser.add_argument("--asr-glossary-prompt", action="store_true",
                         help="Bias Whisper decoding with --glossary-file's terms (Glossary.as_whisper_prompt)")
    parser.add_argument("--semantic-cache", action="store_true")
    parser.add_argument("--semantic-cache-threshold", type=float, default=0.92)
    parser.add_argument("--itde", action="store_true", help="Use IntelligentTranslationDecisionEngine instead of plain caching")
    parser.add_argument("--chunk-ms", type=int, default=100, help="Simulated streaming chunk size fed to AudioSegmenter (default: 100ms)")
    parser.add_argument("--energy-threshold", type=float, default=0.02, help="AudioSegmenter RMS voice threshold (default matches pipeline.py)")
    parser.add_argument("--min-voiced-seconds", type=float, default=0.3)
    parser.add_argument("--min-silence-seconds", type=float, default=0.6)
    parser.add_argument("--max-segment-seconds", type=float, default=15.0)
    parser.add_argument("--case-sensitive", action="store_true", help="Don't lowercase before computing WER")
    parser.add_argument("--output", default="evaluate_accuracy_results.json")
    args = parser.parse_args()

    results = asyncio.run(run_evaluation(args))
    _print_summary(results)

    Path(args.output).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {args.output}")

    if args.whisper_model is None or args.nllb_model_dir is None:
        print(
            "\n(Fake backend(s) were used -- re-run with --whisper-model/--nllb-model-dir for "
            "numbers that belong in the paper; see module docstring.)"
        )


if __name__ == "__main__":
    main()
