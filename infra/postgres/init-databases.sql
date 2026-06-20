-- Runs automatically on first container startup (mounted into
-- /docker-entrypoint-initdb.d/). Creates one logical database per service,
-- per the design doc's database-per-service ownership model (§4.1).
-- These are separate databases on one Postgres instance for local-dev
-- convenience -- Postgres does not allow cross-database foreign keys, so the
-- architectural guarantee (no enforced cross-service FK) holds identically
-- to running five separate instances, at a fraction of the local overhead.
--
-- A .sql file (not .sh) is used deliberately: Postgres' entrypoint pipes
-- .sql files into psql rather than executing them, which avoids a Docker
-- Desktop for Mac issue where bind-mounted shell scripts fail to execve()
-- through the VirtioFS/gRPC-FUSE file-sharing layer with "Permission
-- denied", even though the file reports as executable inside the container.

CREATE DATABASE catalog_db;
CREATE DATABASE theatre_db;
CREATE DATABASE booking_db;
CREATE DATABASE payment_db;
CREATE DATABASE user_db;
CREATE DATABASE asset_db;
