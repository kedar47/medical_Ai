#!/bin/sh
set -e

TEST_DB="${POSTGRES_DB}_test"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -tAc "SELECT 1 FROM pg_database WHERE datname = '$TEST_DB'" \
  | grep -q 1 \
  || psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    -c "CREATE DATABASE \"$TEST_DB\""
