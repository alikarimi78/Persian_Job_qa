-- Accounts gain a person's name beside the credential.
--
-- Kept as its own migration rather than folded into `0001_init`, and the split is the
-- whole baselining story: `0001_init` is exactly the schema Alembic's 0001–0006 left
-- behind, so an existing database is `migrate resolve --applied 0001_init` and then an
-- ordinary `migrate deploy`, whether or not Alembic's 0007 ever reached it. A database
-- that already has these columns is resolved as applied for this one too.
--
-- **Both are nullable**, the same bargain the organization profile made: every account
-- that exists when this runs has no name, and a NOT NULL would have meant inventing one.
-- Being required is the *schema's* job — `AccountIn` asks for both, so no new account can
-- be nameless — and `POST /accounts/{id}/name` (an admin) and `POST /auth/name` (the
-- account itself, which is how the seeded first super_admin gets one) are how the rows
-- that predate this are filled in. Until they are, `models.display_name` falls back to
-- the username, so nothing prints blank.
--
-- Not unique and not indexed: two people may share a name, and nothing looks an account
-- up by it — `username` is still the identifier.
ALTER TABLE "users" ADD COLUMN "first_name" VARCHAR(64);
ALTER TABLE "users" ADD COLUMN "last_name"  VARCHAR(64);
