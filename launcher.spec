# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hidden_fastapi = collect_submodules('fastapi')
hidden_starlette = collect_submodules('starlette')
hidden_uvicorn = collect_submodules('uvicorn')

a = Analysis(
    ['protected/launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('protected/pyarmor_runtime_000000', 'pyarmor_runtime_000000')],
    hiddenimports=[
        'server', 'licensing', 'backends', 'qa_pipeline', 'nllb_tokenizer',
        'pipeline', 'session', 'glossary', 'accessibility', 'decision_engine',
        'dynamic_glossary', 'persistent_memory', 'translation_cache', 'activate',
        'faster_whisper', 'ctranslate2', 'sentencepiece', 'cryptography',
        'fastapi.staticfiles', 'starlette.staticfiles', 'aiofiles',
    ] + hidden_fastapi + hidden_starlette + hidden_uvicorn,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)