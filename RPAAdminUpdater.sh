#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/NoCoder4you/RPA-Admin.git"

TARGET="/home/pi/discord-bots/bots/RPA Admin"
CACHE_BASE="/home/pi/discord-bots/.repo_cache"
CACHE="$CACHE_BASE/RPA-Admin"

echo "[Updater] Cache:  $CACHE"
echo "[Updater] Target: $TARGET"
echo "[Updater] Repo:   $REPO_URL"
echo "----------------------------------"

mkdir -p "$TARGET" "$CACHE_BASE"

if [[ ! -d "$CACHE/.git" ]]; then
  echo "[Updater] Creating cache..."
  rm -rf "$CACHE"
  git clone "$REPO_URL" "$CACHE"
else
  echo "[Updater] Updating cache..."
  git -C "$CACHE" fetch --all --prune
fi

# Determine the default branch without assuming the remote HEAD is configured.
BRANCH="$(git -C "$CACHE" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@' || true)"
if [[ -z "${BRANCH:-}" ]]; then
  if git -C "$CACHE" show-ref --verify --quiet refs/remotes/origin/main; then
    BRANCH="main"
  elif git -C "$CACHE" show-ref --verify --quiet refs/remotes/origin/master; then
    BRANCH="master"
  else
    BRANCH="main"
  fi
fi

echo "[Updater] Branch: $BRANCH"

git -C "$CACHE" reset --hard "origin/$BRANCH"
git -C "$CACHE" submodule update --init --recursive

echo "----------------------------------"
echo "[Updater] Syncing files..."

# Preserve environment-specific files/directories that live only on the target host.
# The protect filters stop rsync from deleting or overwriting these paths even when
# --delete is active, while the matching excludes keep the cache copy from replacing
# them if similarly named files appear in the repository later.
RSYNC_ENV_GUARDS=(
  --filter='P .env'
  --filter='P .env.*'
  --filter='P env/'
  --filter='P .venv/'
  --filter='P venv/'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='env/'
  --exclude='.venv/'
  --exclude='venv/'
)

rsync -a --delete \
  --filter='P bot.py' \
  --exclude='bot.py' \
  --exclude='.git' \
  --exclude='.repo_cache' \
  "${RSYNC_ENV_GUARDS[@]}" \
  "$CACHE/" "$TARGET/"

echo "[Updater] Sync complete -> $TARGET"
