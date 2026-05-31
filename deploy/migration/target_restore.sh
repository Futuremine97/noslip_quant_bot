#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/wehackteam/no-slip.git}"
APP_DIR="${APP_DIR:-/root/no-slip}"
BACKUP_PATH="${BACKUP_PATH:-}"
ENV_FILE="${ENV_FILE:-/etc/no-slip/prediction-api.env}"
SERVICE_USER="${SERVICE_USER:-root}"
SERVICE_NAME="${SERVICE_NAME:-no-slip-prediction}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_TIMESFM="${INSTALL_TIMESFM:-false}"
TIMESFM_REPO_PATH="${TIMESFM_REPO_PATH:-/opt/timesfm}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root on the target server." >&2
  exit 1
fi

apt-get update
apt-get install -y git python3 python3-venv python3-pip curl build-essential rsync

if [ ! -d "${APP_DIR}/.git" ]; then
  mkdir -p "$(dirname "${APP_DIR}")"
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" fetch origin main
  git -C "${APP_DIR}" checkout main
  git -C "${APP_DIR}" pull --ff-only origin main
fi

if [ -n "${BACKUP_PATH}" ]; then
  if [ ! -f "${BACKUP_PATH}" ]; then
    echo "BACKUP_PATH does not exist: ${BACKUP_PATH}" >&2
    exit 1
  fi
  tar --extract --gzip --file "${BACKUP_PATH}" --directory "/"
fi

mkdir -p /etc/no-slip /tmp/no-slip-matplotlib "${APP_DIR}/services/trader/model_cache"

if [ ! -f "${ENV_FILE}" ]; then
  cp "${APP_DIR}/deploy/prediction-api.env.example" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created ${ENV_FILE}. Edit PREDICTION_API_TOKEN before exposing the service." >&2
fi

${PYTHON_BIN} -m venv "${APP_DIR}/services/trader/.venv"
"${APP_DIR}/services/trader/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"${APP_DIR}/services/trader/.venv/bin/python" -m pip install -r "${APP_DIR}/services/trader/requirements.txt"

if [ "${INSTALL_TIMESFM}" = "true" ]; then
  if [ ! -d "${TIMESFM_REPO_PATH}/.git" ]; then
    git clone https://github.com/google-research/timesfm "${TIMESFM_REPO_PATH}"
  fi
  "${APP_DIR}/services/trader/.venv/bin/python" -m pip install -e "${TIMESFM_REPO_PATH}[torch]"
fi

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=No Slip Prophet Prediction API
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/services/trader/.venv/bin/uvicorn services.trader.prediction_api:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=${APP_DIR} /tmp/no-slip-matplotlib /etc/no-slip

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
sleep 3
systemctl status "${SERVICE_NAME}" --no-pager
curl --fail --silent --show-error http://127.0.0.1:8000/health
echo
