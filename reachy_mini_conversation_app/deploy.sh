#!/usr/bin/env bash
# Deploy Reachy Mini Conversation App to robot
# Usage:
#   ./deploy.sh            — git pull + reinstall on robot (standard deploy)
#   ./deploy.sh --rsync    — rsync local source to robot (fast local dev)
#   ./deploy.sh --env      — sync .env file to robot
#   ./deploy.sh --profiles — sync profiles/ to robot
#   ./deploy.sh --all      — rsync + env + profiles + reinstall
#   ./deploy.sh --restart  — restart the app after deploy (any flag combo)

set -euo pipefail

ROBOT_IP="10.0.0.60"
ROBOT_USER="pollen"
ROBOT_PASS="root"
ROBOT_REPO="~/dev/reachy_mini_conversation_app/reachy_mini_conversation_app"
LOCAL_REPO="$(cd "$(dirname "$0")/reachy_mini_conversation_app" && pwd)"

SSH="sshpass -p '$ROBOT_PASS' ssh -o StrictHostKeyChecking=no -o PreferredAuthentications=password $ROBOT_USER@$ROBOT_IP"
SCP="sshpass -p '$ROBOT_PASS' scp -o StrictHostKeyChecking=no -o PreferredAuthentications=password"
RSYNC="sshpass -p '$ROBOT_PASS' rsync -avz --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' --exclude='*.egg-info'"

DO_RSYNC=false
DO_ENV=false
DO_PROFILES=false
DO_RESTART=false
DO_GIT=true

for arg in "$@"; do
  case $arg in
    --rsync)    DO_RSYNC=true; DO_GIT=false ;;
    --env)      DO_ENV=true; DO_GIT=false ;;
    --profiles) DO_PROFILES=true; DO_GIT=false ;;
    --all)      DO_RSYNC=true; DO_ENV=true; DO_PROFILES=true ;;
    --restart)  DO_RESTART=true ;;
    --help|-h)
      grep '^# ' "$0" | sed 's/^# //'
      exit 0
      ;;
  esac
done

echo "→ Deploying to $ROBOT_USER@$ROBOT_IP"

# Git pull + pip install (standard deploy)
if $DO_GIT; then
  echo "→ Pulling latest from git..."
  eval "$SSH 'cd $ROBOT_REPO && git pull && /venvs/apps_venv/bin/pip install -e . -q && echo Done'"
fi

# Rsync local source (fast dev iteration — no git push needed)
if $DO_RSYNC; then
  echo "→ Rsyncing source..."
  eval "$RSYNC \
    '$LOCAL_REPO/src/reachy_mini_conversation_app/' \
    '$ROBOT_USER@$ROBOT_IP:$ROBOT_REPO/src/reachy_mini_conversation_app/'"
fi

# Sync .env to robot (stored at ~/.reachy_mini/.env)
if $DO_ENV; then
  ENV_FILE="$LOCAL_REPO/.env"
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "✗ No .env found at $ENV_FILE — copy .env.example and fill in your values"
    exit 1
  fi
  echo "→ Syncing .env..."
  eval "$SSH 'mkdir -p ~/.reachy_mini'"
  eval "$SCP '$ENV_FILE' '$ROBOT_USER@$ROBOT_IP:~/.reachy_mini/.env'"
  echo "  ✓ .env deployed to ~/.reachy_mini/.env"
fi

# Sync profiles/
if $DO_PROFILES; then
  echo "→ Syncing profiles/..."
  eval "$RSYNC \
    '$LOCAL_REPO/profiles/' \
    '$ROBOT_USER@$ROBOT_IP:$ROBOT_REPO/profiles/'"
fi

# Restart app via launcher API
if $DO_RESTART; then
  echo "→ Restarting app..."
  eval "$SSH 'curl -s -X POST http://localhost:8000/api/apps/restart-current-app || true'"
  echo "  ✓ Restart requested"
fi

echo "✓ Deploy complete"
