#!/usr/bin/env bash
set -euo pipefail

# Wrapper for podman-compose with defaults that work in non-systemd environments.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

if [[ -z "${CONTAINERS_CONF:-}" ]]; then
  USER_CONTAINERS_CONF="${HOME}/.config/containers/containers.conf"
  if [[ -f "${USER_CONTAINERS_CONF}" ]]; then
    export CONTAINERS_CONF="${USER_CONTAINERS_CONF}"
  else
    export CONTAINERS_CONF="${PROJECT_ROOT}/tools/podman_containers.conf"
  fi
fi
export BUILDAH_ISOLATION="${BUILDAH_ISOLATION:-chroot}"
export PODMAN_SYSTEMD_UNIT="${PODMAN_SYSTEMD_UNIT:-0}"

PODMAN_ARGS=(--cgroup-manager=cgroupfs --events-backend=file)
if [[ -n "${PODMAN_EXTRA_ARGS:-}" ]]; then
  while read -r arg; do
    PODMAN_ARGS+=("${arg}")
  done < <(xargs -n1 <<< "${PODMAN_EXTRA_ARGS}")
fi

export PODMAN_COMPOSE_LOG_PATH="${PODMAN_COMPOSE_LOG_PATH:-${PROJECT_ROOT}/.podman-compose}"
mkdir -p "${PODMAN_COMPOSE_LOG_PATH}"

exec podman-compose --podman-args "${PODMAN_ARGS[*]}" "$@"
