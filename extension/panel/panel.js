const params = new URLSearchParams(location.search);
const mode = params.get("mode") ?? "playlist";
const mediaUrl = params.get("url") ?? "";
const mediaTitle = params.get("title") ?? "YouTube playlist";
const playlistSection = document.querySelector("#playlist");
const statusSection = document.querySelector("#status");
const errorElement = document.querySelector("#error");
const statusTitle = document.querySelector("#status-title");
const statusDetail = document.querySelector("#status-detail");
let submitting = false;

document.querySelector("#playlist-title").textContent = mediaTitle;
for (const button of document.querySelectorAll("[data-destination]")) {
  button.addEventListener("click", () => submit(button.dataset.destination));
}

void initialize();

async function initialize() {
  if (params.get("error")) {
    showError(params.get("error"));
  }
  const connection = await send({ type: "connection" });
  if (!connection.ok || connection.reachable === false) {
    showOnly(statusSection);
    statusTitle.textContent = "SongDrop could not connect";
    statusDetail.textContent =
      connection.error ??
      "Run songdrop install-browser-helper, then reload the extension on brave://extensions.";
    return;
  }
  showOnly(playlistSection);
}

async function submit(destination) {
  if (submitting) {
    return;
  }
  submitting = true;
  setControlsDisabled(true);
  clearError();
  showOnly(statusSection);
  statusTitle.textContent = "Sent to SongDrop";
  statusDetail.textContent =
    destination === "filesystem"
      ? "The playlist will be prepared in your SongDrop downloads folder."
      : "The audio will be prepared and imported into Apple Music.";

  const response = await send({
    type: "submit",
    url: mediaUrl,
    destination,
  });
  if (!response.ok) {
    showError(response.error);
    statusTitle.textContent = "SongDrop could not start";
    submitting = false;
    setControlsDisabled(false);
    return;
  }
  await poll(response.jobId);
}

async function poll(jobId) {
  for (;;) {
    await delay(1500);
    const response = await send({ type: "job", jobId });
    if (!response.ok) {
      showError(response.error);
      return;
    }
    const job = response.job;
    const progress = job.progress;
    statusTitle.textContent = statusLabel(job);
    statusDetail.textContent = statusDetailLabel(job);
    if (["completed", "failed", "cancelled"].includes(job.status)) {
      if (job.status !== "completed") {
        const preserved = job.preserved_path
          ? ` The recoverable file was preserved at ${job.preserved_path}.`
          : "";
        showError(`${job.message ?? "The job did not complete."}${preserved}`);
      }
      return;
    }
  }
}

function statusLabel(job) {
  if (job.status === "completed") {
    return job.destination === "filesystem"
      ? "Playlist saved"
      : "Imported into Apple Music";
  }
  if (job.status === "failed") {
    return "SongDrop needs attention";
  }
  if (job.status === "cancelled") {
    return "Job cancelled";
  }
  return job.progress.phase === "inspecting" ? "Inspecting source" : "Preparing audio";
}

function statusDetailLabel(job) {
  if (job.status === "completed") {
    if (job.skipped && !job.imported && !job.saved) {
      return job.message ?? "This audio was already processed.";
    }
    if (job.result_path) {
      return `Saved to ${job.result_path}`;
    }
    const count = job.destination === "filesystem" ? job.saved : job.imported;
    return `${count} track${count === 1 ? "" : "s"} completed.`;
  }
  const progress = job.progress;
  return progress.total
    ? `${progress.current ?? 0} of ${progress.total}${progress.label ? ` — ${progress.label}` : ""}`
    : progress.label ?? "SongDrop is working in the background.";
}

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function showOnly(section) {
  playlistSection.hidden = section !== playlistSection;
  statusSection.hidden = section !== statusSection;
}

function showError(message) {
  errorElement.textContent = message;
  errorElement.hidden = false;
}

function clearError() {
  errorElement.hidden = true;
  errorElement.textContent = "";
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function setControlsDisabled(disabled) {
  for (const control of document.querySelectorAll("button, input")) {
    control.disabled = disabled;
  }
}
