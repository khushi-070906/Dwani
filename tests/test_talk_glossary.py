"""Slides vocabulary: reading slides, picking terms, applying them live, plan gating."""
import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import talk_glossary as tg  # noqa: E402
from glossary import Glossary, GlossaryAwareTranslationBackend, GlossaryTerm  # noqa: E402

A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
PRO = SimpleNamespace(tier="pro", max_attendees=None, features=lambda: {"core", "glossary"})
FREE = SimpleNamespace(tier="free", max_attendees=20, features=lambda: {"core"})


def make_pptx(slides, notes=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for i, paras in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paras)
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld xmlns:p='x' {A}><a:txBody>{body}</a:txBody></p:sld>")
        for i, text in (notes or {}).items():
            z.writestr(f"ppt/notesSlides/notesSlide{i}.xml", f"<p:notes xmlns:p='x' {A}><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:notes>")
    return buf.getvalue()


SLIDES = make_pptx([
    ["Introduction to Deep Learning", "Why LSTM networks still matter"],
    ["Training with gradient descent", "The LSTM cell and the Transformer"],
    ["Gradient descent pitfalls", "Use gradient descent with a small learning rate", "Compare LSTM vs Transformer"],
    ["Questions?"],
], notes={2: "Mention CUDA kernels here. CUDA makes this fast."})


def test_pptx_text_in_slide_order_with_notes():
    text = tg.extract_text("talk.pptx", SLIDES)
    lines = text.splitlines()
    assert lines[0] == "Introduction to Deep Learning" and "Questions?" == lines[-1]
    assert lines.index("Training with gradient descent") < lines.index("Mention CUDA kernels here. CUDA makes this fast.") \
        < lines.index("Gradient descent pitfalls")                       # speaker notes follow their slide


def test_docx_and_txt():
    buf = io.BytesIO()
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {W}><w:body><w:p><w:r><w:t>Kubernetes and </w:t></w:r>"
                                        f"<w:r><w:t>Docker</w:t></w:r></w:p></w:body></w:document>")
    assert tg.extract_text("abstract.docx", buf.getvalue()) == "Kubernetes and Docker"
    assert tg.extract_text("n.md", "RAG and RAG".encode()) == "RAG and RAG"


def test_pdf():
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 700, "Retrieval Augmented Generation with RAG")
    c.save()
    assert "Retrieval Augmented Generation" in tg.extract_text("s.pdf", buf.getvalue())


def test_bad_files_get_clear_messages():
    for name, data, msg in (("old.ppt", b"x", "Save it as .pptx"), ("broken.pptx", b"not a zip", "Couldn't read"),
                            ("photo.jpg", b"x", "Upload slides"), ("big.pdf", b"0" * (tg.MAX_UPLOAD_BYTES + 1), "40 MB")):
        with pytest.raises(ValueError, match=msg):
            tg.extract_text(name, data)


def test_candidates():
    c = {x["term"]: x for x in tg.candidates(tg.extract_text("t.pptx", SLIDES))}
    assert c["LSTM"]["suggested"] and c["CUDA"]["suggested"] and c["Transformer"]["suggested"]
    assert c["gradient descent"]["suggested"]                                    # lowercase jargon, 3+ times
    assert "Introduction to Deep Learning" not in c or not c["Introduction to Deep Learning"]["suggested"]   # one-off title


class FakeASR:
    _initial_prompt = None


class EchoMT:
    async def translate(self, text, lang):
        return f"[{lang}] " + text.replace("the", "दी")


