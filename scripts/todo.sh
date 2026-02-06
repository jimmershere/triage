#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
TODAY="$(date +%F)"
DEFAULT_GROUP="misc"

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
  if [[ ! -s "$TODO_FILE" ]]; then
    printf '[%s]\n' "$DEFAULT_GROUP" >"$TODO_FILE"
  fi
}

usage() {
  cat <<USAGE
$SCRIPT_NAME - simple todo.txt CLI

Usage:
  $SCRIPT_NAME [options] <command> [args]

Commands:
  add [options] "task text"
                          Add a task (uses creation date).
  list [filter]           List tasks, optionally filtered.
  listdone [filter]       List completed tasks.
  done <number>           Complete a task by its list number.
  pri <number> <A-Z>      Set priority for a task.
  depri <number>          Remove priority for a task.
  update|-u <number> [options]
                          Update task group/priority/description.
  alert                   Email overdue or due-today tasks (requires --email).
  help                    Show this help.

Options:
  -d, --dir DIR           Todo directory (default: $TODO_DIR_DEFAULT).
  -t, --todo FILE         Todo file (default: $TODO_FILE_DEFAULT).
  -D, --done FILE         Done file (default: $DONE_FILE_DEFAULT).
  -l, --log FILE          Log file (default: $LOG_FILE_DEFAULT).
  -e, --email ADDRESS     Email address for alerts.
  -h, --help              Show this help.

Add options:
  -g GROUP                Group name (default: $DEFAULT_GROUP).
  -p PRIORITY             Priority letter A-Z.

Update options:
  -g GROUP                New group name.
  -p PRIORITY             New priority letter A-Z.
  -d DESCRIPTION          New task description.

Notes:
  * Format: [group] headers with items as "(A) description YYYY-MM-DD".
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

normalize_group_name() {
  local name="$1"
  name="$(printf '%s' "$name" | sed -E 's/^\[//; s/\]$//; s/^[[:space:]]+|[[:space:]]+$//g')"
  if [[ -z "$name" ]]; then
    name="$DEFAULT_GROUP"
  fi
  printf '%s' "$name"
}

parse_records() {
  awk -v default_group="$DEFAULT_GROUP" '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    BEGIN { group = default_group; order = 0 }
    /^[[:space:]]*$/ { next }
    /^\[.*\]$/ {
      group = $0
      sub(/^\[/, "", group)
      sub(/\]$/, "", group)
      group = trim(group)
      if (group == "") group = default_group
      next
    }
    {
      line = $0
      priority = ""
      date = ""
      if (match(line, /[0-9]{4}-[0-9]{2}-[0-9]{2}$/, arr)) {
        date = arr[0]
        line = substr(line, 1, RSTART - 1)
        line = trim(line)
      }
      if (match(line, /^\(([A-Z])\)[[:space:]]+/, arr)) {
        priority = arr[1]
        line = substr(line, RLENGTH + 1)
        line = trim(line)
      } else if (match(line, /^([A-Z])[[:space:]]+/, arr)) {
        priority = arr[1]
        line = substr(line, RLENGTH + 1)
        line = trim(line)
      }
      order++
      priority_empty = (priority == "" ? 1 : 0)
      printf "%s\t%s\t%s\t%s\t%d\t%d\n", group, priority, line, date, order, priority_empty
    }
  ' "$TODO_FILE"
}

write_records() {
  local records_file="$1"
  local sorted
  sorted="$(mktemp)"
  LC_ALL=C sort -t$'\t' -k1,1 -k6,6n -k2,2 -k5,5n "$records_file" >"$sorted"
  awk -v default_group="$DEFAULT_GROUP" '
    BEGIN { current = "" }
    {
      group = $1
      priority = $2
      desc = $3
      date = $4
      if (group == "") group = default_group
      if (group != current) {
        if (current != "") print ""
        print "[" group "]"
        current = group
      }
      line = ""
      if (priority != "") line = "(" priority ") " desc
      else line = desc
      if (date != "") line = line " " date
      print line
    }
    END {
      if (current == "") {
        print "[" default_group "]"
      }
    }
  ' "$sorted" >"$TODO_FILE"
  rm -f "$sorted"
}

normalize_todo_file() {
  local records
  records="$(mktemp)"
  parse_records >"$records"
  write_records "$records"
  rm -f "$records"
}

list_tasks() {
  local filter="${1:-}"
  local records
  records="$(mktemp)"
  parse_records >"$records"
  awk -v filter="$filter" '
    BEGIN { current=""; idx=0; has_filter=(filter != "") }
    {
      group=$1; priority=$2; desc=$3; date=$4
      entry = desc
      if (priority != "") entry="(" priority ") " entry
      if (date != "") entry=entry " " date
      matches = (!has_filter || index(tolower(group), tolower(filter)) || index(tolower(entry), tolower(filter)))
      if (!matches) next
      if (group != current) {
        if (current != "") print ""
        print "[" group "]"
        current=group
      }
      idx++
      printf "%d) %s\n", idx, entry
    }
  ' "$records"
  rm -f "$records"
}

list_done_tasks() {
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
  local group="$DEFAULT_GROUP"
  local priority=""
  local task_parts=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -g)
        group="$2"
        shift 2
        ;;
      -p)
        priority="$2"
        shift 2
        ;;
      --)
        shift
        task_parts+=("$@")
        break
        ;;
      *)
        task_parts+=("$1")
        shift
        ;;
    esac
  done

  local task="${task_parts[*]}"
  if [[ -z "$task" ]]; then
    echo "Task text required." >&2
    return 1
  fi
  if [[ -n "$priority" && ! "$priority" =~ ^[A-Z]$ ]]; then
    echo "Priority must be A-Z." >&2
    return 1
  fi
  group="$(normalize_group_name "$group")"

  local records
  records="$(mktemp)"
  parse_records >"$records"
  local next_order
  next_order="$(awk 'END { print NR + 1 }' "$records")"
  local priority_empty=1
  if [[ -n "$priority" ]]; then
    priority_empty=0
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$group" "$priority" "$task" "$TODAY" "$next_order" "$priority_empty" >>"$records"
  write_records "$records"
  rm -f "$records"
  log "INFO" "Added task: $task"
}

