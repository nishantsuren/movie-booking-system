#!/bin/bash
# Runs automatically on first container startup (mounted into
# /docker-entrypoint-initdb.d/). Creates one logical database per service,
# per the design doc's database-per-service ownership model (§4.1).
# These are separate databases on one Postgres instance for local-dev
# convenience — Postgres does not allow cross-database foreign keys, so the
# architectural guarantee (no enforced cross-service FK) holds identically
# to running five separate instances, at a fraction of the local overhead.
set -e

for db in catalog_db theatre_db booking_db payment_db user_db asset_db; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<-EOSQL
    CREATE DATABASE ${db};
EOSQL
  echo "Created database: ${db}"
done
