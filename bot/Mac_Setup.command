#!/bin/bash
set -eu
cd -- "$(dirname -- "$0")"
for py in /usr/local/bin/python3 /opt/homebrew/bin/python3 python3; do
  if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then
    "$py" mac_setup.py || true
    read -r -p "Press Enter to close this window. " answer
    exit 0
  fi
done
echo 'Install Python 3.12 or newer from https://www.python.org/downloads/macos/'
echo 'Run its Install Certificates.command, then open this setup again.'
read -r -p "Press Enter to close this window. " answer
