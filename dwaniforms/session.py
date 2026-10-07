"""Slot-filling conversation with read-back confirmation (step 4 of the DwaniForm flow).

One FormSession = one citizen filling one form. Text in, Reply out; ASR happens outside (service.py), so the whole
state machine is testable with plain strings. The translator is any object with `async translate(text, target_lang)`
(pipeline.TranslationBackend); `translate_between(text, src, tgt)` is used when present (RealNLLBBackend has it).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from . import validators
from .messages import MESSAGES, _words, is_skip, parse_yes_no
from .schema import FormField, FormTemplate
from .spoken import MONTH_NAMES, ascii_digits

MAX_ATTEMPTS = 3


def mask_value(v: str) -> str:
    """Show only the last four characters (UIDAI-style masking)."""
    return "•" * max(len(v) - 4, 0) + v[-4:] if v else v


@dataclass
class Answer:
    value: str                       # what goes on the form (form language / canonical)
    native: str                      # what the citizen actually said (as transcribed)
    needs_review: bool = False       # operator should eyeball it (untransliterated name, machine-translated text)
    confirmed: bool = True


@dataclass
class Reply:
    text: str                        # say/show this to the citizen, in their language
    field_id: str | None
    phase: str                       # "ask" | "confirm" | "escalate" | "done"
    done: bool = False
    machine_translated: bool = False
    pending: str | None = None       # value awaiting confirmation (show on screen)
    outcome: dict | None = None      # draft letter / record found / schemes, when the flow is complete


DIGIT_LENGTHS = {"aadhaar": 12, "mobile": 10, "pincode": 6}   # numbers people often say in pieces


@dataclass
class FormSession:
    template: FormTemplate
    lang: str
    translator: object | None = None
    transliterate: object | None = None          # callable(text, src_lang, tgt_lang) -> str, optional
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    answers: dict[str, Answer] = field(default_factory=dict)
    idx: int = 0
    phase: str = "start"
    attempts: int = 0
    _pending: tuple[str, str, bool] | None = None   # (value, native, needs_review)
    _cache: dict[tuple[str, str], str] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)    # session expiry is based on this, not on `created`
    machine_translated_used: bool = False
    english_fallback: bool = False                           # prompts shown in English because no translator is available
    # values we already have a guess for (extracted from a complaint, or carried over from the scheme advisor):
    # asked as "I noted X. Is this correct?" instead of an open question. field_id -> (value, native, needs_review)
    suggestions: dict = field(default_factory=dict)
    extractor: object | None = None                          # callable(text, lang, template) -> {field_id: (value, native)}
    checker: object | None = None                            # callable(field, value, session) -> (ok, value, err_key)
                                                             # e.g. "does this village exist in the cached records?"
    _from_suggestion: bool = False
    _final_ok: bool = False
    _fix_attempts: int = 0
    outcome: dict | None = None                              # set by the service when the flow finishes
    # Digits already heard for a fixed-length number, when the speech recogniser stopped at a pause
    # ("2345" ... "6789 0124"): kept and joined with the next answer. field_id -> digits
    partial_digits: dict = field(default_factory=dict)

    def touch(self) -> None:
        self.last_active = time.time()

    # ---- localisation ------------------------------------------------------
    async def _tr(self, text_en: str, lang: str | None = None) -> str:
        lang = lang or self.lang
        if lang == "en":
            return text_en
        if self.translator is None:
            self.english_fallback = True
            return text_en
        key = (text_en, lang)
        if key not in self._cache:
            self._cache[key] = await self.translator.translate(text_en, lang)
            self.machine_translated_used = True
        return self._cache[key]

    async def msg(self, key: str, **kw) -> str:
        table = MESSAGES.get(self.lang)
        if table and key in table:
            return table[key].format(**kw) if kw else table[key]
        # Fill placeholders BEFORE translating: a translator can mangle "{title}" (and a mangled brace crashes .format()).
        en = MESSAGES["en"][key]
        return await self._tr(en.format(**kw) if kw else en)

    async def _prompt(self, f: FormField) -> str:
        return f.prompt.get(self.lang) or await self._tr(f.prompt["en"])

    @property
    def current(self) -> FormField | None:
        return self.template.fields[self.idx] if self.idx < len(self.template.fields) else None

    # ---- flow --------------------------------------------------------------
    def applicable(self, f: FormField) -> bool:
        """`when` conditions: a land question only for farmers, a widow-pension question only for women..."""
        w = f.when
        if not w:
            return True
        a = self.answers.get(w["field"])
        if a is None or a.value == "":
            return False
        v = a.value
        if "in" in w:
            return v in w["in"]
        if "not_in" in w:
            return v not in w["not_in"]
        if "eq" in w:
            return v == w["eq"]
        from decimal import Decimal, InvalidOperation
        try:
            n = Decimal(v)
        except InvalidOperation:
            return False
        return (("gte" not in w or n >= Decimal(str(w["gte"]))) and ("lte" not in w or n <= Decimal(str(w["lte"]))))

    def _next_idx(self) -> int:
        """First applicable field with no answer yet (so redo / operator edits never re-ask answered fields)."""
        for i, f in enumerate(self.template.fields):
            if f.id not in self.answers and self.applicable(f):
                return i
        return len(self.template.fields)

    def prefill(self, values: dict) -> None:
        """Values known from elsewhere (the scheme advisor): each is confirmed with one yes/no, not asked again."""
        for fid, value in values.items():
            try:
                self.template.field_by_id(fid)
            except KeyError:
                continue
            if fid not in self.answers and value not in (None, ""):
                self.suggestions[fid] = (str(value), str(value), False)

    def _accepted(self, f: FormField) -> None:
        """After an answer is final: a complaint/RTI request is mined for department, place and date."""
        ex = self.template.extract
        if not ex or f.id != ex["from"] or self.extractor is None:
            return
        a = self.answers.get(f.id)
        if not a or not a.native:
            return
        found = self.extractor(a.native, self.lang, self.template) or {}
        for target, (value, native) in found.items():
            if target in ex["fields"] and target not in self.answers and value:
                self.suggestions[target] = (value, native, False)

    async def start(self) -> Reply:
        self.touch()
        self.phase = "ask"
        self.idx = self._next_idx()
        welcome = await self.msg("welcome", title=self.template.title_in(self.lang))
        return await self._ask(prefix=welcome)

    async def _ask(self, prefix: str = "") -> Reply:
        f = self.current
        if f is None:
            if self.template.final_readback and not self._final_ok:
                return await self._final(prefix)
            self.phase = "done"
            return Reply(await self.msg("done"), None, "done", done=True, machine_translated=self.machine_translated_used)
        self.phase, self.attempts, self._pending, self._from_suggestion = "ask", 0, None, False
        pre = (prefix + " " if prefix else "")
        if f.id in self.suggestions:
            value, native, review = self.suggestions.pop(f.id)
            self._pending, self.phase, self._from_suggestion = (value, native, review), "confirm", True
            label = f.label_in(self.lang) or await self._tr(f.label)
            text = pre + await self.msg("suggested", label=label, value=self.say_value(f, value, native)) + " " + \
                await self.msg("is_correct")
            return Reply(text, f.id, "confirm", pending=value, machine_translated=self.machine_translated_used)
        text = pre + await self._prompt(f)
        return Reply(text, f.id, "ask", machine_translated=self.machine_translated_used)

    # ---- final read-back (complaints, RTI) ---------------------------------------
    async def _final(self, prefix: str = "") -> Reply:
        self.phase = "final"
        parts = []
        for f in self.template.fields:
            a = self.answers.get(f.id)
            if not a or not a.value or not self.applicable(f):
                continue
            label = f.label_in(self.lang) or await self._tr(f.label)
            said = mask_value(a.value) if f.sensitive else self.say_value(f, a.value, a.native)
            parts.append(f"{label}: {said}")
        text = (prefix + " " if prefix else "") + await self.msg("final_intro") + " " + "; ".join(parts) + ". " + \
            await self.msg("final_ok")
        return Reply(text, None, "final", machine_translated=self.machine_translated_used)

    def _field_named(self, text: str) -> FormField | None:
        said = " " + " ".join(_words(text)) + " "
        hits = []
        for f in self.template.fields:
            if f.id not in self.answers or not self.applicable(f):
                continue
            names = [f.label, f.id.replace("_", " "), *(v for v in f.labels.values())]
            if any((" " + " ".join(_words(n)) + " ") in said or " ".join(_words(n)).split()[0:1] and
                   (" " + " ".join(_words(n)).split()[0] + " ") in said for n in names if _words(n)):
                hits.append(f)
        return hits[0] if len(hits) == 1 else None

    async def _on_final(self, text: str) -> Reply:
        yn = parse_yes_no(text)
        if yn:
            self._final_ok = True
            return await self._ask()
        if yn is None:
            return Reply(await self.msg("yesno_invalid"), None, "final")
        self.phase, self._fix_attempts = "fix", 0
        return await self._which_fix()

    async def _which_fix(self) -> Reply:
        labels = [f.label_in(self.lang) or await self._tr(f.label) for f in self.template.fields
                  if f.id in self.answers and self.applicable(f)]
        return Reply(await self.msg("fix_which") + " " + ", ".join(labels), None, "fix")

    async def _on_fix(self, text: str) -> Reply:
        f = self._field_named(text)
        if f is None:
            self._fix_attempts += 1
            if self._fix_attempts >= MAX_ATTEMPTS:
                self.phase = "escalate"
                return Reply(await self.msg("ask_operator"), None, "escalate")
            return await self._which_fix()
        return await self.redo(f.id)

    async def submit(self, text: str) -> Reply:
        """Feed one transcribed utterance. Returns what to say next."""
        self.touch()
        if self.phase == "start":
            return await self.start()
        if self.phase == "done":
            return Reply(await self.msg("done"), None, "done", done=True)
        text = text.strip()
        if self.phase == "final":
            return await self._on_final(text)
        if self.phase == "fix":
            return await self._on_fix(text)
        return await (self._on_confirm(text) if self.phase == "confirm" else self._on_answer(text))

    async def _on_answer(self, text: str) -> Reply:
        f = self.current
        assert f is not None
        need = DIGIT_LENGTHS.get(f.kind)
        prev = self.partial_digits.pop(f.id, "") if need else ""
        if prev:
            text = prev + " " + text                     # continue the number from where the pause cut it
        ok, value, err, review = await self._parse(f, text)
        if not ok and need:
            from .spoken import spoken_to_digits
            got = spoken_to_digits(text)
            if 0 < len(got) < need:                      # an incomplete number, not a wrong one: ask for the rest
                self.partial_digits[f.id] = got
                return Reply(await self.msg("partial_digits", got=len(got), left=need - len(got)), f.id, "ask",
                             machine_translated=self.machine_translated_used)
        if ok and self.checker is not None and value:
            ok, value, err = self.checker(f, value, self)
            if ok and f.params.get("lookup"):
                review = False                          # matched against the records: the spelling is the record's
        # An optional field can be skipped -- but for a yes/no or choice question, "no" / "नहीं" IS the answer,
        # so a skip word only skips when it isn't a valid answer.
        if not f.required and is_skip(text) and not (ok and f.kind in ("yesno", "choice")):
            self.answers[f.id] = Answer("", text)
            self._accepted(f)
            self.idx = self._next_idx()
            return await self._ask()
        if not ok:
            self.attempts += 1
            reason = await self.msg(err) if err in MESSAGES["en"] else err
            if f.kind == "choice":
                reason += " " + ", ".join([await self._option_label(o) for o in f.options])
            if self.attempts >= MAX_ATTEMPTS:
                self.phase = "escalate"
                return Reply(reason + " " + await self.msg("ask_operator"), f.id, "escalate",
                             machine_translated=self.machine_translated_used)
            return Reply(reason, f.id, "ask", machine_translated=self.machine_translated_used)
        if f.kind in ("choice", "yesno"):                    # unambiguous by construction; no read-back round trip
            self.answers[f.id] = Answer(value, text, review)
            self._accepted(f)
            self.idx = self._next_idx()
            return await self._ask()
        self._pending = (value, text, review)
        self.phase = "confirm"
        rb = f"{await self.msg('you_said')} {self.say_value(f, value, text)}. {await self.msg('is_correct')}"
        return Reply(rb, f.id, "confirm", pending=value, machine_translated=self.machine_translated_used)

    async def _on_confirm(self, text: str) -> Reply:
        f, (value, native, review) = self.current, self._pending
        yn = parse_yes_no(text)
        if yn is None:
            return Reply(await self.msg("yesno_invalid"), f.id, "confirm", pending=value)
        if yn:
            self.answers[f.id] = Answer(value, native, review)
            self._accepted(f)
            self.idx = self._next_idx()
            return await self._ask()
        if self._from_suggestion:                         # our guess was wrong: just ask the question normally
            self._from_suggestion, self.phase, self._pending = False, "ask", None
            return Reply(await self._prompt(f), f.id, "ask", machine_translated=self.machine_translated_used)
        self.attempts += 1
        if self.attempts >= MAX_ATTEMPTS:
            self.phase = "escalate"
            return Reply(await self.msg("ask_operator"), f.id, "escalate")
        self.phase, self._pending = "ask", None
        return Reply(await self.msg("retry") + " " + await self._prompt(f), f.id, "ask")

    # ---- operator/UI controls ------------------------------------------------
    async def redo(self, field_id: str) -> Reply:
        """Go back to one field (citizen spotted an error on the review screen)."""
        self.touch()
        self.template.field_by_id(field_id)              # KeyError for an unknown field
        self.idx = next(i for i, f in enumerate(self.template.fields) if f.id == field_id)
        self.answers.pop(field_id, None)
        self.suggestions.pop(field_id, None)
        self.partial_digits.pop(field_id, None)
        self._final_ok = False
        self.outcome = None                               # the draft / result must be rebuilt from the new answer
        return await self._ask()

    async def ask_current(self) -> Reply:
        """Re-issue the current question (after an operator edit)."""
        self.touch()
        return await self._ask()

    def operator_set(self, field_id: str, value: str) -> tuple[bool, str]:
        """Operator types a value (escalation path). Same validation, no read-back."""
        self.touch()
        f = self.template.field_by_id(field_id)          # KeyError for an unknown field
        if f.kind in validators.VALIDATORS:
            ok, v, err = validators.VALIDATORS[f.kind](value, **f.params)
            if not ok:
                return False, err
            value = v
        elif f.kind == "choice":
            hit = next((o.value for o in f.options if o.value.lower() == value.strip().lower()), None)
            if hit is None:
                return False, "choice_invalid"
            value = hit
        elif f.kind == "yesno":
            yn = parse_yes_no(value)
            if yn is None:
                return False, "yesno_invalid"
            value = "Yes" if yn else "No"
        else:                                            # name / place / text
            ok, v, err = validators.text(value, **f.params)
            if not ok:
                return False, err
            value = v
        self.answers[field_id] = Answer(value, value, needs_review=False)
        self.outcome = None
        old = self.idx
        self.idx = self._next_idx()
        if self.idx != old:
            self.phase, self.attempts, self._pending = "ask", 0, None
        return True, ""

    # ---- parsing -----------------------------------------------------------
    async def _parse(self, f: FormField, text: str) -> tuple[bool, str | None, str, bool]:
        k = f.kind
        if k in validators.VALIDATORS:
            ok, v, err = validators.VALIDATORS[k](text, **f.params)
            return ok, v, err, False
        if k in ("name", "place"):
            ok, v, err = validators.text(text, **f.params)
            if not ok:
                return False, None, err, False
            review = False
            if self.lang != self.template.form_language:
                if self.transliterate is not None:
                    v = self.transliterate(v, self.lang, self.template.form_language)
                else:
                    review = True      # we do NOT machine-translate names; operator confirms the Latin spelling
            return True, v, "", review
        if k == "text":
            ok, v, err = validators.text(text, **f.params)
            if not ok:
                return False, None, err, False
            if self.lang != self.template.form_language and self.translator is not None:
                v = await self._to_form_language(v)
                return True, v, "", True
            return True, v, "", False
        if k == "yesno":
            yn = parse_yes_no(text)
            return (True, "Yes" if yn else "No", "", False) if yn is not None else (False, None, "yesno_invalid", False)
        if k == "choice":
            opt = self._match_option(f, text)
            if opt is None and self.lang != "en" and self.translator is not None:
                opt = self._match_option(f, await self._to_form_language(text))
            return (True, opt, "", False) if opt else (False, None, "choice_invalid", False)
        return False, None, "text_short", False

    async def _to_form_language(self, text: str) -> str:
        tgt = self.template.form_language
        if hasattr(self.translator, "translate_between"):
            return await self.translator.translate_between(text, self.lang, tgt)
        return await self.translator.translate(text, tgt)

    def _match_option(self, f: FormField, text: str) -> str | None:
        def norm(x: str) -> str:
            return " " + " ".join(_words(ascii_digits(x))) + " "
        t = norm(text)
        best: dict[str, int] = {}          # option -> length (in words) of its longest synonym found in the answer
        for o in f.options:
            forms = [o.value, *o.synonyms.get(self.lang, []), *o.synonyms.get("en", [])]
            for s in forms:
                n = norm(s)
                if n.strip() and n in t:
                    best[o.value] = max(best.get(o.value, 0), len(n.split()))
        if not best:
            return None
        top = max(best.values())
        winners = [v for v, n in best.items() if n == top]
        # "farm labour" beats "labour"; two different options matched equally well is ambiguous -> ask again
        return winners[0] if len(winners) == 1 else None

    async def _option_label(self, o) -> str:
        return (o.synonyms.get(self.lang) or [o.value])[0]

    def say_value(self, f: FormField, value: str, native: str = "") -> str:
        """What to SAY back to the citizen: their own words for free text / names / places (not the translation
        they may not read), the option's name in their language for choices, spelled digits for numbers."""
        if f.kind in ("text", "name", "place") and native and self.lang != self.template.form_language:
            return native
        if f.kind == "choice":
            for o in f.options:
                if o.value == value:   # the form's own wording in English; the option's name in other languages
                    return o.value if self.lang == "en" else (o.synonyms.get(self.lang) or [o.value])[0]
        return self.readback(f, value, self.lang)

    # ---- read-back ---------------------------------------------------------
    @staticmethod
    def readback(f: FormField, value: str, lang: str = "en") -> str:
        """Spoken form of a value. Digit strings are spelled out in groups so TTS doesn't say 'nine hundred and ...'."""
        if f.kind == "aadhaar":
            return "  ".join(" ".join(value[i:i + 4]) for i in range(0, 12, 4))
        if f.kind == "mobile":
            return "  ".join(" ".join(value[i:i + 5]) for i in (0, 5))
        if f.kind in validators.SPELLED_KINDS:
            return " ".join(value)
        if f.kind == "date" and len(value) == 10 and value[4] == "-":      # ISO -> "15 August 1990" (TTS-friendly)
            y, m, d = int(value[:4]), int(value[5:7]), int(value[8:])
            names = MONTH_NAMES.get(lang)
            return f"{d} {names[m - 1]} {y}" if names else f"{d} {m} {y}"
        return value

    # ---- output ------------------------------------------------------------
    def complete(self) -> bool:
        return all(f.id in self.answers or not f.required or not self.applicable(f) for f in self.template.fields)

    def form_values(self, mask: bool = False) -> list[dict]:
        """Rows for the UI/exports. mask=True hides all but the last 4 characters of sensitive fields (and drops what
        the citizen actually said for them)."""
        out = []
        for f in self.template.fields:
            if not self.applicable(f):
                continue
            a = self.answers.get(f.id)
            value, native = (a.value, a.native) if a else ("", "")
            if mask and f.sensitive and value:
                value, native = mask_value(value), ""
            out.append({"id": f.id, "label": f.label, "value": value, "native": native,
                        "needs_review": bool(a and a.needs_review), "sensitive": f.sensitive, "kind": f.kind})
        return out
