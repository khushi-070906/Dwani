"""
Benchmark script for the semantic translation cache -- Section 8.2 of the
paper. Feeds a list of already-transcribed sentences through the
translation step for a set of target languages, twice: once through a fresh
NoOpCache (today's always-translate behavior) and once through SemanticCache
-- then reports the four metrics called out in the proposal:

    - cache hit rate
    - translation time reduction (wall-clock, cache run vs no-cache run)
    - CPU usage (average %, sampled via psutil during each run)
    - memory overhead (peak RSS delta during each run, via psutil)

This is scoped to the translation step only, deliberately: sentences come in
as text (--sentences-file), not audio, so these numbers aren't diluted by
faster-whisper's own latency/variance, and the runs are directly comparable
(exact same sentences, exact same target languages, only the cache differs).
For an end-to-end (audio-in) accuracy comparison against reference
translations, see evaluate_accuracy.py -- a separate concern (WER/BLEU/chrF)
from what this script measures.

-----------------------------------------------------------------------------
1. Prepare a sentences file
-----------------------------------------------------------------------------

A JSON list of strings -- ideally drawn from (or representative of) a real
lecture transcript. Ordinary lectures repeat phrasing more than invented
test sentences do ("as I mentioned earlier", recapping a slide title,
restating a definition), so a synthetic list will understate the real hit
rate you'd see live. See sample_sentences.json for the shape and a few
illustrative near-duplicates.

-----------------------------------------------------------------------------
2. Install
-----------------------------------------------------------------------------

    pip install -r requirements.txt -r requirements-cache.txt psutil --break-system-packages

Add --nllb-model-dir to translate with the real NLLB-200 backend instead of
FakeTranslationBackend -- see backends.py for the one-time CTranslate2
conversion step.

-----------------------------------------------------------------------------
3. Run
-----------------------------------------------------------------------------

    python benchmark_semantic_cache.py \
        --sentences-file sample_sentences.json \
        --target-langs hi,fr \
        --nllb-model-dir nllb-200-ct2

Omitting --nllb-model-dir runs FakeTranslationBackend instead. That still
exercises and times the real caching logic (embedding + similarity search),
so the hit-rate number is real -- but FakeTranslationBackend returns
instantly regardless of caching, so translation-time-reduction, CPU, and
memory numbers are NOT meaningful in that mode. The script says so again at
the end of its output; re-run with --nllb-model-dir for numbers that belong
in the paper.

-----------------------------------------------------------------------------
Output
-----------------------------------------------------------------------------

Prints a comparison table (matches Table 8.2's layout) and writes
--output (default benchmark_results.json) with the full numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import threading
import time
from pathlib import Path

from pipeline import FakeTranslationBackend, NoOpCache
from translation_cache import SemanticCache


def load_sentences(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"{path} should contain a non-empty JSON list of strings")
    return [str(s) for s in data]


def build_translator(nllb_model_dir: str | None, presenter_language: str):
    if nllb_model_dir:
        from backends import RealNLLBBackend

        return RealNLLBBackend(model_dir=nllb_model_dir, source_lang=presenter_language)
    print(
        "WARNING: no --nllb-model-dir given -- using FakeTranslationBackend. The hit-rate "
        "number below is real; translation-time-reduction, CPU, and memory numbers are not "
        "meaningful in this mode (see module docstring).",
        file=sys.stderr,
    )
    return FakeTranslationBackend()


async def run_once(sentences: list[str], target_langs: list[str], translator, cache) -> dict:
    """Runs every (sentence, lang) pair once, in order, through
    cache.get() -> (miss: translator.translate() + cache.put()) -- the same
    sequence Pipeline._process_segment follows per segment/language, minus
    AudioSegmenter/ASR (out of scope here, see module docstring)."""
    translate_seconds: list[float] = []
    hits = 0
    misses = 0

    start = time.monotonic()
    for sentence in sentences:
        for lang in target_langs:
            cached = await cache.get(sentence, lang)
            if cached is not None:
                hits += 1
                continue
            misses += 1
            t0 = time.monotonic()
            translated = await translator.translate(sentence, lang)
            translate_seconds.append(time.monotonic() - t0)
            await cache.put(sentence, lang, translated)
    total_seconds = time.monotonic() - start

    return {
        "hits": hits,
        "misses": misses,
        "hit_rate": hits / (hits + misses) if (hits + misses) else 0.0,
        "total_seconds": total_seconds,
        "mean_translate_seconds": statistics.mean(translate_seconds) if translate_seconds else 0.0,
        "translate_call_count": len(translate_seconds),
    }


def _sample_resource_usage(fn):
    """Runs the zero-arg coroutine factory `fn` under a psutil sampler on a
    background thread, since psutil's cpu_percent() only means something
    when polled repeatedly over an interval, and asyncio.run() blocks the
    calling thread for the whole run. Returns (fn's result, usage dict)."""
    import psutil

    process = psutil.Process()
    process.cpu_percent()  # first call always returns 0.0 -- primes the internal counter
    baseline_rss = process.memory_info().rss

    samples: list[float] = []
    peak_rss = baseline_rss
    stop = threading.Event()

    def sampler():
        nonlocal peak_rss
        while not stop.is_set():
            samples.append(process.cpu_percent())
            peak_rss = max(peak_rss, process.memory_info().rss)
            stop.wait(0.05)

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    try:
        result = asyncio.run(fn())
    finally:
        stop.set()
        thread.join()

    usage = {
        "avg_cpu_percent": statistics.mean(samples) if samples else 0.0,
        "peak_rss_delta_mb": (peak_rss - baseline_rss) / (1024 * 1024),
    }
    return result, usage


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the semantic translation cache (Section 8.2): hit rate, latency, CPU, memory."
    )
    parser.add_argument("--sentences-file", required=True, help="JSON list of already-transcribed sentences, e.g. sample_sentences.json")
    parser.add_argument("--target-langs", default="hi,fr", help="Comma-separated target language codes (default: hi,fr)")
    parser.add_argument("--presenter-language", default="en")
    parser.add_argument("--nllb-model-dir", default=None, help="Omit to use FakeTranslationBackend (hit-rate numbers only -- see module docstring)")
    parser.add_argument("--similarity-threshold", type=float, default=0.92)
    parser.add_argument("--output", default="benchmark_results.json")
    args = parser.parse_args()

    sentences = load_sentences(Path(args.sentences_file))
    target_langs = [lang.strip() for lang in args.target_langs.split(",") if lang.strip()]

    try:
        import psutil  # noqa: F401
    except ImportError:
        raise SystemExit("psutil is required for this script: pip install psutil --break-system-packages")

    print(f"Loaded {len(sentences)} sentences, {len(target_langs)} target language(s): {target_langs}\n")

    print("Run 1/2: no cache ...")
    translator_nocache = build_translator(args.nllb_model_dir, args.presenter_language)
    no_cache_result, no_cache_usage = _sample_resource_usage(
        lambda: run_once(sentences, target_langs, translator_nocache, NoOpCache())
    )

    print("Run 2/2: semantic cache ...")
    translator_cached = build_translator(args.nllb_model_dir, args.presenter_language)
    cache = SemanticCache(similarity_threshold=args.similarity_threshold)
    cached_result, cached_usage = _sample_resource_usage(
        lambda: run_once(sentences, target_langs, translator_cached, cache)
    )

    time_reduction_pct = (
        100.0 * (no_cache_result["total_seconds"] - cached_result["total_seconds"]) / no_cache_result["total_seconds"]
        if no_cache_result["total_seconds"] > 0
        else 0.0
    )

    results = {
        "no_cache": {**no_cache_result, **no_cache_usage},
        "semantic_cache": {**cached_result, **cached_usage},
        "translation_time_reduction_percent": round(time_reduction_pct, 2),
        "cache_hit_rate": round(cached_result["hit_rate"], 4),
    }

    print("\n--- Results (Table 8.2) ---")
    print(f"{'metric':<32}{'no cache':>16}{'semantic cache':>18}")
    print(f"{'translate() calls':<32}{no_cache_result['translate_call_count']:>16}{cached_result['translate_call_count']:>18}")
    print(f"{'cache hit rate':<32}{'n/a':>16}{cached_result['hit_rate']:>18.2%}")
    print(f"{'total time (s)':<32}{no_cache_result['total_seconds']:>16.3f}{cached_result['total_seconds']:>18.3f}")
    print(f"{'avg CPU %':<32}{no_cache_usage['avg_cpu_percent']:>16.1f}{cached_usage['avg_cpu_percent']:>18.1f}")
    print(f"{'peak RSS delta (MB)':<32}{no_cache_usage['peak_rss_delta_mb']:>16.1f}{cached_usage['peak_rss_delta_mb']:>18.1f}")
    print(f"\nTranslation time reduction: {time_reduction_pct:.1f}%")

    if args.nllb_model_dir is None:
        print(
            "\n(FakeTranslationBackend was used -- hit rate above is real, but time/CPU/memory "
            "numbers are not meaningful. Re-run with --nllb-model-dir for numbers to put in the paper.)"
        )

    Path(args.output).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
    