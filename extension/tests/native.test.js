import assert from "node:assert/strict";
import test from "node:test";

import {
  NATIVE_HOST_NAME,
  NativeHelperError,
  connectWithAutostart,
  requestServiceStart,
} from "../shared/native.js";
import { ApiError } from "../shared/api.js";

test("a reachable API bypasses the native helper", async () => {
  let starts = 0;
  const session = await connectWithAutostart(
    async () => ({ token: "already-running" }),
    async () => {
      starts += 1;
    },
  );

  assert.equal(session.token, "already-running");
  assert.equal(starts, 0);
});

test("an unavailable API starts the helper and retries", async () => {
  let connections = 0;
  let starts = 0;
  const session = await connectWithAutostart(
    async () => {
      connections += 1;
      if (connections === 1) {
        throw new ApiError("offline", 0);
      }
      return { token: "started-token" };
    },
    async () => {
      starts += 1;
    },
    { delay: async () => undefined },
  );

  assert.equal(session.token, "started-token");
  assert.equal(starts, 1);
  assert.equal(connections, 2);
});

test("HTTP API errors do not launch a native process", async () => {
  let starts = 0;
  await assert.rejects(
    () =>
      connectWithAutostart(
        async () => {
          throw new ApiError("Forbidden", 403);
        },
        async () => {
          starts += 1;
        },
      ),
    (error) => error instanceof ApiError && error.status === 403,
  );
  assert.equal(starts, 0);
});

test("native helper request uses the fixed host and one allowed action", async () => {
  let captured;
  const runtime = {
    lastError: undefined,
    sendNativeMessage(host, message, callback) {
      captured = { host, message };
      callback({ ok: true, started: true });
    },
  };

  const response = await requestServiceStart(runtime);

  assert.deepEqual(captured, {
    host: NATIVE_HOST_NAME,
    message: { action: "ensure_service" },
  });
  assert.equal(response.started, true);
});

test("missing native helper gives a one-time installation instruction", async () => {
  const runtime = {
    lastError: undefined,
    sendNativeMessage(_host, _message, callback) {
      this.lastError = { message: "host not found" };
      callback(undefined);
    },
  };

  await assert.rejects(
    () => requestServiceStart(runtime),
    (error) =>
      error instanceof NativeHelperError && error.message.includes("install-browser-helper"),
  );
});
