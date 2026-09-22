ALTER TABLE "users" ADD COLUMN "updated_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE "users" ADD COLUMN "last_login" TIMESTAMP(6);
ALTER TABLE "users" ADD COLUMN "created_by" INTEGER;

-- Nothing recorded an edit before this column existed, so an existing account was last touched when made.
UPDATE "users" SET "updated_at" = "created_at";

ALTER TABLE "users" ADD CONSTRAINT "users_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE NO ACTION;
