-- CreateTable
CREATE TABLE "saved_searches" (
    "id" SERIAL NOT NULL,
    "user_id" INTEGER NOT NULL,
    "question" VARCHAR(500) NOT NULL,
    "mode" VARCHAR(64) NOT NULL,
    "job_title" VARCHAR(255),
    "payload" JSONB NOT NULL,
    "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "saved_searches_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "ix_saved_searches_user_id" ON "saved_searches"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "uq_saved_searches_user_question" ON "saved_searches"("user_id", "question");

-- AddForeignKey
ALTER TABLE "saved_searches" ADD CONSTRAINT "saved_searches_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE NO ACTION;
