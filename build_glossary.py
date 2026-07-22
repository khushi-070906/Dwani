"""
Builds a candidate glossary.json from conference materials (papers, slides,
abstracts) -- Section 4.x "Conference Glossary Adaptation" (Option 2).

Runs extract_candidate_terms() (glossary.py) over one or more files and
writes the results as an editable glossary.json: every candidate starts as
"preserve verbatim" (empty translations dict). This is a FIRST PASS, not a
finished glossary -- extract_candidate_terms() is heuristic (see its
docstring) and will both miss real terms and include false positives. Open
the output, read through it, delete anything that isn't actually terminology
you want protected, and fill in per-language translations for terms that
should be translated to a specific word rather than left in English (e.g.
"Transformer" -> "ट्रांसफार्मर") rather than preserved as-is (e.g. "CUDA").

-----------------------------------------------------------------------------
1. Install
-----------------------------------------------------------------------------

    pip install -r requirements.txt pypdf --break-system-packages

(pypdf only needed if any input file is a .pdf -- see glossary.py's
extract_text_from_file().)

-----------------------------------------------------------------------------
2. Run
-----------------------------------------------------------------------------

    python build_glossary.py \
        --input paper.pdf slides.txt abstract.txt \
        --output glossary.json \
        --min-occurrences 2

-----------------------------------------------------------------------------
3. Review
-----------------------------------------------------------------------------

Open glossary.json. For each entry:
    - Delete it if it's not actually a term worth protecting.
    - Leave "translations": {} if it should be preserved verbatim in every
      target language (product/library names, acronyms: "CUDA", "GPU").
    - Add "translations": {"hi": "...", "fr": "..."} for terms that DO have
      a correct, specific translation you want forced rather than left to
      NLLB's guess.

-----------------------------------------------------------------------------
4. Use it
-----------------------------------------------------------------------------

    from glossary import Glossary
    glossary = Glossary.load("glossary.json")

or pass --glossary-file glossary.json to server.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from glossary import Glossary, GlossaryTerm, extract_candidate_terms, extract_text_from_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract candidate glossary terms from conference materials (paper/slides/abstract)."
    )
    parser.add_argument("--input", nargs="+", required=True, help="One or more .txt/.md/.pdf files to scan")
    parser.add_argument("--output", default="glossary.json", help="Path to write the candidate glossary (default: glossary.json)")
    parser.add_argument(
        "--min-occurrences",
        type=int,
        default=2,
        help="Only keep a candidate term if it appears at least this many times across all input files (default: 2)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="If --output already exists, add newly found candidates to it instead of overwriting "
        "(existing entries, including any translations you've already filled in, are kept as-is).",
    )
    args = parser.parse_args()

    combined_text_parts: list[str] = []
    for input_path in args.input:
        path = Path(input_path)
        if not path.exists():
            print(f"SKIPPING {path} -- file not found", file=sys.stderr)
            continue
        try:
            combined_text_parts.append(extract_text_from_file(path))
        except ValueError as e:
            print(f"SKIPPING {path} -- {e}", file=sys.stderr)

    if not combined_text_parts:
        raise SystemExit("No input files could be read -- check --input paths above.")

    combined_text = "\n".join(combined_text_parts)
    candidates = extract_candidate_terms(combined_text, min_occurrences=args.min_occurrences)

    output_path = Path(args.output)
    if args.merge and output_path.exists():
        glossary = Glossary.load(output_path)
        existing_terms = {t.term for t in glossary}
        new_terms = [t for t in candidates if t not in existing_terms]
        for term in new_terms:
            glossary.add(GlossaryTerm(term=term))
        print(f"Merged {len(new_terms)} new candidate(s) into {output_path} ({len(existing_terms)} already present).")
    else:
        glossary = Glossary.preserve_only(candidates)
        print(f"Found {len(candidates)} candidate term(s).")

    glossary.save(output_path)
    print(f"Wrote {output_path} -- REVIEW THIS before using it live (see this script's module docstring).")

    if candidates:
        print("\nCandidates found:")
        for term in candidates:
            print(f"  - {term}")


if __name__ == "__main__":
    main()