ALTER TABLE "jobs_info" ADD COLUMN "organization_id" INTEGER;

CREATE INDEX "ix_jobs_info_organization_id" ON "jobs_info"("organization_id");

-- NoAction, like the accounts': an organization's records are deleted explicitly, in
-- the same transaction that deletes the organization, so nothing is orphaned quietly.
ALTER TABLE "jobs_info" ADD CONSTRAINT "jobs_info_organization_id_fkey" FOREIGN KEY ("organization_id") REFERENCES "organizations"("id") ON DELETE NO ACTION ON UPDATE NO ACTION;
