#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
TODAY="$(date +%F)"

TODO_DIR_DEFAULT="${TODO_DIR:-$HOME/.todo}"
TODO_FILE_DEFAULT="${TODO_FILE:-$TODO_DIR_DEFAULT/todo.txt}"
DONE_FILE_DEFAULT="${DONE_FILE:-$TODO_DIR_DEFAULT/done.txt}"
LOG_FILE_DEFAULT="${LOG_FILE:-$TODO_DIR_DEFAULT/todo.log}"
EMAIL_TO_DEFAULT="${EMAIL_TO:-}"

TODO_DIR="$TODO_DIR_DEFAULT"
TODO_FILE="$TODO_FILE_DEFAULT"
DONE_FILE="$DONE_FILE_DEFAULT"
LOG_FILE="$LOG_FILE_DEFAULT"
EMAIL_TO="$EMAIL_TO_DEFAULT"

TODO_FILE_SET=false
DONE_FILE_SET=false
LOG_FILE_SET=false

log() {
  local level="$1"
  shift
  local message="$*"
  printf '%s [%s] %s\n' "$(date '+%F %T')" "$level" "$message" >>"$LOG_FILE"
}

ensure_files() {
  mkdir -p "$TODO_DIR"
  touch "$TODO_FILE" "$DONE_FILE" "$LOG_FILE"
}

usage() {
  cat <<USAGE
$SCRIPT_NAME - simple todo.txt CLI

Usage:
  $SCRIPT_NAME [options] <command> [args]

Commands:
  add "task text"        Add a task (prepends creation date).
  list [filter]           List tasks, optionally filtered.
  listdone [filter]       List completed tasks.
  done <number>           Complete a task by its list number.
  pri <number> <A-Z>      Set priority for a task.
  depri <number>          Remove priority for a task.
  alert                   Email overdue or due-today tasks (requires --email).
  help                    Show this help.

Options:
  -d, --dir DIR           Todo directory (default: $TODO_DIR_DEFAULT).
  -t, --todo FILE         Todo file (default: $TODO_FILE_DEFAULT).
  -D, --done FILE         Done file (default: $DONE_FILE_DEFAULT).
  -l, --log FILE          Log file (default: $LOG_FILE_DEFAULT).
  -e, --email ADDRESS     Email address for alerts.
  -h, --help              Show this help.

Notes:
  * todo.txt format: https://github.com/todotxt/todo.txt
  * Use due:YYYY-MM-DD in tasks for alerting.
USAGE
}

send_email() {
  local subject="$1"
  local body="$2"

  if [[ -z "$EMAIL_TO" ]]; then
    log "WARN" "Email requested but EMAIL_TO not set."
    return 1
  fi

  if command -v mail >/dev/null 2>&1; then
    printf '%s\n' "$body" | mail -s "$subject" "$EMAIL_TO"
    log "INFO" "Sent email alert to $EMAIL_TO"
  elif command -v mailx >/dev/null 2>&1; then
    printf '%s\n' "$body" | mailx -s "$subject" "$EMAIL_TO"
    log "INFO" "Sent email alert to $EMAIL_TO"
  else
    log "ERROR" "mail/mailx not found; unable to send email."
    return 1
  fi
}

list_tasks() {
  local file="$1"
  shift
  local filter="${*:-}"

  if [[ -n "$filter" ]]; then
    nl -ba "$file" | rg -i --fixed-strings -- "$filter" || true
  else
    nl -ba "$file"
  fi
}

add_task() {
  local task="$1"
  if [[ -z "$task" ]]; then
    echo "Task text required." >&2
    return 1
  fi
  printf '%s %s\n' "$TODAY" "$task" >>"$TODO_FILE"
  log "INFO" "Added task: $task"
}

complete_task() {
  local number="$1"
  if ! [[ "$number" =~ ^[0-9]+$ ]]; then
    echo "Task number must be numeric." >&2
    return 1
  fi

  local task
  task="$(sed -n "${number}p" "$TODO_FILE" || true)"
  if [[ -z "$task" ]]; then
    echo "No task found at number $number." >&2
    return 1
  fi

  printf 'x %s %s\n' "$TODAY" "$task" >>"$DONE_FILE"
  sed -i "${number}d" "$TODO_FILE"
  log "INFO" "Completed task #$number: $task"
}

