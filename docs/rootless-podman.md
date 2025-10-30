# Rootless Podman on Ubuntu 24.04

TurboHedi targets rootless Podman for local development and CI. Ubuntu 24.04 no longer enables a user systemd session by default, which leads to Podman reporting:

- `WARN The cgroupv2 manager is set to systemd but there is no systemd user session available`
- `Falling back to --cgroup-manager=cgroupfs`
- `sd-bus call: Interactive authentication required.: Permission denied`

Without a persistent systemd runtime, rootless Podman attempts to talk to system services (polkit) that require interactive authorization, so builds fail before containers are even created. The fix is to keep Podman on the `cgroupfs` manager and file-based event logger so it never needs to contact systemd or journald.

## Required packages

Install the rootless dependencies that Podman expects (these are the only commands that need sudo):

- `uidmap` – enables unprivileged user namespaces
- `dbus-user-session` – provides the user session bus used by Podman helpers
- `slirp4netns` – user-mode networking for rootless containers
- `fuse-overlayfs` – layered storage driver that works without kernel overlay permissions
- `containernetworking-plugins` – CNI plugins needed for networking

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
# run the printed apt-get commands (no systemd tweaks required)
make doctor
make build
make up
make down
```

`make build` / `make up` invoke `tools/podman_compose.sh`, which automatically
applies the non-systemd Podman flags. You can still call the wrapper directly if
you prefer a slimmer workflow script.

`make doctor` validates the runtime (cgroupfs, crun, fuse-overlayfs) and runs quick container build/run smoke tests.

## Need to recover a broken Podman cache?

If you previously ran Podman with the systemd cgroup manager, cached images and containers may still reference the old backend. Run `podman system prune -af` to clear them before retrying `make doctor`.
