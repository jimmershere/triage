#!/usr/bin/env bash
set -euo pipefail

cat <<'MSG'
TurboHedi rootless Podman bootstrap

Run the following commands with sudo to install required dependencies and enable a persistent user systemd runtime (linger) so rootless Podman can avoid "no systemd user session" errors:

  sudo apt-get update
  sudo apt-get install -y uidmap dbus-user-session slirp4netns fuse-overlayfs containernetworking-plugins
  sudo loginctl enable-linger $(id -u)

The enable-linger command ensures your user has a dedicated systemd runtime even when not logged in, preventing sd-bus authentication prompts during Podman operations.
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
  1. Log out and back in (or run: systemctl --user daemon-reload) after enabling linger.
  2. Run: make doctor
  3. Run: make build

If make doctor reports any failures, resolve them before building TurboHedi.
MSG
