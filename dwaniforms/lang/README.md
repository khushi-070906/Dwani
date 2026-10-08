# DwaniForms language packs

One file per language. Everything DwaniForms says or shows in that language is in the file: the questions, the screen
text, the scheme details, and the words people use when they answer (yes / no, numbers, months, "two and a half").

| File | Language | Status |
|---|---|---|
| `bn.json` | বাংলা Bengali | draft |
| `gu.json` | ગુજરાતી Gujarati | draft |
| `kn.json` | ಕನ್ನಡ Kannada | draft |
| `ml.json` | മലയാളം Malayalam | draft |
| `mr.json` | मराठी Marathi | draft |
| `pa.json` | ਪੰਜਾਬੀ Punjabi | draft |
| `ta.json` | தமிழ் Tamil | draft |
| `te.json` | తెలుగు Telugu | draft |

Hindi and English are not here: they are written into the code itself (`messages.py`, `templates/`, `static/app.html`).

**"draft" means an AI assistant wrote it.** It has not been checked by a person who speaks the language. While a pack is a
draft, the app shows a small note under each question: "not yet checked by a native speaker".

## How to review a pack (no coding needed)

1. Open the file in any text editor (VS Code, Notepad++ or even GitHub's web editor). Keep the file as UTF-8.
2. Change only the text on the **right** side of each `:`. The left side is the key (often the English sentence) and must
   stay exactly as it is.
3. Keep anything in curly braces exactly as it is: `{title}`, `{value}`, `{count}`, `{lang}`... The app fills these in.
4. What to look for:
   - **messages**, **text**: would an older person in a village understand this when it is read aloud? Short, polite,
     everyday words beat correct-but-formal ones.
   - **options**: for each answer choice, the words people actually say. The **first** word is the one shown on screen
     and read back; add more spoken forms after it (dialect words, common English words, inflected forms such as
     "I am a farmer").
   - **words.yes / words.no**: single words only. Never put the same word in both.
   - **words.digits**: 0 to 10 (several spellings allowed). **months**: 12 names, January first.
   - **words.local_land_units**: land units that differ by state (bigha, guntha, cent...). The app asks for acres instead
     of guessing. Do **not** list a unit that equals one acre where you live (e.g. Punjab's killa).
5. When you are done, set `"status": "reviewed"` and write your name in `"reviewed_by"`.
6. Check it: `python -m dwaniforms.langpacks check` (must say "complete"), then `pytest tests/test_dwaniforms_langpacks.py`.

## Adding a language

`python -m dwaniforms.langpacks skeleton > dwaniforms/lang/or.json`, set `"lang"`, `"name"` and `"speech"` (the speech
recogniser's language tag, e.g. `or-IN`), and translate every value. Add the language to the list in
`tests/test_dwaniforms_langpacks.py::test_every_pack_is_installed`. The app offers it online automatically once the file
is there.

## When the English changes

Every question is keyed by its English sentence. If someone edits an English question in `templates/` or `schemes/`,
`langpacks check` (and CI) lists that sentence as missing in every pack until it is translated.
