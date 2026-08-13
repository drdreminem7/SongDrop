const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

/** Classify only URL shapes accepted by SongDrop's current provider. */
export function classifySongDropUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return { kind: "unsupported", url: rawUrl };
  }

  const hostname = parsed.hostname.toLowerCase().replace(/\.$/, "");
  if (parsed.protocol !== "https:" || !YOUTUBE_HOSTS.has(hostname)) {
    return { kind: "unsupported", url: rawUrl };
  }

  if (
    hostname !== "youtu.be" &&
    parsed.pathname === "/playlist" &&
    parsed.searchParams.get("list")
  ) {
    return { kind: "playlist", url: parsed.href };
  }

  if (hostname === "youtu.be" && firstPathPart(parsed.pathname)) {
    return { kind: "track", url: parsed.href };
  }

  if (parsed.pathname === "/watch" && parsed.searchParams.get("v")) {
    return { kind: "track", url: parsed.href };
  }

  const [prefix, identifier] = pathParts(parsed.pathname);
  if (["shorts", "live", "embed"].includes(prefix) && identifier) {
    return { kind: "track", url: parsed.href };
  }

  return { kind: "unsupported", url: rawUrl };
}

/** Translate a supported page into the product action exposed by the toolbar button. */
export function actionForUrl(rawUrl) {
  const classification = classifySongDropUrl(rawUrl);
  if (classification.kind === "track") {
    return { ...classification, action: "import_apple_music" };
  }
  if (classification.kind === "playlist") {
    return { ...classification, action: "choose_playlist_destination" };
  }
  return { ...classification, action: "reject" };
}

function firstPathPart(pathname) {
  return pathParts(pathname)[0] ?? null;
}

function pathParts(pathname) {
  return pathname.split("/").filter(Boolean);
}
