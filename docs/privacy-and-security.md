# Privacy and Security

## Runtime boundary

All imported sources, extracted text, cleaned previews, chunks, SQLite metadata, playback
positions, and generated WAV files remain in the configured local application data directory.
The MVP has no accounts, telemetry, analytics, crash upload, cloud synchronization, remote
URL ingestion, or network server.

`reader models setup` is the sole application command intended to use the network. It writes
model weights to the local models directory. Model loading sets `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1`; an unavailable model produces an error instead of a silent download.

## Input handling

- Paths are expanded and resolved before validation, and only readable regular files proceed.
- Supported types are checked by extension; PDF content is additionally checked by signature.
- Empty, oversized, malformed, encrypted, and excessive-page inputs are rejected.
- HTML is parsed from local bytes and never fetches scripts, styles, frames, images, or links.
- The original source is copied with a SHA-256 integrity check and never modified.
- Derived files use unique temporary names and atomic replacement.

## Local data

The default data directory is `~/Library/Application Support/LocalNaturalTTSReader/`. Set
`LOCAL_TTS_READER_DATA_DIR` before invoking the CLI to use another location. Deleting that
directory removes the local library, audio cache, models downloaded by the reader, and resume
state. Back up documents separately before deletion.

Cache pruning is dry-run-first and deletes only unreferenced WAV artifacts when `--delete` is
explicitly provided. It does not delete sources, models, or referenced audio.

## Logs and errors

User-visible status contains IDs, file names, counts, paths, and safe error categories. It does
not emit full document text unless `reader preview --stdout` is explicitly requested. The
current MVP does not write a persistent application log by default.

## Voice rights

Voice cloning and voice design are outside the MVP. Any future cloning flow must require a
local reference recording, an affirmative consent acknowledgement, and provenance metadata.
Users remain responsible for rights in source documents, reference voices, and generated
audio.
