#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cp "$HERE/commons-sync.service" /etc/systemd/system/
cp "$HERE/commons-sync.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now commons-sync.timer
systemctl list-timers --all | grep commons || echo "timer listed above if present"
