#!/bin/sh
# Existing database only. Never use create/drop-database, overwrite, or quiet.
set -eu
set -o pipefail
: "${SQL_PASSWORD:?SQL_PASSWORD is required}"
: "${SQL_DATABASE:?SQL_DATABASE is required}"
[ "$SQL_DATABASE" = agent ]
: "${P3_SCHEMA_MODE:?Explicit initialize or upgrade mode is required}"
case "$P3_SCHEMA_MODE" in initialize|upgrade) ;; *) exit 2 ;; esac

run_sql() {
    temporal-sql-tool "$@" 2>&1 |
        awk 'BEGIN {secret = ENVIRON["SQL_PASSWORD"]}
        {
            rest = $0; safe = ""
            while ((position = index(rest, secret)) > 0) {
                safe = safe substr(rest, 1, position - 1) "[REDACTED]"
                rest = substr(rest, position + length(secret))
            }
            print safe rest; fflush()
        }'
}

export SQL_CONNECT_ATTRIBUTES=search_path=p3_temporal
if [ "$P3_SCHEMA_MODE" = initialize ]; then run_sql setup-schema -v 0.0; fi
run_sql update-schema --schema-name postgresql/v12/temporal
export SQL_CONNECT_ATTRIBUTES=search_path=p3_temporal_visibility
if [ "$P3_SCHEMA_MODE" = initialize ]; then run_sql setup-schema -v 0.0; fi
run_sql update-schema --schema-name postgresql/v12/visibility
