#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
verification_root=$(mktemp -d "${TMPDIR:-/tmp}/local-tts-reader.XXXXXX")

cleanup() {
	rm -rf -- "$verification_root"
}
trap cleanup EXIT HUP INT TERM

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export UV_CACHE_DIR="$project_root/.cache/uv"
config_path="$verification_root/config.toml"

cd "$project_root"

command -v uv >/dev/null 2>&1 || {
	echo "error: uv is required" >&2
	exit 1
}
command -v jq >/dev/null 2>&1 || {
	echo "error: jq is required" >&2
	exit 1
}

echo "[offline] checking platform"
uv run --no-sync reader --config "$config_path" doctor >/dev/null

echo "[offline] importing fixture"
document_json=$(uv run --no-sync reader --config "$config_path" ingest tests/fixtures/text/two_paragraphs.txt)
document_id=$(printf '%s' "$document_json" | jq -er '.document_id')

echo "[offline] validating fake synthesis and cache"
uv run --no-sync reader --config "$config_path" speak "$document_id" \
	--engine fake --model fake-tone --no-play >/dev/null
uv run --no-sync reader --config "$config_path" status "$document_id" | jq -e '.state == "complete"' >/dev/null

if [ -n "${LOCAL_TTS_READER_MODEL_PATH:-}" ]; then
	echo "[offline] validating installed MLX model"
	uv run --no-sync reader --config "$config_path" models verify --model "$LOCAL_TTS_READER_MODEL_PATH" >/dev/null
	uv run --no-sync reader --config "$config_path" speak "$document_id" \
		--engine mlx-audio --model "$LOCAL_TTS_READER_MODEL_PATH" --no-play >/dev/null
fi

echo "[offline] verification passed"
