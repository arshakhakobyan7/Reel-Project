# Third-party software

Every tool, library and model Reel uses is free (D-009). This list was made from the
installed packages' metadata and the tools' own licence files. Versions are the ones
installed on the development PC on 2026-11-20.

## Python packages: runtime

These are the packages declared in `pyproject.toml`, together with the packages they need
in turn.

| Package | Version | Licence | Why Reel needs it |
|---|---|---|---|
| fastapi | 0.165.0 | MIT | staff dashboard and API |
| starlette | 1.10.0 | BSD-3-Clause | used by FastAPI |
| uvicorn | 0.54.0 | BSD-3-Clause | web server of `reel serve` |
| jinja2 | 3.1.6 | BSD-3-Clause | dashboard pages |
| markupsafe | 3.0.4 | BSD-3-Clause | used by Jinja2 (escaping) |
| python-multipart | 0.0.34 | Apache-2.0 | HTML form parsing |
| pydantic / pydantic-core | 2.14.3 / 2.50.4 | MIT | configuration validation, LLM answer schemas |
| annotated-types, typing-inspection, typing-extensions | 0.8.0, 0.4.5, 4.16.0 | MIT, MIT, PSF-2.0 | used by pydantic and others |
| annotated-doc, opentelemetry-api | 0.0.7, 1.45.2 | MIT, Apache-2.0 | used by FastAPI |
| sqlalchemy | 2.1.9 | MIT | database layer (SQLite) |
| python-dotenv | 1.2.4 | BSD-3-Clause | reads `.env` |
| tzdata | 2026.6 | Apache-2.0 | time zones on Windows |
| faster-whisper | 1.2.1 | MIT | local speech recognition |
| ctranslate2 | 4.9.0 | MIT | Whisper inference engine |
| av (PyAV) | 19.0.2 | BSD-3-Clause | audio decoding for faster-whisper |
| tokenizers | 0.23.3 | Apache-2.0 | Whisper tokenizer |
| huggingface-hub, hf-xet | 2.4.0, 1.8.0 | Apache-2.0 | used by faster-whisper (the models are downloaded by `scripts/setup_whisper.py`) |
| onnxruntime | 1.32.0 | MIT | person detector (YOLOX), voice activity detection |
| numpy | 2.5.5 | BSD-3-Clause (with 0BSD, MIT, Zlib, CC0 parts) | frames and audio arrays |
| flatbuffers, protobuf | 25.12.19, 7.36.2 | Apache-2.0, BSD-3-Clause | used by onnxruntime |
| anyio, h11, idna, click | 4.16.0, 0.16.0, 3.20, 8.5.1 | MIT, MIT, BSD-3-Clause, BSD-3-Clause | used by the web server and HTTP clients |
| httpx2, httpcore2, truststore | 2.16.2, 2.16.2, 0.10.5 | BSD-3-Clause, BSD-3-Clause, MIT | used by huggingface-hub |
| certifi | 2026.10.23 | MPL-2.0 | CA certificates for HTTPS |
| filelock, fsspec, packaging, pyyaml, tqdm, colorama | — | MIT, BSD-3-Clause, Apache-2.0/BSD-2-Clause, MIT, MPL-2.0/MIT, BSD-3-Clause | used by the packages above |

Reel itself calls Google, Slack and the other services with the Python standard library
(`urllib`), so it needs no extra HTTP package for them.

## Python packages: development only

| Package | Licence | Use |
|---|---|---|
| pytest (with pluggy, iniconfig, pygments) | MIT, MIT, MIT, BSD-2-Clause | tests |
| ruff | MIT | lint and formatting |
| httpx (with httpcore) | BSD-3-Clause | FastAPI's test client |

## Programs

Reel does not ship these programs. The setup scripts download them into the project folder.

| Program | Version | Licence | Use |
|---|---|---|---|
| FFmpeg (gyan.dev "essentials" build) | 9.0.1 | GPL-3.0 (built with `--enable-gpl --enable-version3`) | probing, audio extraction, analysis, rendering, thumbnails; `scripts/setup_ffmpeg.py` |
| Ollama | 0.40.16 | MIT | runs the local LLM; `scripts/setup_ollama.py` |

Reel runs FFmpeg as a separate program and does not link to it. If you give FFmpeg to other
people, its GPL terms apply. The build's source code is available from gyan.dev and
ffmpeg.org.

## Models

| Model | Licence | Use |
|---|---|---|
| Whisper large-v3-turbo (Systran's CTranslate2 conversion of OpenAI Whisper) | MIT | Armenian transcription; `scripts/setup_whisper.py` |
| Whisper tiny (the same conversion) | MIT | tests |
| Gemma 3 4B (`gemma3:4b` through Ollama) | Gemma Terms of Use (free; not an OSI licence) | lesson boundaries, chapter labels and titles |
| YOLOX-tiny / YOLOX-nano ONNX (Megvii) | Apache-2.0 | person detector for the empty-board rule; `scripts/setup_person_model.py` |

Gemma may be used free of charge, but under Google's Gemma Terms of Use and its Prohibited
Use Policy, not under an open-source licence. Generating lesson chapters is allowed under
these terms. To use only OSI-licensed models, set `[llm] model` to such a model in Ollama, for
example a Qwen or Mistral model with an Apache-2.0 licence. The answers are checked against
the transcript either way (D-039, D-044).

## Services

YouTube Data API, Google Classroom API and the Slack Web API are free to use within their
quotas. They need the academy's own accounts (`docs/setup/google.md`). No paid API is used.

## Reel's own licence

Reel's own licence has not been chosen yet. It is a question for the user (PROGRESS.md).
