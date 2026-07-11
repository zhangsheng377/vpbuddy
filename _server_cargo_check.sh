#!/bin/bash
set -e
export PATH="$HOME/.cargo/bin:$PATH"

# Install rust if needed
if ! command -v cargo &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal 2>&1
    source "$HOME/.cargo/env"
fi

cd /data/vpbuddy/server
git checkout main
git pull origin main 2>&1

cd vpbuddy-client/src-tauri

# Install system deps for cpal (alsa)
apt-get update -qq && apt-get install -y -qq libasound2-dev pkg-config 2>&1 | tail -3

cargo check 2>&1 | tail -40
echo "---EXIT CODE: $?---"
