import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, SongDropApi } from "../shared/api.js";

test("job submissions are authenticated and default to MP3", async () => {
  let captured;
  const fakeFetch = async (url, options) => {
    captured = { url, options };
    return new Response(JSON.stringify({ job_id: "job-1", status: "queued" }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    });
  };

  await new SongDropApi("local-token", fakeFetch).submit(
    "https://www.youtube.com/watch?v=abc123",
    "apple_music",
  );

  assert.equal(captured.url, "http://127.0.0.1:8765/v1/jobs");
  assert.equal(captured.options.headers.Authorization, "Bearer local-token");
  assert.deepEqual(JSON.parse(captured.options.body), {
    url: "https://www.youtube.com/watch?v=abc123",
    destination: "apple_music",
    audio_format: "mp3",
  });
});

test("native-style fetch receives the worker global as its receiver", async () => {
  let receiver;
  async function receiverCheckedFetch() {
    receiver = this;
    return new Response(JSON.stringify({ token: "automatic-token" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  const response = await new SongDropApi(null, receiverCheckedFetch).connect();

  assert.equal(receiver, globalThis);
  assert.equal(response.token, "automatic-token");
});

test("API failures expose the local service's readable detail", async () => {
  const fakeFetch = async () =>
    new Response(JSON.stringify({ detail: "Invalid token" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });

  await assert.rejects(
    () => new SongDropApi("stale", fakeFetch).job("job-1"),
    (error) => error instanceof ApiError && error.status === 401 && error.message === "Invalid token",
  );
});
