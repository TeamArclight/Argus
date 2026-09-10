#!/usr/bin/env bash
# Regenerate services/intelligence/requirements.txt as an exact pinned lock.
#
# Audit finding H-14: the service installed from open version ranges with no
# lock, so builds drifted. Run this once from a machine with PyPI access, commit
# the result, and every subsequent image build installs identical code.
#
#   ./scripts/freeze-requirements.sh
#
# Resolution happens inside the same base image the service ships on, so the
# lock matches what production actually installs -- not whatever happens to be
# on the developer's laptop.
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_IMAGE="${ARGUS_PY_BASE_IMAGE:-python:3.11-slim}"
OUT="${SERVICE_DIR}/requirements.txt"

if [ ! -f "${SERVICE_DIR}/requirements.in" ]; then
  echo "ERROR: requirements.in not found in ${SERVICE_DIR}" >&2
  exit 1
fi

echo "Resolving ${SERVICE_DIR}/requirements.in inside ${BASE_IMAGE} ..."

docker run --rm \
  -v "${SERVICE_DIR}:/work" \
  -w /work \
  "${BASE_IMAGE}" \
  sh -eux -c '
    pip install --no-cache-dir --upgrade pip pip-tools >/dev/null
    pip-compile \
      --quiet \
      --no-header \
      --strip-extras=false \
      --output-file /work/.requirements.locked \
      /work/requirements.in
  '

{
  echo "# GENERATED FILE -- do not edit by hand."
  echo "#"
  echo "# Regenerate with: ./scripts/freeze-requirements.sh"
  echo "# Source of truth: requirements.in"
  echo "#"
  echo "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# Base image: ${BASE_IMAGE}"
  echo
  cat "${SERVICE_DIR}/.requirements.locked"
} > "${OUT}"

rm -f "${SERVICE_DIR}/.requirements.locked"

echo "Wrote ${OUT}"
echo "Review the diff and commit it."
