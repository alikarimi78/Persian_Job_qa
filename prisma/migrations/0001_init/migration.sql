-- CreateEnum
CREATE TYPE "role" AS ENUM ('user', 'unit_admin', 'org_admin', 'super_admin');

-- CreateEnum
CREATE TYPE "jobstatus" AS ENUM ('approved', 'pending', 'rejected');

-- CreateTable
CREATE TABLE "organizations" (
    "id" SERIAL NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "code" VARCHAR(64),
    "address" VARCHAR(512),
    "phone" VARCHAR(32),
    "email" VARCHAR(254),
    "logo" BYTEA,
    "logo_mime" VARCHAR(64),
    "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "organizations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "units" (
    "id" SERIAL NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "organization_id" INTEGER NOT NULL,
    "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "units_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "users" (
    "id" SERIAL NOT NULL,
    "username" VARCHAR(64) NOT NULL,
    "hashed_password" VARCHAR(128) NOT NULL,
    "role" "role" NOT NULL DEFAULT 'user',
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "organization_id" INTEGER,
    "unit_id" INTEGER,
    "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "jobs_info" (
    "id" SERIAL NOT NULL,
    "job_title" VARCHAR(255) NOT NULL,
    "aliases" TEXT NOT NULL DEFAULT '',
    "tools" TEXT NOT NULL DEFAULT '',
    "skills" TEXT NOT NULL DEFAULT '',
    "knowledge" TEXT NOT NULL DEFAULT '',
    "abilities" TEXT NOT NULL DEFAULT '',
    "work_context" TEXT NOT NULL DEFAULT '',
    "career_path_next" TEXT NOT NULL DEFAULT '',
    "description" TEXT NOT NULL DEFAULT '',
    "responsibilities" TEXT NOT NULL DEFAULT '',
    "status" "jobstatus" NOT NULL DEFAULT 'pending',
    "suggested_by" INTEGER,
    "reviewed_by" INTEGER,
    "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "jobs_info_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "ix_organizations_name" ON "organizations"("name");

-- CreateIndex
CREATE INDEX "ix_units_name" ON "units"("name");

-- CreateIndex
CREATE INDEX "ix_units_organization_id" ON "units"("organization_id");

-- CreateIndex
CREATE UNIQUE INDEX "uq_units_org_name" ON "units"("organization_id", "name");

-- CreateIndex
CREATE UNIQUE INDEX "ix_users_username" ON "users"("username");

-- CreateIndex
CREATE INDEX "ix_users_organization_id" ON "users"("organization_id");

-- CreateIndex
CREATE INDEX "ix_users_unit_id" ON "users"("unit_id");

-- CreateIndex
CREATE INDEX "ix_jobs_info_job_title" ON "jobs_info"("job_title");

-- CreateIndex
CREATE INDEX "ix_jobs_info_status" ON "jobs_info"("status");

-- AddForeignKey
ALTER TABLE "units" ADD CONSTRAINT "units_organization_id_fkey" FOREIGN KEY ("organization_id") REFERENCES "organizations"("id") ON DELETE NO ACTION ON UPDATE NO ACTION;

-- AddForeignKey
ALTER TABLE "users" ADD CONSTRAINT "users_organization_id_fkey" FOREIGN KEY ("organization_id") REFERENCES "organizations"("id") ON DELETE NO ACTION ON UPDATE NO ACTION;

-- AddForeignKey
ALTER TABLE "users" ADD CONSTRAINT "users_unit_id_fkey" FOREIGN KEY ("unit_id") REFERENCES "units"("id") ON DELETE NO ACTION ON UPDATE NO ACTION;

-- AddForeignKey
ALTER TABLE "jobs_info" ADD CONSTRAINT "jobs_info_suggested_by_fkey" FOREIGN KEY ("suggested_by") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE NO ACTION;

-- AddForeignKey
ALTER TABLE "jobs_info" ADD CONSTRAINT "jobs_info_reviewed_by_fkey" FOREIGN KEY ("reviewed_by") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE NO ACTION;


-- ---------------------------------------------------------------------------
-- Everything below this line is hand-written and has no counterpart in
-- schema.prisma: Prisma can express neither a CHECK constraint nor a partial
-- (filtered) unique index, and its introspection does not see either of them.
--
-- That is a standing hazard, not a one-off: `prisma migrate dev` builds the next
-- migration by diffing schema.prisma against a shadow database replayed from
-- this history, and these three objects are invisible on both sides. Read every
-- generated migration before applying it, and if one proposes dropping either
-- index, delete that statement. They came over verbatim from Alembic's 0003.
-- ---------------------------------------------------------------------------

-- Which scope column a role carries. Note the last branch: role='user' may have a
-- NULL unit_id, because rows that predate the hierarchy have no unit to point at.
-- New users always get one — `accounts.create_account` requires it — so the slack
-- is for legacy rows only.
ALTER TABLE "users" ADD CONSTRAINT "ck_users_scope" CHECK (
    (role = 'super_admin' AND organization_id IS NULL     AND unit_id IS NULL)     OR
    (role = 'org_admin'   AND organization_id IS NOT NULL AND unit_id IS NULL)     OR
    (role = 'unit_admin'  AND organization_id IS NULL     AND unit_id IS NOT NULL) OR
    (role = 'user'        AND organization_id IS NULL)
);

-- "One admin per organization" and "one admin per unit". Partial, so that ordinary
-- users still share a unit_id freely and only the admin row is capped.
CREATE UNIQUE INDEX "uq_users_org_admin"  ON "users" ("organization_id") WHERE role = 'org_admin';
CREATE UNIQUE INDEX "uq_users_unit_admin" ON "users" ("unit_id")         WHERE role = 'unit_admin';
