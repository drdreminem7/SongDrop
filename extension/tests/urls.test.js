import assert from "node:assert/strict";
import test from "node:test";

import { actionForUrl, classifySongDropUrl } from "../shared/urls.js";

test("explicit playlist pages open the playlist workflow", () => {
  for (const url of [
    "https://www.youtube.com/playlist?list=PL123",
    "https://music.youtube.com/playlist?list=PL123",
  ]) {
    assert.equal(classifySongDropUrl(url).kind, "playlist");
  }
});

test("watch pages with a playlist parameter remain single tracks", () => {
  const result = classifySongDropUrl(
    "https://music.youtube.com/watch?v=abc123&list=PL123",
  );
  assert.equal(result.kind, "track");
  assert.equal(
    actionForUrl("https://music.youtube.com/watch?v=abc123&list=PL123").action,
    "import_apple_music",
  );
});

test("only explicit playlists expose a destination choice", () => {
  assert.equal(
    actionForUrl("https://www.youtube.com/playlist?list=PL123").action,
    "choose_playlist_destination",
  );
  assert.equal(
    actionForUrl("https://www.youtube.com/watch?v=abc123").action,
    "import_apple_music",
  );
});

test("supported single-track URL shapes go straight to the track workflow", () => {
  for (const url of [
    "https://www.youtube.com/watch?v=abc123",
    "https://youtu.be/abc123",
    "https://www.youtube.com/shorts/abc123",
    "https://www.youtube.com/live/abc123",
  ]) {
    assert.equal(classifySongDropUrl(url).kind, "track");
  }
});

test("lookalike and unrelated URLs are rejected", () => {
  for (const url of [
    "https://youtube.com.example/watch?v=abc123",
    "http://www.youtube.com/watch?v=abc123",
    "https://example.com/watch?v=abc123",
    "not a url",
  ]) {
    assert.equal(classifySongDropUrl(url).kind, "unsupported");
  }
});
