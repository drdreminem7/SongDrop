import assert from "node:assert/strict";
import test from "node:test";

import { ApiError } from "../shared/api.js";
import { trackedJobExists } from "../shared/jobs.js";

test("a job still known by the API remains tracked", async () => {
  const exists = await trackedJobExists("job-running", async (jobId) => ({
    id: jobId,
    status: "running",
  }));

  assert.equal(exists, true);
});

test("a job forgotten after an API restart is stale", async () => {
  const exists = await trackedJobExists("job-from-old-process", async () => {
    throw new ApiError("Job not found", 404);
  });

  assert.equal(exists, false);
});

test("non-404 API failures do not silently discard tracking", async () => {
  await assert.rejects(
    () =>
      trackedJobExists("job-unauthorized", async () => {
        throw new ApiError("Forbidden", 403);
      }),
    (error) => error instanceof ApiError && error.status === 403,
  );
});
