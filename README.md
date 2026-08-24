# Local Natural TTS Reader

## Overview

Local Natural TTS Reader is a private, offline-first document narrator for Apple-silicon
macOS. It imports local TXT, Markdown, HTML, and born-digital PDF files; extracts and
conservatively cleans their readable prose; creates deterministic chunks; generates WAV
audio with [MLX-Audio](https://github.com/Blaizzy/mlx-audio) and
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS); and plays the result with resumable,
cache-aware progress.

The runtime does not summarize, rewrite, fetch remote URLs, or upload document text. Model
and dependency acquisition require network access during explicit setup. Normal import,
preview, synthesis, caching, and playback are local.

## Requirements

- Apple-silicon Mac running macOS.
- Python 3.12 managed through `uv`.
- The built-in `/usr/bin/afplay` command for audible CLI playback.
- Approximately 3 GB for the default Qwen3-TTS model plus generated audio.

Scanned/image-only PDFs require OCR and are rejected by this MVP with a `needs_ocr` error.
Complex multi-column PDFs, tables, formulas, and unusual reading orders require preview.

## Install

Create the isolated environment and install the CLI, quality tools, and MLX-Audio runtime:

```sh
UV_CACHE_DIR=.cache/uv uv sync --group dev --extra mlx
```

Verify the local platform without downloading a model:

```sh
UV_CACHE_DIR=.cache/uv uv run --extra mlx reader doctor
```

## Explicit model setup

This is the only reader command that downloads model weights:

```sh
UV_CACHE_DIR=.cache/uv uv run --extra mlx reader models setup
```

The default is `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit` on
[Hugging Face](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit).
Verify that the model is present without network access:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  UV_CACHE_DIR=.cache/uv uv run --no-sync reader models verify
```

The first `reader` command creates `~/.local-natural-tts-reader/config.toml`. This TOML file
controls the local workspace, model, narrator defaults, and safety limits. The default workspace
is `~/.local-natural-tts-reader/`; edit `data_dir` in the config to relocate it. For an explicit
alternate profile, pass `--config /path/to/config.toml` before the reader subcommand.

## Usage

For a complete human-oriented walkthrough of installation, model setup, configuration,
commands, maintenance, and troubleshooting, open
[`docs/manual.html`](docs/manual.html) in a browser. The manual is a standalone offline HTML
file with no remote assets.

Import a document and save the returned document ID:

```sh
uv run --no-sync reader ingest path/to/document.pdf
uv run --no-sync reader preview DOCUMENT_ID --stdout
```

Forgot an earlier ID? List the local library with source, import, playback, and cached-audio
metadata:

```sh
uv run --no-sync reader documents list
```

To permanently remove one reader-managed document, inspect the scope first and then confirm the
interactive prompt. This creates no backup; the original source file outside the reader workspace
is never deleted.

```sh
uv run --no-sync reader documents delete DOCUMENT_ID --dry-run
uv run --no-sync reader documents delete DOCUMENT_ID
```

Use `--keep-exports` to retain complete WAV exports. After deletion, the command offers to prune
all unreferenced WAVs in the global cache.

Start local Qwen narration with the default Aiden voice:

```sh
uv run --no-sync reader speak DOCUMENT_ID
```

Create one WAV containing the complete cached narration in chunk order:

```sh
uv run --no-sync reader export wav DOCUMENT_ID
```

Exports are named `SOURCE_NAME-DOCUMENT_ID-PROFILE_HASH.wav` so they remain understandable
outside the reader while avoiding collisions between narration profiles.

Pause from another terminal and resume later:

```sh
uv run --no-sync reader pause DOCUMENT_ID
uv run --no-sync reader resume DOCUMENT_ID
```

Use the fake engine for a fast non-audible end-to-end check:

```sh
uv run --no-sync reader speak DOCUMENT_ID \
  --engine fake --model fake-tone --no-play
```

Inspect status and cache use:

```sh
uv run --no-sync reader status DOCUMENT_ID
uv run --no-sync reader cache inspect
uv run --no-sync reader cache prune --dry-run
```

Changing voice, language, instruction, speed, model, or generation settings creates a new
audio cache identity. Changing playback position does not invalidate audio.

For Qwen generation-limit troubleshooting without exposing the document text, add `--debug` to
`reader speak`. It writes structured attempt, token-cap, token-count, elapsed-time, and fragment
size diagnostics to stderr and `~/.local-natural-tts-reader/error.log`. Token-limit and terminal
synthesis failures are appended to that file even when `--debug` is omitted.

## Configuration

Run `reader doctor` once to create the default configuration, then edit
`~/.local-natural-tts-reader/config.toml`. It contains the workspace path, default Qwen model,
voice, language, instruction, speed, source/PDF limits, chunk limits, pauses, and minimum free
space. Command-specific narration options such as `reader speak --voice Ryan` override the
stored default for that invocation.

The default chunk limits are deliberately short (`280` target characters and `360` hard-limit
characters) for reliable Qwen3-TTS completion. If an individual request still reaches Qwen's
generation limit, the MLX adapter retries it as smaller complete sentences or packed natural
phrases and joins valid results with brief pauses. It never splits a word, acronym, or partially
quoted token for synthesis. WAVs that end in more than three seconds of silence are rejected rather
than cached. On the next reader command, the former default pair of `1200` and `1800` is migrated
once to these safer values.

Use a separate config and workspace for a different profile or test run:

```sh
uv run --no-sync reader --config /path/to/reader.toml doctor
```

If it does not exist, the reader creates the file and uses its parent directory as that profile's
initial workspace. No `LOCAL_TTS_READER_DATA_DIR` environment variable is read by the reader.

## Offline verification

Run the deterministic fake-engine verification with dependency resolution disabled:

```sh
scripts/verify_offline_runtime.sh
```

To include a real preinstalled model without audible playback:

```sh
LOCAL_TTS_READER_MODEL_PATH=/absolute/path/to/model \
  scripts/verify_offline_runtime.sh
```

## Development

Run quality gates in the required order:

```sh
UV_CACHE_DIR=.cache/uv uv run ruff format .
UV_CACHE_DIR=.cache/uv uv run ruff check .
UV_CACHE_DIR=.cache/uv uv run ty check src tests
UV_CACHE_DIR=.cache/uv uv run pytest
```

The hardware test is opt-in and never downloads weights:

```sh
LOCAL_TTS_READER_MODEL_PATH=/absolute/path/to/model \
  UV_CACHE_DIR=.cache/uv uv run pytest -m hardware
```

## Troubleshooting

- `model is not installed locally`: run `reader models setup` once with network access.
- `MLX-Audio is not installed`: rerun `uv sync --extra mlx`.
- `PDF has no useful text layer`: the PDF is scanned or lacks a readable text layer; OCR is
  outside the MVP.
- Playback waits at `buffering`: generation has not produced the next complete WAV yet.
- Low disk error: inspect the audio cache and run dry-run pruning before deleting anything.

See `docs/extraction-policy.md`, `docs/model-profiles.md`, and
`docs/privacy-and-security.md` for behavioral details. The complete architecture and living
implementation record are in `docs/design-and-architecture.md` and `docs/plan.md`.

## License

The project source is licensed under the MIT License. Model weights, preset voices, input
documents, and generated audio can have separate terms and rights requirements. The default
MLX-community model page identifies its weights as Apache-2.0; verify current upstream terms
before redistribution or commercial use.
