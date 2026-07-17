# Portability

The core supports Python 3.11 and 3.12 on Linux. A clean install is:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/fusionctl test
```

Repository paths are derived from the package/repository root. User data paths
are configurable; active v2 code must not hardcode `/home/lucy-ubuntu`.

GPU AllTalk/XTTS, Docling GPU, Whisper models, Ollama, SearXNG, ffmpeg,
LibreOffice and Tesseract are external runtimes or optional system tools. They
are not mutated by editable installation and are not required by CI.

Constraints for Python 3.11 live in `requirements/constraints-py311.txt`.
Historical requirements remain wrappers around the canonical core/optional
files. Node is used only for static tests and Playwright E2E; no frontend
framework or build step is required.
