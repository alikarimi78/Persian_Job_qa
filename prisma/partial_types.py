from prisma.models import Organization

Organization.create_partial("OrganizationSummary", exclude={"logo"})
