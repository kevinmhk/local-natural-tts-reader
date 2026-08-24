# Benchmarks

## Baseline environment

- Date: 2026-08-16
- Platform: Apple-silicon macOS 26.5.2
- Python: CPython 3.12.8 in `.venv`
- MLX-Audio: 0.4.8
- MLX: 0.32.0
- Default model: `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit`
- Current chunk policy: 280 target characters, 360 hard-limit characters

## Automated baseline

The fake engine validates the complete extraction, chunk, cache, pause, resume, and playback
coordination path without measuring speech quality. Record the final test count and quality
gate results in `docs/plan.md`.

## Hardware narration baseline

The first target-Mac run used the explicit local model setup and then forced
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` for verification and synthesis.

| Measurement | Observed result |
| --- | --- |
| Model revision | `1c6c0ff58c43afa8df571facde2efa077efd85e2` |
| Quantization | 6-bit |
| Speaker and language | Aiden, English |
| Input | 147 characters in one chunk |
| Load plus generation wall time | 4.14 seconds |
| Generated audio | 9.23 seconds, mono, 24 kHz PCM WAV |
| WAV size | 443,084 bytes |
| Real-time factor | 0.45, or about 2.23 times faster than playback |
| Cached replay wall time | 0.51 seconds with zero regenerated chunks |
| Hardware contract | 1 passed in 4.30 seconds |
| Offline end-to-end check | Passed with real MLX synthesis |
| `afplay` acceptance | Exit 0; one cached chunk played without regeneration |

The 4.14-second measurement includes process startup and lazy model loading, so it is a
conservative first-chunk result rather than steady-state generation throughput. Peak memory
was not captured. The playback command proved the audio-device path, but subjective listening
notes for omissions, repetition, pronunciation, noise, and prosody remain a human review item.

The original 1,200/1,800-character defaults are retained only as a historical baseline; existing
configuration files migrate to the current 280/360 policy once. A token-limited request is now
retried by the Qwen adapter as smaller sentence or clause segments before it is considered failed.

Do not promote a different quantization or chunk policy without recording comparable evidence
here and updating the Decision Log in `docs/plan.md`.