set_priority() {
  local number="$1"
  local priority="$2"
  if ! [[ "$priority" =~ ^[A-Z]$ ]]; then
    echo "Priority must be A-Z." >&2
    return 1
  fi
  local task
  task="$(sed -n "${number}p" "$TODO_FILE" || true)"
  if [[ -z "$task" ]]; then
    echo "No task found at number $number." >&2
    return 1
  fi

  task="$(sed -E 's/^\([A-Z]\) //' <<<"$task")"
  sed -i "${number}s/.*/(${priority}) ${task}/" "$TODO_FILE"
  log "INFO" "Set priority $priority for task #$number"
}

remove_priority() {
  local number="$1"
  local task
  task="$(sed -n "${number}p" "$TODO_FILE" || true)"
  if [[ -z "$task" ]]; then
    echo "No task found at number $number." >&2
    return 1
  fi
  task="$(sed -E 's/^\([A-Z]\) //' <<<"$task")"
  sed -i "${number}s/.*/${task}/" "$TODO_FILE"
  log "INFO" "Removed priority for task #$number"
}

alert_tasks() {
  local due_today
  due_today="$(rg -n "due:${TODAY}" "$TODO_FILE" || true)"

  local overdue
  overdue="$(rg -n "due:[0-9]{4}-[0-9]{2}-[0-9]{2}" "$TODO_FILE" | awk -v today="$TODAY" -F: '{
      match($0, /due:([0-9]{4}-[0-9]{2}-[0-9]{2})/, arr);
      if (arr[1] != "" && arr[1] < today) print;
    }' || true)"

  if [[ -z "$due_today" && -z "$overdue" ]]; then
    echo "No due or overdue tasks found."
    log "INFO" "No due or overdue tasks found for alert."
    return 0
  fi

  local body
  body="Todo.txt alerts for $TODAY\n\n"
  if [[ -n "$due_today" ]]; then
    body+="Due today:\n${due_today}\n\n"
  fi
  if [[ -n "$overdue" ]]; then
    body+="Overdue:\n${overdue}\n"
  fi

  send_email "Todo.txt alerts for $TODAY" "$body"
}

main() {
  local args=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -d|--dir)
        TODO_DIR="$2"
        shift 2
        ;;
      -t|--todo)
        TODO_FILE="$2"
        TODO_FILE_SET=true
        shift 2
        ;;
      -D|--done)
        DONE_FILE="$2"
        DONE_FILE_SET=true
        shift 2
        ;;
      -l|--log)
        LOG_FILE="$2"
        LOG_FILE_SET=true
        shift 2
        ;;
      -e|--email)
        EMAIL_TO="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      -* )
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
      *)
        args+=("$1")
        shift
        ;;
    esac
  done

  if [[ ${#args[@]} -eq 0 ]]; then
    usage
    exit 1
  fi

  if [[ "$TODO_DIR" != "$TODO_DIR_DEFAULT" ]]; then
    if [[ "$TODO_FILE_SET" == false ]]; then
      TODO_FILE="$TODO_DIR/todo.txt"
    fi
    if [[ "$DONE_FILE_SET" == false ]]; then
      DONE_FILE="$TODO_DIR/done.txt"
    fi
    if [[ "$LOG_FILE_SET" == false ]]; then
      LOG_FILE="$TODO_DIR/todo.log"
    fi
  fi

  ensure_files

  local command="${args[0]}"
  case "$command" in
    add)
      add_task "${args[*]:1}"
      ;;
    list)
      list_tasks "$TODO_FILE" "${args[*]:1}"
      ;;
    listdone)
      list_tasks "$DONE_FILE" "${args[*]:1}"
      ;;
    done)
      complete_task "${args[1]:-}"
      ;;
    pri)
      set_priority "${args[1]:-}" "${args[2]:-}"
      ;;
    depri)
      remove_priority "${args[1]:-}"
      ;;
    alert)
      alert_tasks
      ;;
    help|--help|-h)
      usage
      ;;
    *)
      echo "Unknown command: $command" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
