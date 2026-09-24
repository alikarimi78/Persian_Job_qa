ALTER TABLE "jobs_info" ADD COLUMN "organization_id" INTEGER;

CREATE INDEX "ix_jobs_info_organization_id" ON "jobs_info"("organization_id");

ALTER TABLE "jobs_info" ADD CONSTRAINT "jobs_info_organization_id_fkey" FOREIGN KEY ("organization_id") REFERENCES "organizations"("id") ON DELETE NO ACTION ON UPDATE NO ACTION;
