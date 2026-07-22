"""
Translation-quality evaluation for Option 2 (Conference Glossary Adaptation).

Answers the research question from the proposal: "Does domain-specific
glossary adaptation improve translation quality for technical talks?" by
translating the same source sentences twice -- once through the plain
translator, once through GlossaryAwareTranslationBackend (glossary.py) --
and scoring both against reference translations you supply. Reports the
three metrics the proposal calls out (BLEU, COMET, human evaluation), plus
a fourth that's specific to what a glossary is actually for: glossary term
accuracy, i.e. whether the protected term itself shows up correctly in the
output, independent of how the rest of the sentence scored.

Like evaluate_accuracy.py, this only measures what you give it real
reference data for -- nothing here is estimated or fabricated. This script
is scoped to the translation step only (source text in, no audio/ASR --
see benchmark_semantic_cache.py for the same scoping rationale), since
glossary adaptation is entirely a translation-time concern.

-----------------------------------------------------------------------------
1. Build a manifest (JSON) -- see glossary_eval_sample_manifest.json
-----------------------------------------------------------------------------

A list of {"source": ..., "reference_translations": {lang: ref, ...}}
entries. Source sentences should actually contain glossary terms (that's
the point) and reference translations should reflect how you'd WANT the
term handled -- e.g. if "CUDA" should stay "CUDA" in the Hindi reference
too, write it that way. The sample manifest's references are illustrative
starting points, not vetted translations -- have a fluent speaker check
them before trusting numbers computed against them, same caveat as any
reference-based MT eval.

-----------------------------------------------------------------------------
2. Install
-----------------------------------------------------------------------------

    pip install -r requirements.txt -r requirements-eval.txt --break-system-packages

Add `pip install unbabel-comet --break-system-packages` and pass --comet to
also compute COMET scores (downloads a scoring model, ~1.7GB, on first use
-- do this once while you still have internet). Omit --comet to skip it and
get BLEU/chrF/terminology-accuracy only.

-----------------------------------------------------------------------------
3. Run
-----------------------------------------------------------------------------

    python evaluate_glossary.py \
        --manifest glossary_eval_sample_manifest.json \
        --glossary-file glossary.json \
        --nllb-model-dir nllb-200-ct2 \
        --presenter-language en \
        --comet

Omitting --nllb-model-dir runs FakeTranslationBackend instead -- useful for
checking the manifest/scoring/glossary wiring itself, but BLEU/COMET/
terminology-accuracy numbers in that mode are meaningless (labeled as such).

-----------------------------------------------------------------------------
Output
-----------------------------------------------------------------------------

Writes two CSVs to --output-dir (default glossary_eval_results/):

    per_sentence.csv -- one row per (sentence, target language): baseline
                         and glossary-adapted hypotheses, sentence-level
                         BLEU/chrF for each, per-term hit/miss, and blank
                         human_score_baseline / human_score_glossary
                         columns for a human rater to fill in (1-5).
    summary.csv       -- one row per target language: corpus-level BLEU
                          (baseline vs glossary), mean chrF, mean COMET (if
                          --comet), and glossary term accuracy (baseline vs
                          glossary) -- the number that most directly answers
                          the research question above.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
from pathlib import Path

from glossary import Glossary, GlossaryAwareTranslationBackend
from pipeline import FakeTranslationBackend


def load_manifest(path: Path) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise SystemExit(f"{path} should contain a non-empty JSON list -- see glossary_eval_sample_manifest.json")
    for i, r in enumerate(records):
        for required in ("source", "reference_translations"):
            if required not in r:
                raise SystemExit(f"manifest entry {i} is missing required field {required!r}")
    return records


def build_translator(nllb_model_dir: str | None, presenter_language: str):
    if nllb_model_dir:
        from backends import RealNLLBBackend

        return RealNLLBBackend(model_dir=nllb_model_dir, source_lang=presenter_language)
    print(
        "WARNING: no --nllb-model-dir given -- using FakeTranslationBackend. This checks the "
        "manifest/scoring/glossary wiring only; BLEU/COMET/terminology-accuracy numbers below "
        "are meaningless.",
        file=sys.stderr,
    )
    return FakeTranslationBackend()


def build_comet_scorer(use_comet: bool):
    if not use_comet:
        return None
    try:
        from comet import download_model, load_from_checkpoint
    except ImportError:
        raise SystemExit("--comet requires unbabel-comet: pip install unbabel-comet --break-system-packages")

    print("Loading COMET scoring model (first run downloads it -- needs internet once) ...")
    model_path = download_model("Unbabel/wmt22-comet-da")
    return load_from_checkpoint(model_path)


def score_comet(comet_model, records: list[dict]) -> list[float]:
    """`records` is a list of {"src", "mt", "ref"} dicts; returns one score
    per record, same order. Batches everything in one predict() call rather
    than one-at-a-time, since COMET's own batching is where its GPU/CPU
    throughput comes from."""
    if comet_model is None or not records:
        return [None] * len(records)  # type: ignore[list-item]
    output = comet_model.predict(records, batch_size=8, gpus=0)
    return list(output.scores)


async def evaluate(args: argparse.Namespace) -> None:
    import sacrebleu

    manifest_path = Path(args.manifest)
    records = load_manifest(manifest_path)
    glossary = Glossary.load(args.glossary_file)
    print(f"Glossary loaded: {len(glossary)} term(s).")

    base_translator = build_translator(args.nllb_model_dir, args.presenter_language)
    glossary_translator = GlossaryAwareTranslationBackend(base_translator, glossary)
    comet_model = build_comet_scorer(args.comet)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    per_sentence_rows: list[dict] = []
    comet_inputs: list[dict] = []  # parallel to per_sentence_rows, one baseline + one glossary entry each

    for record in records:
        source = record["source"]
        # Which glossary terms actually appear in this sentence, and what
        # each SHOULD translate to per language -- computed once via
        # protect(), reused for every target language's term-accuracy check.
        _protected, placeholder_map = glossary.protect(source)
        matched_terms = list(placeholder_map.values())  # list[(GlossaryTerm, matched_text)]

        for lang, reference in record["reference_translations"].items():
            baseline_hyp = await base_translator.translate(source, lang)
            glossary_hyp = await glossary_translator.translate(source, lang)

            baseline_bleu = sacrebleu.sentence_bleu(baseline_hyp, [reference]).score
            glossary_bleu = sacrebleu.sentence_bleu(glossary_hyp, [reference]).score
            baseline_chrf = sacrebleu.sentence_chrf(baseline_hyp, [reference]).score
            glossary_chrf = sacrebleu.sentence_chrf(glossary_hyp, [reference]).score

            term_expectations = [gt.translation_for(lang, matched) for gt, matched in matched_terms]
            terms_total = len(term_expectations)
            baseline_terms_correct = sum(1 for expected in term_expectations if expected in baseline_hyp)
            glossary_terms_correct = sum(1 for expected in term_expectations if expected in glossary_hyp)

            row = {
                "source": source,
                "target_language": lang,
                "reference": reference,
                "glossary_terms_in_sentence": "; ".join(gt.term for gt, _ in matched_terms) or "(none)",
                "baseline_hypothesis": baseline_hyp,
                "glossary_hypothesis": glossary_hyp,
                "baseline_bleu": round(baseline_bleu, 2),
                "glossary_bleu": round(glossary_bleu, 2),
                "baseline_chrf": round(baseline_chrf, 2),
                "glossary_chrf": round(glossary_chrf, 2),
                "baseline_terms_correct": f"{baseline_terms_correct}/{terms_total}" if terms_total else "n/a",
                "glossary_terms_correct": f"{glossary_terms_correct}/{terms_total}" if terms_total else "n/a",
                "baseline_comet": None,
                "glossary_comet": None,
                "human_score_baseline": "",
                "human_score_glossary": "",
            }
            per_sentence_rows.append(row)
            comet_inputs.append({
                "baseline": {"src": source, "mt": baseline_hyp, "ref": reference},
                "glossary": {"src": source, "mt": glossary_hyp, "ref": reference},
            })

    if not per_sentence_rows:
        raise SystemExit("No manifest entries were scored -- check --manifest above.")

    if comet_model is not None:
        print("Scoring with COMET ...")
        baseline_scores = score_comet(comet_model, [c["baseline"] for c in comet_inputs])
        glossary_scores = score_comet(comet_model, [c["glossary"] for c in comet_inputs])
        for row, b_score, g_score in zip(per_sentence_rows, baseline_scores, glossary_scores):
            row["baseline_comet"] = round(b_score, 4)
            row["glossary_comet"] = round(g_score, 4)

    per_sentence_path = output_dir / "per_sentence.csv"
    with per_sentence_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_sentence_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_sentence_rows)

    # -- summary: corpus-level BLEU (not an average of sentence BLEUs --
    # standard MT practice, since sentence-level BLEU averaging is known to
    # bias the number), plus mean chrF/COMET/term-accuracy per language.
    summary_path = output_dir / "summary.csv"
    languages = sorted({r["target_language"] for r in per_sentence_rows})
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "target_language", "num_sentences",
            "baseline_corpus_bleu", "glossary_corpus_bleu",
            "baseline_mean_chrf", "glossary_mean_chrf",
            "baseline_mean_comet", "glossary_mean_comet",
            "baseline_term_accuracy", "glossary_term_accuracy",
        ])
        for lang in languages:
            rows = [r for r in per_sentence_rows if r["target_language"] == lang]
            references = [[r["reference"] for r in rows]]
            baseline_corpus_bleu = sacrebleu.corpus_bleu([r["baseline_hypothesis"] for r in rows], references).score
            glossary_corpus_bleu = sacrebleu.corpus_bleu([r["glossary_hypothesis"] for r in rows], references).score

            def _term_accuracy(field: str) -> float | str:
                fractions = []
                for r in rows:
                    value = r[field]
                    if value == "n/a":
                        continue
                    correct, total = value.split("/")
                    if int(total) > 0:
                        fractions.append(int(correct) / int(total))
                return round(statistics.mean(fractions), 4) if fractions else "n/a"

            comet_rows = [r for r in rows if r["baseline_comet"] is not None]

            writer.writerow([
                lang,
                len(rows),
                round(baseline_corpus_bleu, 2),
                round(glossary_corpus_bleu, 2),
                round(statistics.mean(r["baseline_chrf"] for r in rows), 2),
                round(statistics.mean(r["glossary_chrf"] for r in rows), 2),
                round(statistics.mean(r["baseline_comet"] for r in comet_rows), 4) if comet_rows else "n/a",
                round(statistics.mean(r["glossary_comet"] for r in comet_rows), 4) if comet_rows else "n/a",
                _term_accuracy("baseline_terms_correct"),
                _term_accuracy("glossary_terms_correct"),
            ])

    print(f"\nWrote {per_sentence_path} and {summary_path}")
    print(f"({len(per_sentence_rows)} (sentence, language) pairs scored across {len(languages)} language(s))")
    print(
        "\nFill in human_score_baseline / human_score_glossary in per_sentence.csv (e.g. 1-5 "
        "adequacy/fluency) for the human-evaluation leg of the comparison."
    )
    if args.nllb_model_dir is None:
        print("\n(FakeTranslationBackend was used -- re-run with --nllb-model-dir for numbers to put in the paper.)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate glossary adaptation (Option 2): BLEU/chrF/COMET/term-accuracy, with vs without glossary."
    )
    parser.add_argument("--manifest", required=True, help="Path to a JSON manifest -- see glossary_eval_sample_manifest.json")
    parser.add_argument("--glossary-file", required=True, help="Path to glossary.json (see glossary.py, build_glossary.py)")
    parser.add_argument("--nllb-model-dir", default=None, help="Path to a CTranslate2-converted NLLB-200 directory. Omit to use FakeTranslationBackend (wiring check only).")
    parser.add_argument("--presenter-language", default="en", help="Language the manifest's 'source' field is written in.")
    parser.add_argument("--comet", action="store_true", help="Also compute COMET scores (requires unbabel-comet, downloads a model on first use)")
    parser.add_argument("--output-dir", default="glossary_eval_results", help="Directory to write per_sentence.csv and summary.csv into.")
    args = parser.parse_args()
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()