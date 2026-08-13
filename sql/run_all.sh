#!/bin/bash
# ============================================================
# 전체 SQL 스크립트를 순서대로 실행
# ============================================================

set -e

HOST=${OPENSQL_HOST:-localhost}
PORT=${OPENSQL_PORT:-5432}
USER=${OPENSQL_USER:-app_user}
DB=${OPENSQL_DB:-doc_search}

echo "DB 접속 정보: HOST=$HOST PORT=$PORT USER=$USER DB=$DB"

psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/01_extensions.sql
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/02_types.sql
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/03_core_tables.sql
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/06_app_tables.sql
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/04_indexes.sql
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/05_functions.sql
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/07_views.sql
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f sql/08_seed.sql

echo "모든 스크립트 실행 완료!"
