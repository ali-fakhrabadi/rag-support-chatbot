#!/bin/sh
# Runs once, when the database volume is created. Creates the application role that the API and
# worker use: it can read and write rows but owns nothing, so Row-Level Security applies to it.
set -eu
APP_PASSWORD="$(cat /run/secrets/db_app_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v app_password="$APP_PASSWORD" <<'SQL'
CREATE ROLE rag_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'app_password';
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE rag TO rag_app;
SQL
