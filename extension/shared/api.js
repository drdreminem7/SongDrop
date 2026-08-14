export const API_BASE = "http://127.0.0.1:8765";

export class ApiError extends Error {
  constructor(message, status = 0, cause = undefined) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.cause = cause;
  }
}

export class SongDropApi {
  constructor(token = null, fetchImplementation = fetch) {
    this.token = token;
    // Brave brand-checks native fetch and requires WorkerGlobalScope as its receiver.
    this.fetch = fetchImplementation.bind(globalThis);
  }

  connect() {
    return this.request("/v1/session", {
      method: "POST",
    });
  }

  submit(url, destination) {
    return this.request("/v1/jobs", {
      method: "POST",
      authenticated: true,
      body: JSON.stringify({
        url,
        destination,
        audio_format: "mp3",
      }),
    });
  }

  job(jobId) {
    return this.request(`/v1/jobs/${encodeURIComponent(jobId)}`, {
      authenticated: true,
    });
  }

  async request(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    if (options.authenticated) {
      if (!this.token) {
        throw new ApiError("SongDrop authorization is unavailable. Reload the extension.", 401);
      }
      headers.Authorization = `Bearer ${this.token}`;
    }

    let response;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 5000);
    try {
      response = await this.fetch(`${API_BASE}${path}`, {
        method: options.method ?? "GET",
        headers,
        body: options.body,
        signal: controller.signal,
      });
    } catch (error) {
      console.error("SongDrop local API request failed", error);
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiError(
          "SongDrop's local service did not respond.",
          0,
          error,
        );
      }
      throw new ApiError(
        "Brave could not connect to SongDrop at 127.0.0.1:8765.",
        0,
        error,
      );
    } finally {
      clearTimeout(timeout);
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      throw new ApiError(
        payload?.detail ?? `SongDrop returned HTTP ${response.status}.`,
        response.status,
      );
    }
    return payload;
  }
}
