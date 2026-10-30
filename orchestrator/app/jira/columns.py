# The pipeline stages a Jira issue is moved through; each maps to a Jira status.
# The values are what the pipeline hands to `JiraTracker.move`.
QUEUED = "Queued"
BLOCKED = "Blocked"
BUILDING = "Building"
TESTING = "Testing"
CHECKS = "Checks"
REVIEWING = "Reviewing"
FIXING = "Fixing"
PR_OPEN = "PR open"
DONE = "Done"
FAILED = "Failed"