complete_task() {
  local number="$1"
  if ! [[ "$number" =~ ^[0-9]+$ ]]; then
    echo "Task number must be numeric." >&2
    return 1
  fi
  local records
  records="$(mktemp)"
  parse_records >"$records"
  local total
  total="$(wc -l <"$records" | tr -d ' ')"
  if [[ "$number" -lt 1 || "$number" -gt "$total" ]]; then
    echo "No task found at number $number." >&2
    rm -f "$records"
    return 1
  fi
  local entry
  entry="$(awk -v idx="$number" 'NR==idx { print }' "$records")"
  local group priority desc date
  IFS=$'\t' read -r group priority desc date _ _ <<<"$entry"
  local done_line="[$group]"
  if [[ -n "$priority" ]]; then
    done_line+=" ($priority) $desc $date"
  else
    done_line+=" $desc $date"
  fi
  printf 'x %s %s\n' "$TODAY" "$done_line" >>"$DONE_FILE"
  awk -v idx="$number" 'NR!=idx' "$records" >"${records}.new"
  write_records "${records}.new"
  rm -f "$records" "${records}.new"
  log "INFO" "Completed task #$number: $desc"
}

set_priority() {
  local number="$1"
  local priority="$2"
  if ! [[ "$priority" =~ ^[A-Z]$ ]]; then
    echo "Priority must be A-Z." >&2
    return 1
  fi
  update_task "$number" -p "$priority"
}

remove_priority() {
  local number="$1"
  update_task "$number" -p "" || return 1
  log "INFO" "Removed priority for task #$number"
}

update_task() {
  local number="$1"
  shift
  if ! [[ "$number" =~ ^[0-9]+$ ]]; then
    echo "Task number must be numeric." >&2
    return 1
  fi

  local new_group=""
  local new_priority=""
  local new_desc=""
  local group_set=false
  local priority_set=false
  local desc_set=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -g)
        new_group="$2"
        group_set=true
        shift 2
        ;;
      -p)
        new_priority="$2"
        priority_set=true
        shift 2
        ;;
      -d)
        new_desc="$2"
        desc_set=true
        shift 2
        ;;
      --)
        shift
        break
        ;;
      *)
        echo "Unknown update option: $1" >&2
        return 1
        ;;
    esac
  done

  if [[ "$priority_set" == true && -n "$new_priority" && ! "$new_priority" =~ ^[A-Z]$ ]]; then
    echo "Priority must be A-Z." >&2
    return 1
  fi
  if [[ "$group_set" == true ]]; then
    new_group="$(normalize_group_name "$new_group")"
  fi

  local records
  records="$(mktemp)"
  parse_records >"$records"
  local total
  total="$(wc -l <"$records" | tr -d ' ')"
  if [[ "$number" -lt 1 || "$number" -gt "$total" ]]; then
    echo "No task found at number $number." >&2
    rm -f "$records"
    return 1
  fi

  awk -v idx="$number" \
    -v new_group="$new_group" \
    -v new_priority="$new_priority" \
    -v new_desc="$new_desc" \
    -v group_set="$group_set" \
    -v priority_set="$priority_set" \
    -v desc_set="$desc_set" \
    'BEGIN { OFS="\t" }
    {
      if (NR == idx) {
        if (group_set == "true") $1 = new_group
        if (priority_set == "true") $2 = new_priority
        if (desc_set == "true") $3 = new_desc
        $6 = ($2 == "" ? 1 : 0)
      }
      print
    }' "$records" >"${records}.new"

  write_records "${records}.new"
  rm -f "$records" "${records}.new"
  log "INFO" "Updated task #$number"
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
      -u)
        args+=("-u")
        shift
        args+=("$@")
        break
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
        args+=("$@")
        break
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
      add_task "${args[@]:1}"
      ;;
    update|-u)
      update_task "${args[@]:1}"
      ;;
    list)
      list_tasks "${args[*]:1}"
      ;;
    listdone)
      list_done_tasks "$DONE_FILE" "${args[*]:1}"
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
