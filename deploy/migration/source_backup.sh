#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/root/no-slip}"
ENV_FILE="${ENV_FILE:-/etc/no-slip/prediction-api.env}"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/no-slip-prediction.service}"
OUTPUT_DIR="${OUTPUT_DIR:-/root/no-slip-migration}"
SERVICE_NAME="${SERVICE_NAME:-no-slip-prediction}"
STOP_SERVICE_DURING_BACKUP="${STOP_SERVICE_DURING_BACKUP:-false}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_NAME="no-slip-prediction-${TIMESTAMP}.tar.gz"
BACKUP_PATH="${OUTPUT_DIR}/${BACKUP_NAME}"
MANIFEST_PATH="${OUTPUT_DIR}/manifest-${TIMESTAMP}.txt"
INCLUDE_LIST_PATH="${OUTPUT_DIR}/include-${TIMESTAMP}.txt"

mkdir -p "${OUTPUT_DIR}"

if [ ! -d "${APP_DIR}" ]; then
  echo "APP_DIR does not exist: ${APP_DIR}" >&2
  exit 1
fi

SERVICE_WAS_ACTIVE="false"
if [ "${STOP_SERVICE_DURING_BACKUP}" = "true" ]; then
  if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    SERVICE_WAS_ACTIVE="true"
    systemctl stop "${SERVICE_NAME}"
  fi
fi

cleanup() {
  if [ "${STOP_SERVICE_DURING_BACKUP}" = "true" ] && [ "${SERVICE_WAS_ACTIVE}" = "true" ]; then
    systemctl start "${SERVICE_NAME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

add_existing_path() {
  local path="$1"
  if [ -e "${path}" ]; then
    printf '%s\n' "${path#/}" >> "${INCLUDE_LIST_PATH}"
  fi
}

: > "${INCLUDE_LIST_PATH}"
add_existing_path "${APP_DIR}/data"
add_existing_path "${APP_DIR}/model_cache"
add_existing_path "${APP_DIR}/services/trader/model_cache"
add_existing_path "${APP_DIR}/services/trader/exports"
add_existing_path "${APP_DIR}/.env"
add_existing_path "${APP_DIR}/.env.production"
add_existing_path "${ENV_FILE}"
add_existing_path "${SERVICE_FILE}"

{
  echo "created_at_utc=${TIMESTAMP}"
  echo "hostname=$(hostname)"
  echo "app_dir=${APP_DIR}"
  echo "env_file=${ENV_FILE}"
  echo "service_file=${SERVICE_FILE}"
  echo "stop_service_during_backup=${STOP_SERVICE_DURING_BACKUP}"
  echo "service_was_active=${SERVICE_WAS_ACTIVE}"
  echo "git_head=$(git -C "${APP_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
  echo "git_branch=$(git -C "${APP_DIR}" branch --show-current 2>/dev/null || true)"
  echo "python=$("${APP_DIR}/services/trader/.venv/bin/python" --version 2>/dev/null || python3 --version 2>/dev/null || true)"
  echo "service_status=$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || true)"
  echo "service_model_cache_size=$(du -sh "${APP_DIR}/services/trader/model_cache" 2>/dev/null | awk '{print $1}' || true)"
  echo "root_model_cache_size=$(du -sh "${APP_DIR}/model_cache" 2>/dev/null | awk '{print $1}' || true)"
  echo "data_size=$(du -sh "${APP_DIR}/data" 2>/dev/null | awk '{print $1}' || true)"
  echo "champion_model_json_count=$(find "${APP_DIR}/services/trader/model_cache" "${APP_DIR}/model_cache" -type f -name 'champion_*.json' ! -name '*.metadata.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "champion_metadata_json_count=$(find "${APP_DIR}/services/trader/model_cache" "${APP_DIR}/model_cache" -type f -name 'champion_*.metadata.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "sqlite_count=$(find "${APP_DIR}/services/trader/model_cache" "${APP_DIR}/model_cache" -type f -name '*.sqlite3' 2>/dev/null | wc -l | tr -d ' ')"
  echo
  echo "[included_paths]"
  cat "${INCLUDE_LIST_PATH}"
  echo
  echo "[runtime_state_files]"
  find "${APP_DIR}/services/trader/model_cache" "${APP_DIR}/model_cache" -maxdepth 2 -type f \( -name 'champion_*.json' -o -name '*.sqlite3' -o -name '*state*.json' \) 2>/dev/null | sort || true
} > "${MANIFEST_PATH}"

add_existing_path "${MANIFEST_PATH}"

if [ ! -s "${INCLUDE_LIST_PATH}" ]; then
  echo "No backup paths found. Check APP_DIR, ENV_FILE, and SERVICE_FILE." >&2
  exit 1
fi

tar \
  --create \
  --gzip \
  --file "${BACKUP_PATH}" \
  --directory "/" \
  --files-from "${INCLUDE_LIST_PATH}" \
  --exclude="${APP_DIR#/}/.git" \
  --exclude="${APP_DIR#/}/node_modules" \
  --exclude="${APP_DIR#/}/.next" \
  --exclude="${APP_DIR#/}/services/trader/.venv" \
  --exclude="${APP_DIR#/}/.turbo"

sha256sum "${BACKUP_PATH}" > "${BACKUP_PATH}.sha256"
tar --list --gzip --file "${BACKUP_PATH}" > "${BACKUP_PATH}.contents.txt"

echo "backup_path=${BACKUP_PATH}"
echo "checksum_path=${BACKUP_PATH}.sha256"
echo "contents_path=${BACKUP_PATH}.contents.txt"
echo "manifest_path=${MANIFEST_PATH}"
