# Model Profiles

## Default narrator

The MVP uses one Qwen3-TTS CustomVoice profile through MLX-Audio:

```yaml
engine: mlx-audio
mode: custom_voice
model: mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit
speaker: Aiden
language: English
instruction: Calm, clear long-form narration with restrained expression and natural pauses.
speed: 1.0
```

MLX-Audio 0.4.8 is the first tested runtime dependency. The model is an MLX-community 6-bit
conversion and is downloaded only by `reader models setup`. Normal `speak`, `resume`, and
`models verify` resolve existing local files with Hugging Face offline mode enabled.

The default model and narrator values live in `~/.local-natural-tts-reader/config.toml` and can
be changed there. `reader speak` options override those stored defaults for one invocation.

## Capability boundary

CustomVoice supports preset speakers and optional style instructions. The MVP intentionally
does not expose Base-model voice cloning or VoiceDesign. Those modes require different model
weights, generation methods, consent controls, provenance, and cache inputs.

The CLI accepts voice, language, instruction, and speed. Qwen CustomVoice does not expose a
numerical speed parameter, so non-default speed is expressed as an explicit narration
instruction and remains part of the cache identity. A later native playback backend may
provide high-quality real-time speed adjustment without regenerating audio.

## Cache identity

Audio is reused only when chunk text, model, revision, mode, speaker, language, instruction,
speed, generation parameters, and adapter version match. Each completed WAV is validated
before it is atomically promoted to its content-addressed path.

## Alternative profiles

Qwen3-TTS 0.6B and Kokoro are post-MVP candidates for faster bulk reading. They must implement
the same `TtsEngine` contract and pass hardware, fidelity, cache, and offline tests before
becoming user-selectable defaults.
