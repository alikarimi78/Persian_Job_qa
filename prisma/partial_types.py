from prisma.models import JobRecord, Organization

Organization.create_partial("OrganizationSummary", exclude={"logo"})

JobRecord.create_partial("JobTitleRow", include={"id", "job_title"})
