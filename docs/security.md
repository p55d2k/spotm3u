# Security

## Threat Model

The application accepts ZIP files supplied by users.

Uploaded files must therefore be treated as untrusted data.

---

## Upload Validation

Validate:

- request size
- uploaded file presence
- extension
- ZIP integrity
- expected archive structure where possible

Do not trust the original filename.

---

## Server-Side Filenames

Generate filenames on the server.

Example concept:

```text
job-abc123/export.zip
```

Do not use arbitrary user filenames as filesystem paths.

---

## ZIP Path Traversal

ZIP entries may contain paths such as:

```text
../../something
```

or absolute paths.

Extraction must ensure every destination remains inside the job's extraction directory.

Do not blindly call `extractall()` on an untrusted archive without validating its entries.

---

## File Execution

Uploaded files must never be executed.

The application should treat archive contents strictly as data.

---

## Arbitrary File Access

Users must not be able to request arbitrary filesystem paths through:

- download endpoints
- job IDs
- playlist IDs
- filenames
- query parameters

Generated files should be addressed through server-controlled identifiers.

---

## Job Isolation

Each upload should have its own job directory.

Example:

```text
job-A/
job-B/
```

A user accessing job A must not be able to retrieve files belonging to job B.

---

## Temporary Data

Temporary files should have an expiration policy.

Cleanup must:

- avoid deleting active jobs
- handle interrupted jobs
- safely remove expired directories

---

## Logging

Never log:

- passwords
- authentication tokens
- cookies
- secrets
- raw credentials
- unnecessary sensitive uploaded data

Logs should contain enough information to diagnose application behavior without exposing private information.

---

## Error Messages

Do not expose:

- internal stack traces
- absolute server filesystem paths
- secrets
- implementation details useful for attacking the application

Log detailed errors server-side and display safe summaries to users.

---

## Session Data

Keep Flask cookie sessions small.

Store opaque identifiers rather than large application objects.

Never place credentials or secrets in the client-side session.

---

## Download Authorization

Before returning a generated file:

1. Resolve the current job.
2. Verify it is accessible to the current session.
3. Validate the requested playlist/output identifier.
4. Construct the path from trusted server-side data.
5. Ensure the resulting path remains inside the expected output directory.

Never treat user-supplied path strings as trusted filesystem paths.
