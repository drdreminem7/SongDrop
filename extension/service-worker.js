import { ApiError, SongDropApi } from "./shared/api.js";
import { actionForUrl, classifySongDropUrl } from "./shared/urls.js";

const ACTIVE_JOBS_KEY = "activeJobs";
const TOKEN_KEY = "apiToken";
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);
const completingJobs = new Set();
const submissions = new Map();
const NOTIFICATION_ICON =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";

chrome.action.onClicked.addListener((tab) => {
  handleActionClick(tab).catch((error) => {
    console.error("SongDrop toolbar action failed", error);
    void showFailure(error instanceof Error ? error.message : "SongDrop could not start.");
  });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message).then(sendResponse).catch((error) => {
    sendResponse({
      ok: false,
      error: error instanceof Error ? error.message : "SongDrop request failed.",
      status: error instanceof ApiError ? error.status : 0,
    });
  });
  return true;
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm.name.startsWith("songdrop-job:")) {
    return;
  }
  const jobId = alarm.name.slice("songdrop-job:".length);
  pollJob(jobId).catch(() => undefined);
});

async function handleActionClick(tab) {
  const classification = actionForUrl(tab.url ?? "");
  if (classification.action === "reject") {
    await showFailure("Open a supported YouTube or YouTube Music track or playlist first.");
    return;
  }

  if (classification.action === "choose_playlist_destination") {
    await openPanel("playlist", classification.url, tab.title ?? "YouTube playlist");
    return;
  }

  const token = await ensureToken();
  await submitWithFreshAuthorization(classification.url, "apple_music", token);
  await showNotification(
    "SongDrop started",
    "Preparing this track for Apple Music. You can keep browsing.",
  );
}

async function handleMessage(message) {
  if (!message || typeof message !== "object") {
    throw new Error("Invalid extension request.");
  }
  if (message.type === "connection") {
    try {
      await ensureToken();
      return { ok: true, reachable: true };
    } catch (error) {
      return {
        ok: true,
        reachable: false,
        error: error instanceof Error ? error.message : "SongDrop is unavailable.",
      };
    }
  }
  if (message.type === "submit") {
    const classification = classifySongDropUrl(String(message.url ?? ""));
    if (classification.kind === "unsupported") {
      throw new Error("This is not a supported YouTube URL.");
    }
    if (
      classification.kind === "track" &&
      message.destination !== "apple_music"
    ) {
      throw new Error("Single tracks go directly to Apple Music.");
    }
    const token = await ensureToken();
    const receipt = await submitWithFreshAuthorization(
      classification.url,
      String(message.destination),
      token,
    );
    return { ok: true, jobId: receipt.job_id };
  }
  if (message.type === "job") {
    const token = await ensureToken();
    const job = await jobWithFreshAuthorization(String(message.jobId ?? ""), token);
    return { ok: true, job };
  }
  throw new Error("Unknown extension request.");
}

async function submitWithFreshAuthorization(url, destination, token) {
  try {
    return await submitJob(url, destination, token);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) {
      throw error;
    }
    await chrome.storage.local.remove(TOKEN_KEY);
    return submitJob(url, destination, await ensureToken());
  }
}

async function jobWithFreshAuthorization(jobId, token) {
  try {
    return await new SongDropApi(token).job(jobId);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) {
      throw error;
    }
    await chrome.storage.local.remove(TOKEN_KEY);
    return new SongDropApi(await ensureToken()).job(jobId);
  }
}

async function submitJob(url, destination, token) {
  const key = `${destination}\n${url}`;
  const inFlight = submissions.get(key);
  if (inFlight) {
    return inFlight;
  }
  const submission = submitJobOnce(url, destination, token).finally(() => {
    submissions.delete(key);
  });
  submissions.set(key, submission);
  return submission;
}

