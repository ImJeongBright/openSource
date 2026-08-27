#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runtime_dir=${OPENSQL_RUNTIME_DIR:-/etc/opensql-doc-search}
runtime_group=${OPENSQL_RUNTIME_GROUP:-rocky}
db_os_user=${OPENSQL_DB_OS_USER:-opensql}
db_host=${OPENSQL_ADMIN_HOST:-/home/rocky/opensql_home/tmp}
db_name=${OPENSQL_DB:-doc_search}
psql_bin=${OPENSQL_PSQL_BIN:-/home/rocky/opensql_home/pgsql/bin/psql}
roles_sql=${project_root}/sql/security/01_runtime_roles.sql

for executable in "${psql_bin}" /usr/bin/openssl /usr/sbin/runuser; do
  if [[ ! -x ${executable} ]]; then
    echo "Required executable is missing: ${executable}" >&2
    exit 1
  fi
done

temporary_dir=$(mktemp -d)
cleanup() {
  rm -f "${temporary_dir}/api.env" "${temporary_dir}/worker.env" "${temporary_dir}/mcp.env"
  rmdir "${temporary_dir}"
}
trap cleanup EXIT

api_password=$(/usr/bin/openssl rand -hex 32)
worker_password=$(/usr/bin/openssl rand -hex 32)
mcp_password=$(/usr/bin/openssl rand -hex 32)

/usr/sbin/runuser -u "${db_os_user}" -- "${psql_bin}" \
  -X -h "${db_host}" -U postgres -d "${db_name}" \
  -v ON_ERROR_STOP=1 \
  -v api_password="${api_password}" \
  -v worker_password="${worker_password}" \
  -v mcp_password="${mcp_password}" \
  -f "${roles_sql}"

printf 'OPENSQL_USER=opensql_api\nOPENSQL_PASSWORD=%s\n' "${api_password}" \
  > "${temporary_dir}/api.env"
printf 'OPENSQL_USER=opensql_worker\nOPENSQL_PASSWORD=%s\n' "${worker_password}" \
  > "${temporary_dir}/worker.env"
printf 'OPENSQL_USER=mcp_app_user\nOPENSQL_PASSWORD=%s\n' "${mcp_password}" \
  > "${temporary_dir}/mcp.env"

install -d -m 0750 -o root -g "${runtime_group}" "${runtime_dir}"
install -m 0640 -o root -g "${runtime_group}" \
  "${temporary_dir}/api.env" "${runtime_dir}/api.env"
install -m 0640 -o root -g "${runtime_group}" \
  "${temporary_dir}/worker.env" "${runtime_dir}/worker.env"
install -m 0640 -o root -g "${runtime_group}" \
  "${temporary_dir}/mcp.env" "${runtime_dir}/mcp.env"

echo "Runtime DB roles and protected environment override files are ready."

