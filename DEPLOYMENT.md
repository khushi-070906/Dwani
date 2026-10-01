# Shipping DwaniLive (desktop app)

## Fastest way: GitHub Actions (no Windows setup needed)

Push the repo, then run `git tag v1.1.0 && git push origin v1.1.0`. GitHub builds on a real Windows machine,
self-tests the exe, and publishes `DwaniLive-Setup.exe`, the zip and the checksums as the latest release
(`.github/workflows/windows-release.yml`). Locally on Windows: double-click `build_windows.bat`.

## Release checklist (manual)

1. **Build machine:** Windows 10/11 x64, **Python 3.12** (not 3.13/3.14), project folder **outside OneDrive** (e.g. `C:\dev\Dwani`).
2. Fresh venv:
   ```
   py -3.12 -m venv .venv-build
   .venv-build\Scripts\activate
   pip install -r requirements-desktop.txt
   ```
3. Bump `APP_VERSION` in `appenv.py`.
4. `python build_exe.py` (Nuitka). If Nuitka fails, use `python build_exe.py --backend pyinstaller`.
   The build **runs `DwaniLive.exe --self-test` on the output** and refuses to package if anything is missing.
5. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) once, so the build also produces `release/DwaniLive-Setup.exe`.
6. Test on a **different** PC (or a clean Windows Sandbox) before uploading:
   install, then first-run download, then Free plan, then open the presenter page, then join from a phone on the same Wi-Fi.
7. GitHub → `khushi-070906/Dwani` → new release `v<version>` → upload everything in `release/` → **Set as latest release**.
   The dashboard links to `releases/latest/download/DwaniLive-Setup.exe`, so nothing else needs editing.

## Model bundle

`model_setup.py` → `NLLB_BUNDLE_URL` (currently `Dwani-models` release v1.0). To enable checksum verification:
```
python build_exe.py --hash-bundle dwani-models.zip
```
Then paste the value into `NLLB_BUNDLE_SHA256`.
Whisper `small` downloads from Hugging Face (with an hf-mirror fallback). To avoid HF entirely, upload
`config.json, model.bin, tokenizer.json, vocabulary.txt` from `Systran/faster-whisper-small` to a release
and set `DWANI_WHISPER_BASE_URL=https://github.com/.../releases/download/<tag>/{file}`. You can also hard-code that URL in `WHISPER_BASE_URLS`.

## Where things live on a user's PC

| What | Where |
|---|---|
| App | `%LOCALAPPDATA%\Programs\DwaniLive` (installer) or wherever the zip was extracted |
| Models (~1.1 GB), logs, QR | `%LOCALAPPDATA%\DwaniLive\` (survives upgrades; uninstaller offers to delete) |
| License token | `%USERPROFILE%\.ldst\license.token` |
| Log file | `%LOCALAPPDATA%\DwaniLive\logs\dwanilive.log` |

## Support playbook

| User says | Do |
|---|---|
| "Nothing opens" | Start menu → **DwaniLive (error report)**, or `DwaniLive.exe --diagnose`. Ask for the output. |
| Download stuck/failing | It resumes automatically. Campus Wi-Fi blocking GitHub/HF → use a phone hotspot once. |
| "Model damaged" / crashes on start | In the error screen, click **Re-download models**. Or run `DwaniLive.exe --reset-models`. |
| Phones can't join | Same Wi-Fi? Allowed the network prompt? College/hotel Wi-Fi often blocks device-to-device traffic, so run everything on a phone hotspot instead. |
| Antivirus deleted files | Reinstall. Long-term fix: code-sign the exe (e.g. Azure Trusted Signing / a CA code-signing cert). |

## Behaviour changes in 1.1.0
- The launcher window is an Edge app window served from `127.0.0.1` (tokened). pywebview/pythonnet/WebView2 are no longer needed.
- `--qa` no longer loads a second Whisper and a second NLLB. Typed questions share the main model. Voice questions need the new `--qa-mic` flag.
- The desktop app passes `--no-hotspot`, so it uses the venue Wi-Fi or a phone hotspot. Windows' hosted-network API is unsupported on most modern drivers.
- `activate.exe` is no longer needed. Activation happens inside the app window, or users can choose the Free plan.