async function submitJobOnce(url, destination, token) {
  const stored = await chrome.storage.local.get(ACTIVE_JOBS_KEY);
  const activeJobs = stored[ACTIVE_JOBS_KEY] ?? {};
  const existing = Object.entries(activeJobs).find(
    ([, job]) => job.url === url && job.destination === destination,
  );
  if (existing) {
    void pollSoon(existing[0]);
    return { job_id: existing[0], status: "queued" };
  }

  const receipt = await new SongDropApi(token).submit(url, destination);
  activeJobs[receipt.job_id] = { destination, url };
  await chrome.storage.local.set({ [ACTIVE_JOBS_KEY]: activeJobs });
  await chrome.action.setBadgeBackgroundColor({ color: "#B8742A" });
  await chrome.action.setBadgeText({ text: "…" });
  await chrome.action.setTitle({ title: "SongDrop is processing audio" });
  await chrome.alarms.create(`songdrop-job:${receipt.job_id}`, {
    delayInMinutes: 0.25,
    periodInMinutes: 1,
  });
  void pollSoon(receipt.job_id);
  return receipt;
}

async function pollSoon(jobId) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const job = await pollJob(jobId);
    if (!job || TERMINAL_STATUSES.has(job.status)) {
      return;
    }
  }
}

async function pollJob(jobId) {
  const token = await storedToken();
  if (!token) {
    return null;
  }
  let job;
  try {
    job = await jobWithFreshAuthorization(jobId, token);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      await finishTracking(jobId);
    }
    return null;
  }
  if (!TERMINAL_STATUSES.has(job.status)) {
    return job;
  }

  if (completingJobs.has(jobId)) {
    return job;
  }
  completingJobs.add(jobId);

  try {
    await finishTracking(jobId);
    if (job.status === "completed") {
      const count = job.destination === "filesystem" ? job.saved : job.imported;
      const destination =
        job.destination === "filesystem" ? "the SongDrop folder" : "Apple Music";
      const message =
        !count && job.skipped
          ? job.message ?? "This audio was already processed."
          : `${count} track${count === 1 ? "" : "s"} sent to ${destination}.`;
      await showNotification("SongDrop complete", message);
    } else {
      const preserved = job.preserved_path ? ` Preserved at: ${job.preserved_path}` : "";
      await showFailure(`${job.message ?? "SongDrop could not finish the job."}${preserved}`);
    }
  } finally {
    completingJobs.delete(jobId);
  }
  return job;
}

async function finishTracking(jobId) {
  await chrome.alarms.clear(`songdrop-job:${jobId}`);
  const stored = await chrome.storage.local.get(ACTIVE_JOBS_KEY);
  const activeJobs = stored[ACTIVE_JOBS_KEY] ?? {};
  delete activeJobs[jobId];
  await chrome.storage.local.set({ [ACTIVE_JOBS_KEY]: activeJobs });
  const remaining = Object.keys(activeJobs).length;
  await chrome.action.setBadgeText({ text: remaining ? String(remaining) : "" });
  await chrome.action.setTitle({
    title: remaining ? `${remaining} SongDrop job(s) active` : "Send to SongDrop",
  });
}

async function storedToken() {
  const stored = await chrome.storage.local.get(TOKEN_KEY);
  return stored[TOKEN_KEY] ?? null;
}

async function ensureToken() {
  const existing = await storedToken();
  if (existing) {
    return existing;
  }
  const session = await new SongDropApi().connect();
  await chrome.storage.local.set({ [TOKEN_KEY]: session.token });
  return session.token;
}

async function openPanel(mode, url, title, error = "") {
  const query = new URLSearchParams({ mode, url, title });
  if (error) {
    query.set("error", error);
  }
  await chrome.windows.create({
    url: `${chrome.runtime.getURL("panel/panel.html")}?${query}`,
    type: "popup",
    width: 420,
    height: 520,
  });
}

async function showFailure(message) {
  await chrome.action.setBadgeBackgroundColor({ color: "#B42318" });
  await chrome.action.setBadgeText({ text: "!" });
  await chrome.action.setTitle({ title: message });
  return showNotification("SongDrop needs attention", message);
}

function showNotification(title, message) {
  return chrome.notifications.create({
    type: "basic",
    iconUrl: NOTIFICATION_ICON,
    title,
    message,
  });
}
