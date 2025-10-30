# Rootless Podman on Ubuntu 24.04

TurboHedi targets rootless Podman for local development and CI. Ubuntu 24.04 no longer enables a user systemd session by default, which leads to Podman reporting:

- `WARN The cgroupv2 manager is set to systemd but there is no systemd user session available`
- `Falling back to --cgroup-manager=cgroupfs`
- `sd-bus call: Interactive authentication required.: Permission denied`

Without a persistent systemd runtime, rootless Podman attempts to talk to system services (polkit) that require interactive authorization, so builds fail before containers are even created.

## Required packages

Install the rootless dependencies that Podman expects:

- `uidmap` – enables unprivileged user namespaces
- `dbus-user-session` – provides the user session bus used by Podman helpers
- `slirp4netns` – user-mode networking for rootless containers
- `fuse-overlayfs` – layered storage driver that works without kernel overlay permissions
- `containernetworking-plugins` – CNI plugins needed for networking

## Enable linger (persistent user systemd)

A rootless Podman runtime needs a per-user systemd instance for sd-bus interactions. `loginctl enable-linger <uid>` creates that environment even on headless servers, preventing `sd-bus call: Interactive authentication required` errors.

## Configuration applied by `make bootstrap`

The bootstrap script writes rootless-safe Podman defaults under `~/.config/containers`:

- `containers.conf`
  - `cgroup_manager = "cgroupfs"` avoids systemd cgroup dependencies.
  - `events_logger = "file"` sidesteps journald requirements.
  - `runtime = "crun"` selects the OCI runtime optimized for rootless.
- `storage.conf`
  - Forces the `overlay` driver under `~/.local/share/containers/storage`.
  - Configures `/usr/bin/fuse-overlayfs` for layered storage in user space.
- `registries.conf`
  - Limits unqualified image searches to `docker.io` for predictable pulls.

It also ensures your shell exports:

```bash
export CONTAINERS_CGROUP_MANAGER=cgroupfs
export CONTAINERS_NO_PROMPT=1
export BUILDAH_FORMAT=docker
```

## Usage workflow

```bash
make bootstrap
# follow the printed sudo instructions and enable linger
# log out/in (or reboot) so the new user systemd runtime starts
make doctor
make build
make up
```

`make doctor` validates the runtime (cgroupfs, crun, fuse-overlayfs) and runs quick container build/run smoke tests.

## Temporary fallback (use sparingly)

If you must build before logging out/in after enabling linger, run:

```bash
sudo -E CONTAINERS_CGROUP_MANAGER=cgroupfs podman-compose build
```

This bypasses the rootless runtime, but the permanent fix is to enable linger and start a fresh login session so `make doctor` passes cleanly.
