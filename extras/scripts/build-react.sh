#!/usr/bin/env bash
# Build the React fork and package it for the production Docker image.
# Usage: extras/scripts/build-react.sh [path-to-react-checkout]   (default: ./react)
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
react_dir="$(cd "${1:-$repo_root/react}" && pwd)"
vendor="$repo_root/extras/docker/vendor"

cd "$react_dir"
[ -d node_modules ] || npm ci --no-audit --no-fund
npm run build
mkdir -p "$vendor"
rm -f "$vendor"/*.tgz
npm pack --pack-destination "$vendor"
echo "Packaged into $vendor, now rebuild the image."