def test_live_glossary_whisper_translation_and_persistence(tmp_path):
    g = Glossary([GlossaryTerm("NLLB")])                       # a term from --glossary-file stays untouched
    asr = FakeASR()
    store = tmp_path / "glossary.json"
    t = tg.TalkGlossary(g, store, asr=asr)
    t.add(["LSTM", "gradient descent", "lstm", "  "])
    assert t.terms == ["LSTM", "gradient descent"]                 # de-duplicated, blanks dropped
    assert "gradient descent" in asr._initial_prompt and "NLLB" in asr._initial_prompt
    wrapped = GlossaryAwareTranslationBackend(EchoMT(), g)
    out = asyncio.run(wrapped.translate("the LSTM and the gradient descent step", "hi"))
    assert "LSTM" in out and "gradient descent" in out             # kept intact, rest translated
    t.remove(["LSTM"])
    assert [x.term for x in g] .count("LSTM") == 0 and "NLLB" in [x.term for x in g]
    again = tg.TalkGlossary(Glossary(), store, asr=FakeASR())
    assert again.terms == ["gradient descent"]                     # saved for the next session
    t.set_terms([])
    assert asr._initial_prompt is None or "gradient" not in asr._initial_prompt


def test_endpoints_and_plan_gating(tmp_path, monkeypatch):
    import phone_mic
    import server
    from fastapi.testclient import TestClient

    asr = FakeASR()
    monkeypatch.setattr(server, "talk_glossary", tg.TalkGlossary(Glossary(), tmp_path / "g.json", asr=asr))
    c = TestClient(server.app)
    monkeypatch.setattr(server, "active_license", FREE)
    assert c.get("/glossary/terms").json()["locked"] is True
    r = c.post("/glossary/extract", files={"file": ("t.pptx", SLIDES)})
    assert r.status_code == 402 and r.json()["locked"]
    assert c.post("/glossary/terms", json={"add": ["X"]}).status_code == 402

    monkeypatch.setattr(server, "active_license", PRO)
    r = c.post("/glossary/extract", files={"file": ("t.pptx", SLIDES)})
    assert r.status_code == 200 and any(x["term"] == "LSTM" for x in r.json()["candidates"])
    st = c.post("/glossary/terms", json={"add": ["LSTM", "CUDA"]}).json()
    assert st["terms"] == ["LSTM", "CUDA"] and st["whisper_hint"] and st["locked"] is False
    r = c.post("/glossary/extract", files={"file": ("t.pptx", SLIDES)})
    assert not any(x["term"] in ("LSTM", "CUDA") for x in r.json()["candidates"])   # already added: not suggested again
    assert c.post("/glossary/terms", json={"remove": ["CUDA"]}).json()["terms"] == ["LSTM"]
    bad = c.post("/glossary/extract", files={"file": ("x.ppt", b"x")})
    assert bad.status_code == 400 and ".pptx" in bad.json()["error"]
    remote = TestClient(server.app, client=("10.1.1.1", 5))
    assert remote.get("/glossary/terms").status_code == 404
    assert remote.get(f"/glossary/terms?key={phone_mic.PRESENTER_KEY}").status_code == 200


def test_notes_plan_gating(tmp_path, monkeypatch):
    import notes as N
    import server
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DWANI_NOTES_DIR", str(tmp_path))
    rec = N.NotesRecorder(tmp_path, "g1", "en")
    for i, t in enumerate(["Gradient descent trains the network.", "The learning rate sets the step size."]):
        rec.record(t, {"hi": "[hi] " + t}, rec.started + 5 * (i + 1), 3)
    monkeypatch.setattr(server, "notes_recorder", rec)
    c = TestClient(server.app)
    base = f"/notes/export?id={rec.path.stem}"
    monkeypatch.setattr(server, "active_license", FREE)
    assert c.get("/notes/status").json()["full_notes"] is False
    assert c.get(base + "&format=txt").status_code == 200                       # transcript: free
    for q in ("&format=notes", "&format=srt", "&format=md", "&format=txt&lang=hi"):
        r = c.get(base + q)
        assert r.status_code == 402 and "Pro feature" in r.text and "format=txt" in r.text, q
    monkeypatch.setattr(server, "active_license", PRO)
    assert c.get("/notes/status").json()["full_notes"] is True
    for q in ("&format=notes", "&format=srt", "&format=md", "&format=txt&lang=hi"):
        assert c.get(base + q).status_code == 200, q
