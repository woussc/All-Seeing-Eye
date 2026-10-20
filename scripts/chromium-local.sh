#!/usr/bin/env bash
set -euo pipefail
# Headless checks of this local development app inside the managed container.
# Chromium's setuid sandbox is unavailable here; the cloud container supplies isolation.
export XDG_CONFIG_HOME=/workspace/All-Seeing-Eye/.runtime/chromium-config
export XDG_CACHE_HOME=/workspace/All-Seeing-Eye/.runtime/chromium-cache
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
exec /usr/bin/chromium --no-sandbox --disable-dev-shm-usage --disable-breakpad "$@"
