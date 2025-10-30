#!/usr/bin/env bash
set -euo pipefail

cat <<'MSG'
TurboHedi rootless Podman bootstrap

Install the rootless prerequisites that Podman expects (run with sudo):

  sudo apt-get update
  sudo apt-get install -y uidmap dbus-user-session slirp4netns fuse-overlayfs containernetworking-plugins

These packages provide the user-namespace helpers, networking shims, and overlay storage driver that keep Podman working without a systemd user session or journald.
MSG

CONFIG_DIR="${HOME}/.config/containers"
mkdir -p "${CONFIG_DIR}"

cat <<'CONF' > "${CONFIG_DIR}/containers.conf"
[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
runtime = "crun"

[engine.runtimes]
crun = ["/usr/bin/crun"]

[containers]
pids_limit = 4096
CONF

cat <<'CONF' > "${CONFIG_DIR}/storage.conf"
[storage]
driver = "overlay"
runroot = "/run/user/$UID/containers"
graphroot = "$HOME/.local/share/containers/storage"
[storage.options]
mount_program = "/usr/bin/fuse-overlayfs"
CONF

cat <<'CONF' > "${CONFIG_DIR}/registries.conf"
unqualified-search-registries = ["docker.io"]
CONF

BASHRC="${HOME}/.bashrc"
declare -A ENV_EXPORTS=(
  ["export CONTAINERS_CGROUP_MANAGER=cgroupfs"]=0
  ["export CONTAINERS_NO_PROMPT=1"]=0
  ["export BUILDAH_FORMAT=docker"]=0
)

if [[ ! -f "${BASHRC}" ]]; then
  touch "${BASHRC}"
fi

for line in "${!ENV_EXPORTS[@]}"; do
  if ! grep -Fq "${line}" "${BASHRC}"; then
    printf '\n%s\n' "${line}" >> "${BASHRC}"
  fi
done

cat <<'MSG'

Next steps:
  1. Start a fresh shell so the exported variables take effect.
  2. Run: make doctor
  3. Run: make build

If make doctor reports any failures, resolve them before building TurboHedi.
MSG
