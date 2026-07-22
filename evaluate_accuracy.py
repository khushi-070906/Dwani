"""
Accuracy evaluation -- Section 6.2 of the paper.

Feeds recorded lecture audio through the real ASR/MT pipeline (backends.py)
and scores the output against reference transcripts/translations you
supply, using word error rate (WER) for transcription and BLEU/chrF for
translation. This produces real numbers to drop into Section 8.2 of the
paper -- nothing here is estimated or fabricated; if you don't have
reference audio and translations yet, this script has nothing to measure
until you do.

-----------------------------------------------------------------------------
1. Build a manifest (JSON) describing your test set
-----------------------------------------------------------------------------

See manifest.example.json for the exact shape. In short: a list of
recordings, each with the audio file path, the correct ("reference")
transcript in the presenter's spoken language, and a reference translation
for each target language you want scored. Audio can be any format
`soundfile` reads (WAV, FLAC, OGG); mono or stereo (stereo is downmixed to
its first channel); any sample rate (resampled to 16kHz to match what the
ASR backend expects).

-----------------------------------------------------------------------------
2. Install eval-only dependencies
-----------------------------------------------------------------------------

    pip install -r requirements.txt -r requirements-eval.txt --break-system-packages

-----------------------------------------------------------------------------
3. Run
-----------------------------------------------------------------------------

    python evaluate_accuracy.py \
        --manifest manifest.json \
        --whisper-model small \
        --nllb-model-dir nllb-200-ct2 \
        --presenter-language en

Omitting --whisper-model / --nllb-model-dir runs FakeASRBackend /
FakeTranslationBackend instead (same placeholders server.py falls back to)
-- useful for confirming the manifest/scoring wiring itself is correct
before you have real models installed, but the WER/BLEU numbers it prints
in that mode are meaningless and are labeled as such.

-----------------------------------------------------------------------------
Output
-----------------------------------------------------------------------------

Writes two CSVs to --output-dir (default accuracy_results/):

    per_file.csv -- one row per (audio file, target language): hypothesis
                    and reference text, WER, BLEU, chrF.
    summary.csv  -- one row per target language, averaged across every
                    file that included a reference translation for it --
                    directly matches Table 8.2's column layout.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
from pathlib import Path

import numpy as np

from backends import resample_linear
from pipeline import AudioSegment, FakeASRBackend, FakeTranslationBackend

TARGET_SAMPLE_RATE = 16_000


def load_manifest(path: Path) -> list[dict]:
    # Explicit encoding="utf-8" -- without it, Path.read_text() falls back to
    # the OS's default codepage. On Windows that's typically cp1252, which
    # can't represent Devanagari/Arabic/etc. reference text and raises
    # UnicodeDecodeError; macOS/Linux default to UTF-8 so this only bites on
    # Windows, which is exactly what happened here.
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise SystemExit(f"{path} should contain a non-empty JSON list -- see manifest.example.json")
    for i, r in enumerate(records):
        for required in ("audio", "reference_transcript", "reference_translations"):
            if required not in r:
                raise SystemExit(f"manifest entry {i} is missing required field {required!r}")
    return records


def load_audio_as_segment(audio_path: Path) -> AudioSegment:
    import soundfile as sf

    samples, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    mono = samples[:, 0]  # downmix: take first channel if stereo
    resampled = resample_linear(mono, sample_rate, TARGET_SAMPLE_RATE)
    duration = len(resampled) / TARGET_SAMPLE_RATE
    return AudioSegment(samples=resampled, sample_rate=TARGET_SAMPLE_RATE, start_ts=0.0, end_ts=duration)


def build_backends(args: argparse.Namespace):
    if args.whisper_model:
        from backends import RealWhisperBackend

        asr = RealWhisperBackend(model_size=args.whisper_model, language=args.presenter_language)
    else:
        print("WARNING: no --whisper-model given -- using FakeASRBackend. WER numbers below are "
              "meaningless (they only measure the placeholder transcript against your references), "
              "this run is for checking the manifest/scoring wiring only.", file=sys.stderr)
        asr = FakeASRBackend(default_transcript="[no ASR model loaded]")

    if args.nllb_model_dir:
        from backends import RealNLLBBackend

        translator = RealNLLBBackend(model_dir=args.nllb_model_dir, source_lang=args.presenter_language)
    else:
        print("WARNING: no --nllb-model-dir given -- using FakeTranslationBackend. BLEU/chrF numbers "
              "below are meaningless, this run is for checking the manifest/scoring wiring only.",
              file=sys.stderr)
        translator = FakeTranslationBackend()

    return asr, translator


async def evaluate(args: argparse.Namespace) -> None:
    import jiwer
    import sacrebleu

    manifest_path = Path(args.manifest)
    records = load_manifest(manifest_path)
    asr, translator = build_backends(args)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    per_file_rows: list[dict] = []

    for record in records:
        audio_path = Path(record["audio"])
        if not audio_path.is_absolute():
            audio_path = manifest_path.parent / audio_path
        if not audio_path.exists():
            print(f"SKIPPING {audio_path} -- file not found", file=sys.stderr)
            continue

        print(f"Transcribing {audio_path.name} ...")
        segment = load_audio_as_segment(audio_path)
        hypothesis_transcript = await asr.transcribe(segment)
        wer = jiwer.wer(record["reference_transcript"], hypothesis_transcript)

        for lang, reference_translation in record["reference_translations"].items():
            hypothesis_translation = await translator.translate(hypothesis_transcript, lang)
            bleu = sacrebleu.corpus_bleu([hypothesis_translation], [[reference_translation]]).score
            chrf = sacrebleu.corpus_chrf([hypothesis_translation], [[reference_translation]]).score

            per_file_rows.append({
                "audio_file": audio_path.name,
                "target_language": lang,
                "wer": round(wer, 4),
                "bleu": round(bleu, 2),
                "chrf": round(chrf, 2),
                "reference_transcript": record["reference_transcript"],
                "hypothesis_transcript": hypothesis_transcript,
                "reference_translation": reference_translation,
                "hypothesis_translation": hypothesis_translation,
            })

    if not per_file_rows:
        raise SystemExit("No manifest entries were scored -- check --manifest and audio paths above.")

    per_file_path = output_dir / "per_file.csv"
    # encoding="utf-8" here for the same reason as load_manifest() above --
    # these rows contain the actual Hindi/French/etc. translated text, so
    # without it this write fails on Windows the moment real (non-Fake)
    # backends produce non-Latin output.
    with per_file_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_file_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_file_rows)

    summary_path = output_dir / "summary.csv"
    languages = sorted({r["target_language"] for r in per_file_rows})
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["target_language", "mean_wer", "mean_bleu", "mean_chrf", "num_files"])
        for lang in languages:
            rows = [r for r in per_file_rows if r["target_language"] == lang]
            writer.writerow([
                lang,
                round(statistics.mean(r["wer"] for r in rows), 4),
                round(statistics.mean(r["bleu"] for r in rows), 2),
                round(statistics.mean(r["chrf"] for r in rows), 2),
                len(rows),
            ])

    print(f"\nWrote {per_file_path} and {summary_path}")
    print(f"({len(per_file_rows)} (file, language) pairs scored across {len(languages)} language(s))")


def main() -> None:
    parser = argparse.ArgumentParser(description="Section 6.2 accuracy evaluation: WER + BLEU/chrF against real reference data.")
    parser.add_argument("--manifest", required=True, help="Path to a JSON manifest -- see manifest.example.json")
    parser.add_argument("--whisper-model", default=None, help="faster-whisper model size, e.g. 'small'. Omit to use FakeASRBackend (wiring check only).")
    parser.add_argument("--nllb-model-dir", default=None, help="Path to a CTranslate2-converted NLLB-200 directory. Omit to use FakeTranslationBackend (wiring check only).")
    parser.add_argument("--presenter-language", default="en", help="Language the reference_transcript field is written in.")
    parser.add_argument("--output-dir", default="accuracy_results", help="Directory to write per_file.csv and summary.csv into.")
    args = parser.parse_args()
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()