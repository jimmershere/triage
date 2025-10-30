#!/usr/bin/env bash
set -euo pipefail

STATUS=0

ok() {
  printf 'OK   %s\n' "$1"
}

fail() {
  printf 'FAIL %s\n' "$1"
  STATUS=1
}

section() {
  printf '\n== %s ==\n' "$1"
}

section "Environment"
if command -v podman >/dev/null 2>&1; then
  ok "podman found: $(podman --version)"
else
  fail "podman command not found"
fi

if ! command -v podman >/dev/null 2>&1; then
  echo "Install podman and re-run doctor."
  exit 1
fi

INFO_JSON=$(podman info --format '{{json .}}')

section "Podman configuration"
OCIRUNTIME=$(python3 - <<'PY' "$INFO_JSON"
import json, sys

def _strip(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""

info = json.loads(sys.argv[1])
host = info.get("Host") or {}

runtime = ""
candidate = host.get("OCIRuntime")
if isinstance(candidate, str):
    runtime = candidate.strip()
elif isinstance(candidate, dict):
    runtime = _strip(candidate.get("Name")) or _strip(candidate.get("name"))
    if not runtime:
        runtime_path = candidate.get("Path") or candidate.get("path")
        if isinstance(runtime_path, str) and runtime_path.strip():
            runtime = runtime_path.strip().rsplit("/", 1)[-1]
if not runtime:
    default = host.get("OCIRuntimeDefault")
    if isinstance(default, str):
        runtime = default.strip()
if not runtime:
    runtimes = host.get("OCIRuntimes")
    if isinstance(runtimes, dict):
        default_runtime = host.get("OCIRuntime")
        if isinstance(default_runtime, str) and default_runtime.strip():
            runtime = default_runtime.strip()
        if not runtime:
            default_runtime = host.get("OCIRuntimeDefault")
            if isinstance(default_runtime, str) and default_runtime.strip():
                runtime = default_runtime.strip()
        if not runtime:
            # fall back to the first configured runtime name
            for name in runtimes:
                if isinstance(name, str) and name.strip():
                    runtime = name.strip()
                    break

print(runtime)
PY
)
if [[ "${OCIRUNTIME}" == "crun" ]]; then
  ok "OCI runtime is crun"
else
  fail "OCI runtime is '${OCIRUNTIME}' (expected crun)"
fi

CGROUP_MANAGER=$(python3 - <<'PY' "$INFO_JSON"
import json, sys

info = json.loads(sys.argv[1])
host = info.get("Host") or {}

value = ""
for key in ("CgroupManager", "cgroupManager", "Cgroupmanager"):
    candidate = host.get(key)
    if isinstance(candidate, str) and candidate.strip():
        value = candidate.strip()
        break

print(value)
PY
)
if [[ "${CGROUP_MANAGER}" == "cgroupfs" ]]; then
  ok "Cgroup manager is cgroupfs"
else
  fail "Cgroup manager is '${CGROUP_MANAGER}' (expected cgroupfs)"
fi

GRAPH_DRIVER=$(python3 - <<'PY' "$INFO_JSON"
import json, sys

info = json.loads(sys.argv[1])
store = info.get("Store") or {}

value = ""
for key in ("GraphDriverName", "GraphDriver", "graphDriverName"):
    candidate = store.get(key)
    if isinstance(candidate, str) and candidate.strip():
        value = candidate.strip()
        break

print(value)
PY
)
if [[ "${GRAPH_DRIVER}" == "overlay" ]]; then
  ok "Graph driver is overlay"
else
  fail "Graph driver is '${GRAPH_DRIVER}' (expected overlay)"
fi

GRAPH_OPTIONS=$(python3 - <<'PY' "$INFO_JSON"
import json, sys
opts = json.loads(sys.argv[1]).get("Store", {}).get("GraphOptions", [])
if isinstance(opts, dict):
    values = [f"{k}={v}" for k, v in opts.items()]
else:
    values = [str(item) for item in opts]
print('\n'.join(values))
PY
)
if grep -q "mount_program=/usr/bin/fuse-overlayfs" <<< "${GRAPH_OPTIONS}"; then
  ok "fuse-overlayfs mount program is configured"
else
  fail "fuse-overlayfs mount program not detected"
fi

section "Configuration files"
CONFIG_FILES=("${HOME}/.config/containers/containers.conf" "${HOME}/.config/containers/storage.conf" "${HOME}/.config/containers/registries.conf")
for file in "${CONFIG_FILES[@]}"; do
  if [[ -f "${file}" ]]; then
    ok "Found ${file}"
  else
    fail "Missing ${file}"
  fi
done

section "Subuid/Subgid"
USER_NAME=$(id -un)
if grep -q "^${USER_NAME}:" /etc/subuid 2>/dev/null; then
  ok "/etc/subuid contains entry for ${USER_NAME}"
else
  fail "Add '${USER_NAME}' to /etc/subuid"
fi
if grep -q "^${USER_NAME}:" /etc/subgid 2>/dev/null; then
  ok "/etc/subgid contains entry for ${USER_NAME}"
else
  fail "Add '${USER_NAME}' to /etc/subgid"
fi

section "Runtime checks"
if podman run --rm alpine:3.20 sh -lc 'id; cat /proc/self/cgroup | head -n1'; then
  ok "Container run test passed"
else
  fail "Container run test failed"
fi

TMP_DIR=$(mktemp -d)
trap 'podman rmi -f turbohedi-doctor-test >/dev/null 2>&1 || true; rm -rf "${TMP_DIR}"' EXIT
cat <<'CF' > "${TMP_DIR}/Containerfile"
FROM alpine:3.20
RUN echo ok
CF

if podman build -t turbohedi-doctor-test "${TMP_DIR}" >/dev/null; then
  ok "Container build test passed"
else
  fail "Container build test failed"
fi

if podman rmi -f turbohedi-doctor-test >/dev/null; then
  ok "Removed temporary doctor image"
fi

if [[ ${STATUS} -eq 0 ]]; then
  echo "\nDoctor checks completed successfully."
else
  echo "\nDoctor detected issues. See failures above."
fi

exit ${STATUS}
