#!/bin/zsh
set -euo pipefail

repo_root="/Users/n4agents/openclaw"
db_path="$repo_root/data/n4os.db"
backup_dir="/Users/n4agents/Library/CloudStorage/GoogleDrive-n4agents@gmail.com/My Drive/N4OS backup/sqlite"
timestamp="$(date +%Y-%m-%d_%H-%M-%S)"
backup_path="$backup_dir/n4os-$timestamp.sqlite"
success_status_path="$backup_dir/last-success.txt"
failure_status_path="$backup_dir/last-failure.txt"

on_failure() {
  local exit_code="$?"
  local failed_at
  failed_at="$(date '+%Y-%m-%d %H:%M:%S %Z')"

  mkdir -p "$backup_dir" 2>/dev/null || true
  {
    printf "FAILED at %s\n" "$failed_at"
    printf "Database: %s\n" "$db_path"
    printf "Backup target: %s\n" "$backup_path"
    printf "Exit code: %s\n" "$exit_code"
  } >"$failure_status_path" 2>/dev/null || true

  osascript -e 'display notification "N4OS SQLite backup failed. Check last-failure.txt in Google Drive." with title "N4OS Backup"' >/dev/null 2>&1 || true
  exit "$exit_code"
}

trap on_failure ERR

mkdir -p "$backup_dir"

sqlite3 "$db_path" ".backup '$backup_path'"
sqlite3 "$backup_path" "PRAGMA integrity_check;" | grep -qx "ok"

find "$backup_dir" -name "n4os-*.sqlite" -mtime +30 -delete

{
  printf "SUCCESS at %s\n" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
  printf "Database: %s\n" "$db_path"
  printf "Backup: %s\n" "$backup_path"
} >"$success_status_path"

rm -f "$failure_status_path"

printf "Backed up %s to %s\n" "$db_path" "$backup_path"
