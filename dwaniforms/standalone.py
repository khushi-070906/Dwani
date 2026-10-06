"""Run DwaniForms on its own (CSC/panchayat laptop, no internet after models are downloaded):

    python -m dwaniforms.standalone --whisper-model small --nllb-model-dir nllb-200-ct2
    python -m dwaniforms.standalone --text-only          # no models: typed input only, for demos/UI work

Then open http://localhost:8100/form/
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # reuse DwaniLive modules from the repo root


def build_app(whisper_model: str | None, nllb_dir: str | None, sp_model: str, text_only: bool,
              demo_records: bool = False, browser_voice: bool = False):
    from fastapi import FastAPI
    from .api import create_router
    from .schema import load_all
    from .service import FormService

    templates = load_all()
    asr = translator = None
    if not text_only:
        from backends import RealNLLBBackend, RealWhisperBackend
        from glossary import Glossary, GlossaryAwareTranslationBackend
        terms = sorted({t for f in templates.values() for t in f.glossary})
        glossary = Glossary.preserve_only(terms)               # keep "Aadhaar", "e-Shram", "khasra"... verbatim
        asr = RealWhisperBackend(model_size=whisper_model or "small", language="hi",
                                 initial_prompt=glossary.as_whisper_prompt())
        if nllb_dir:
            translator = GlossaryAwareTranslationBackend(RealNLLBBackend(nllb_dir, sentencepiece_model_path=sp_model), glossary)
    from .records import RecordStore, write_demo
    if demo_records:                       # fictional data, clearly marked, for demos only -- never the default
        import tempfile
        store = RecordStore(write_demo(Path(tempfile.mkdtemp(prefix="dwaniforms-demo-"))))
    else:
        store = RecordStore()              # what the operator imported (python -m dwaniforms.records import ...)
    service = FormService(asr, translator, templates, record_store=store)
    service.browser_voice = browser_voice

    @asynccontextmanager
    async def lifespan(_app):
        sweeper = asyncio.create_task(service.sweep_forever())     # purge idle sessions (they hold Aadhaar numbers)
        try:
            yield
        finally:
            sweeper.cancel()

    app = FastAPI(title="DwaniForms", lifespan=lifespan)
    app.include_router(create_router(service))

    @app.get("/", include_in_schema=False)
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/form/")
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper-model", default="small")
    ap.add_argument("--nllb-model-dir", default=None)
    ap.add_argument("--sentencepiece-model", default="sentencepiece.bpe.model")
    ap.add_argument("--text-only", action="store_true")
    ap.add_argument("--demo-records", action="store_true", help="use fictional land/ration records (demos only)")
    ap.add_argument("--browser-voice", action="store_true",
                    help="public web demo only: let the page use the browser's speech recognition (not offline)")
    ap.add_argument("--host", default="127.0.0.1")           # local only by default: this handles Aadhaar numbers
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--ssl-keyfile", default=None, help="serve over https (browsers only allow the microphone on https or localhost)")
    ap.add_argument("--ssl-certfile", default=None)
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(build_app(a.whisper_model, a.nllb_model_dir, a.sentencepiece_model, a.text_only, a.demo_records,
                          a.browser_voice),
                host=a.host, port=a.port,
                ssl_keyfile=a.ssl_keyfile, ssl_certfile=a.ssl_certfile)


if __name__ == "__main__":
    main()
