#!/usr/bin/env bash
set -euo pipefail

: "${CONF_USER:?Set CONF_USER in env}"
: "${CONF_PASS:?Set CONF_PASS in env}"
: "${CONFLUENCE_BASE:?Set CONFLUENCE_BASE in env}"
: "${PAGE_ID:?Set PAGE_ID in env}"
: "${ATTACHMENT_FILENAME:?Set ATTACHMENT_FILENAME in env}"

RENDER_OPT="${RENDER_OPT:-FORMATTED_VALUE}"
GOOGLE_SA_JSON="${GOOGLE_SA_JSON:-/secrets/service-account.json}"
SOURCE_KIND="${SOURCE_KIND:-sheet_values}"

ARGS=( --confluence-base "$CONFLUENCE_BASE"
       --page-id "$PAGE_ID"
       --filename "$ATTACHMENT_FILENAME"
       --source-kind "$SOURCE_KIND" )

# Sheet mode (default pilot)
if [[ "$SOURCE_KIND" == "sheet_values" ]]; then
  : "${SPREADSHEET_ID:?Set SPREADSHEET_ID in env}"
  ARGS+=( --spreadsheet "$SPREADSHEET_ID" --render "$RENDER_OPT" )
  if [[ -n "${SHEET_GID:-}" ]]; then
    ARGS+=( --gid "$SHEET_GID" )
  elif [[ -n "${SHEET_TAB_NAME:-}" ]]; then
    ARGS+=( --tab-name "$SHEET_TAB_NAME" )
  else
    echo "ERROR: set either SHEET_GID or SHEET_TAB_NAME for sheet_values" >&2
    exit 2
  fi
fi

# Drive modes (optional, future)
[[ -n "${DRIVE_FILE_ID:-}"   ]] && ARGS+=( --drive-file-id "$DRIVE_FILE_ID" )
[[ -n "${DRIVE_FOLDER_ID:-}" ]] && ARGS+=( --drive-folder-id "$DRIVE_FOLDER_ID" )
[[ -n "${DRIVE_QUERY:-}"     ]] && ARGS+=( --drive-query "$DRIVE_QUERY" )
[[ -n "${EXPORT_MIME:-}"     ]] && ARGS+=( --export-mime "$EXPORT_MIME" )

# Write-back flags (option A: pass explicit full ranges)
[[ -n "${WRITE_BACK_RANGE:-}"    ]] && ARGS+=( --write-back-range "$WRITE_BACK_RANGE" )
[[ -n "${WRITE_BACK_TEMPLATE:-}" ]] && ARGS+=( --write-back-template "$WRITE_BACK_TEMPLATE" )
[[ -n "${APPEND_LOG:-}"          ]] && ARGS+=( --append-log "$APPEND_LOG" )

# Write-back flags (option B: compose ranges from simple pieces if explicit not provided)
# Safely quote the sheet name for A1 notation, doubling single quotes if any.
if [[ -z "${WRITE_BACK_RANGE:-}" && -n "${WRITE_BACK_TAB:-}" && -n "${WRITE_BACK_CELL:-}" ]]; then
  tab_escaped="${WRITE_BACK_TAB//\'/\'\'}"
  ARGS+=( --write-back-range "'${tab_escaped}'!${WRITE_BACK_CELL}" )
fi
if [[ -z "${APPEND_LOG:-}" && -n "${WRITE_BACK_TAB:-}" && -n "${APPEND_LOG_RANGE:-}" ]]; then
  tab_escaped="${WRITE_BACK_TAB//\'/\'\'}"
  ARGS+=( --append-log "'${tab_escaped}'!${APPEND_LOG_RANGE}" )
fi

exec python /app/sheet_to_confluence.py "${ARGS[@]}"
