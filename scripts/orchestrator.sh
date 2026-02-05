#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-$(dirname "$0")/settings.cfg}"
LOG_DIR="${LOG_DIR:-$(dirname "$0")/logs}"
STATE_DIR="${STATE_DIR:-$(dirname "$0")/state}"

mkdir -p "$LOG_DIR" "$STATE_DIR"

LOG_FILE="$LOG_DIR/orchestrator-$(date +"%Y%m%d").log"

timestamp() {
  date +"%Y-%m-%d %H:%M:%S"
}

log() {
  local level="$1"
  shift
  printf "%s [%s] %s\n" "$(timestamp)" "$level" "$*" | tee -a "$LOG_FILE"
}

run_cmd() {
  local description="$1"
  shift
  log "INFO" "$description: $*"
  if "$@"; then
    log "INFO" "$description: success"
    return 0
  fi
  local status=$?
  log "ERROR" "$description: failed (exit $status)"
  return "$status"
}

if [[ ! -f "$CONFIG_PATH" ]]; then
  log "ERROR" "Config not found: $CONFIG_PATH"
  exit 1
fi

declare -a SECTIONS=()
declare -A SOURCE_DIRS=()
declare -A TARGET_DIRS=()
declare -A SERVERS=()
declare -A BACKUP=()
declare -A RSYNC_OPTS=()

current=""
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"
  line="${line%%;*}"
  line="${line%"${line##*[![:space:]]}"}"
  line="${line#"${line%%[![:space:]]*}"}"
  [[ -z "$line" ]] && continue
  if [[ "$line" =~ ^\[(.+)\]$ ]]; then
    current="${BASH_REMATCH[1]}"
    SECTIONS+=("$current")
    continue
  fi
  if [[ -z "$current" ]]; then
    log "ERROR" "Key/value found before section header: $line"
    continue
  fi
  key="${line%%=*}"
  value="${line#*=}"
  key="${key%"${key##*[![:space:]]}"}"
  key="${key#"${key%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="${value#"${value%%[![:space:]]*}"}"
  case "$key" in
    source_dir) SOURCE_DIRS["$current"]="$value" ;;
    target_dir) TARGET_DIRS["$current"]="$value" ;;
    servers) SERVERS["$current"]="$value" ;;
    backup) BACKUP["$current"]="$value" ;;
    rsync_opts) RSYNC_OPTS["$current"]="$value" ;;
    *)
      log "ERROR" "Unknown key '$key' in section [$current]"
      ;;
  esac
done < "$CONFIG_PATH"

if [[ ${#SECTIONS[@]} -eq 0 ]]; then
  log "ERROR" "No sections found in $CONFIG_PATH"
  exit 1
fi

for section in "${SECTIONS[@]}"; do
  source_dir="${SOURCE_DIRS[$section]:-}"
  target_dir="${TARGET_DIRS[$section]:-}"
  servers="${SERVERS[$section]:-}"
  backup="${BACKUP[$section]:-false}"
  rsync_opts="${RSYNC_OPTS[$section]:--az}"

  log "INFO" "Processing [$section]"

  if [[ -z "$source_dir" || -z "$target_dir" || -z "$servers" ]]; then
    log "ERROR" "Missing required config in [$section] (source_dir/target_dir/servers)"
    continue
  fi

  if [[ ! -d "$source_dir" ]]; then
    log "ERROR" "Source directory not found: $source_dir"
    continue
  fi

  last_run_file="$STATE_DIR/${section}.last"
  if [[ -f "$last_run_file" ]]; then
    mapfile -t files < <(find "$source_dir" -type f -newer "$last_run_file")
  else
    mapfile -t files < <(find "$source_dir" -type f)
  fi

  if [[ ${#files[@]} -eq 0 ]]; then
    log "INFO" "No new files found for [$section]"
    touch "$last_run_file"
    continue
  fi

  for file in "${files[@]}"; do
    rel_path="${file#$source_dir/}"
    rel_dir="$(dirname "$rel_path")"
    for server in $servers; do
      remote_dir="${target_dir%/}/$rel_dir"
      run_cmd "Ensure remote dir ($server)" ssh "$server" "mkdir -p \"$remote_dir\""

      rsync_cmd=(rsync $rsync_opts)
      if [[ "$backup" == "true" ]]; then
        rsync_cmd+=(--backup --suffix=.bkp)
      fi
      rsync_cmd+=("$file" "${server}:${target_dir%/}/$rel_path")

      run_cmd "Copy $rel_path to $server" "${rsync_cmd[@]}"
    done
  done

  touch "$last_run_file"
done
