#!/usr/bin/env bash
set -euo pipefail

PYTHONPATH=src python -m ipo_update.runner "$@"
