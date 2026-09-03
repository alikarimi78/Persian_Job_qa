CREATE TYPE "role" AS ENUM ('user', 'org_admin', 'super_admin');

CREATE TYPE "jobstatus" AS ENUM ('approved', 'pending', 'rejected');

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

CREATE TABLE "users" (
    "id" SERIAL NOT NULL,
    "username" VARCHAR(64) NOT NULL,
    "hashed_password" VARCHAR(128) NOT NULL,
    "role" "role" NOT NULL DEFAULT 'user',
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "organization_id" INTEGER,
    "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

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

CREATE UNIQUE INDEX "ix_organizations_name" ON "organizations"("name");

CREATE UNIQUE INDEX "ix_users_username" ON "users"("username");

CREATE INDEX "ix_users_organization_id" ON "users"("organization_id");

CREATE INDEX "ix_jobs_info_job_title" ON "jobs_info"("job_title");

CREATE INDEX "ix_jobs_info_status" ON "jobs_info"("status");

ALTER TABLE "users" ADD CONSTRAINT "users_organization_id_fkey" FOREIGN KEY ("organization_id") REFERENCES "organizations"("id") ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE "jobs_info" ADD CONSTRAINT "jobs_info_suggested_by_fkey" FOREIGN KEY ("suggested_by") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE NO ACTION;

ALTER TABLE "jobs_info" ADD CONSTRAINT "jobs_info_reviewed_by_fkey" FOREIGN KEY ("reviewed_by") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE NO ACTION;

ALTER TABLE "users" ADD CONSTRAINT "ck_users_scope" CHECK (
    (role = 'super_admin' AND organization_id IS NULL)     OR
    (role = 'org_admin'   AND organization_id IS NOT NULL) OR
    (role = 'user')
);

CREATE UNIQUE INDEX "uq_users_org_admin" ON "users" ("organization_id") WHERE role = 'org_admin';
