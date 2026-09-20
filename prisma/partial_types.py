from prisma.models import JobRecord, Organization

Organization.create_partial("OrganizationSummary", exclude={"logo"})

# The title search folds both sides, so it reads every title within the caller's reach — the id and the
# title alone, not the 4.5 KB record apiece that `JobRecord` would carry.
JobRecord.create_partial("JobTitleRow", include={"id", "job_title"})
