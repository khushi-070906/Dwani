"""Form templates: a JSON file per form. New forms need no retraining and no code -- add a template (and optionally
glossary terms). See templates/*.json for the shape."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

KINDS = {"aadhaar", "mobile", "pincode", "account", "pan", "ifsc", "date", "number",
         "name", "text", "place", "choice", "yesno", "khasra", "ration_card"}
# name/place: kept as spoken (transliteration hook), flagged for operator review.
# text: free text, translated into the form language.


@dataclass
class Option:
    value: str                                   # canonical value written to the form (form language)
    synonyms: dict[str, list[str]] = field(default_factory=dict)   # lang -> spoken forms; "en" doubles as the label


@dataclass
class FormField:
    id: str
    kind: str
    label: str                                   # form-language label shown on the output
    prompt: dict[str, str]                       # lang -> question; missing langs are machine-translated from "en"
    required: bool = True
    options: list[Option] = field(default_factory=list)
    params: dict = field(default_factory=dict)   # min_age, max_age, minimum, maximum, min_length
    sensitive: bool = False                      # Aadhaar etc.: never logged, never sent anywhere
    when: dict | None = None                     # ask only if e.g. {"field": "occupation", "in": ["Farmer"]}
    labels: dict[str, str] = field(default_factory=dict)   # lang -> label for read-back in the citizen's language

    def label_in(self, lang: str) -> str | None:
        return self.labels.get(lang)


@dataclass
class FormTemplate:
    id: str
    title: str
    form_language: str                           # language the portal/bank expects (usually "en")
    fields: list[FormField]
    glossary: list[str] = field(default_factory=list)   # terms protected from mistranslation
    flow: str = "form"                           # form | grievance | rti | lookup | advisor  (what happens at the end)
    description: dict[str, str] = field(default_factory=dict)
    extract: dict | None = None                  # {"from": "complaint", "fields": {"department": "department", ...}}
    final_readback: bool = False                 # read the whole draft back and confirm before finishing
    hidden: bool = False                         # not listed as a fill-able form (e.g. the advisor interview)
    titles: dict[str, str] = field(default_factory=dict)   # lang -> title spoken in the welcome

    def title_in(self, lang: str) -> str:
        return self.titles.get(lang, self.title)

    def field_by_id(self, fid: str) -> FormField:
        for f in self.fields:
            if f.id == fid:
                return f
        raise KeyError(fid)


def _field(d: dict) -> FormField:
    if d["kind"] not in KINDS:
        raise ValueError(f"field {d.get('id')!r}: unknown kind {d['kind']!r}")
    if "en" not in d["prompt"]:
        raise ValueError(f"field {d['id']!r}: prompt needs an 'en' entry")
    opts = [Option(o["value"], o.get("synonyms", {})) for o in d.get("options", [])]
    if d["kind"] == "choice" and len(opts) < 2:
        raise ValueError(f"field {d['id']!r}: choice needs >= 2 options")
    when = d.get("when")
    if when is not None and not ({"field"} <= set(when) and set(when) & {"in", "not_in", "gte", "lte", "eq"}):
        raise ValueError(f"field {d['id']!r}: 'when' needs a field and one of in/not_in/gte/lte/eq")
    return FormField(d["id"], d["kind"], d["label"], d["prompt"], d.get("required", True), opts,
                     d.get("params", {}), d.get("sensitive", d["kind"] in {"aadhaar", "account", "pan"}),
                     when, d.get("labels", {}))


def parse_template(data: dict) -> FormTemplate:
    fields = [_field(f) for f in data["fields"]]
    ids = [f.id for f in fields]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate field ids")
    ex = data.get("extract")
    if ex and (ex.get("from") not in ids or not set(ex.get("fields", {})) <= set(ids)):
        raise ValueError("extract: 'from' and every target must be field ids of this form")
    for f in fields:
        if f.when and f.when["field"] not in ids[:ids.index(f.id)]:
            raise ValueError(f"field {f.id!r}: 'when' must refer to an earlier field")
    return FormTemplate(data["id"], data["title"], data.get("form_language", "en"), fields, data.get("glossary", []),
                        data.get("flow", "form"), data.get("description", {}), ex, data.get("final_readback", False),
                        data.get("hidden", False), data.get("titles", {}))


def load_template(path: str | Path) -> FormTemplate:
    return parse_template(json.loads(Path(path).read_text(encoding="utf-8")))


def load_all(directory: str | Path | None = None) -> dict[str, FormTemplate]:
    directory = Path(directory) if directory else Path(__file__).parent / "templates"
    return {t.id: t for t in (load_template(p) for p in sorted(directory.glob("*.json")))}
