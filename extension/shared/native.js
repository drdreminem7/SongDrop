export const NATIVE_HOST_NAME = "com.songdrop.service_launcher";

export class NativeHelperError extends Error {
  constructor(message, cause = undefined) {
    super(message);
    this.name = "NativeHelperError";
    this.cause = cause;
  }
}

export function requestServiceStart(runtime = chrome.runtime) {
  return new Promise((resolve, reject) => {
    runtime.sendNativeMessage(
      NATIVE_HOST_NAME,
      { action: "ensure_service" },
      (response) => {
        const runtimeError = runtime.lastError;
        if (runtimeError) {
          reject(
            new NativeHelperError(
              "SongDrop's browser helper is not available. Run " +
                "'songdrop install-browser-helper', then fully quit and reopen Brave. " +
                `Brave reported: ${runtimeError.message}`,
              runtimeError,
            ),
          );
          return;
        }
        if (!response?.ok) {
          reject(
            new NativeHelperError(
              response?.error ?? "SongDrop's browser helper could not start the local service.",
            ),
          );
          return;
        }
        resolve(response);
      },
    );
  });
}

export async function connectWithAutostart(
  connect,
  startService,
  { attempts = 8, retryDelayMs = 200, delay = defaultDelay } = {},
) {
  try {
    return await connect();
  } catch (error) {
    if (error?.status !== 0) {
      throw error;
    }
  }

  await startService();
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await connect();
    } catch (error) {
      lastError = error;
      if (error?.status !== 0) {
        throw error;
      }
      if (attempt + 1 < attempts) {
        await delay(retryDelayMs);
      }
    }
  }
  throw lastError ?? new NativeHelperError("SongDrop's local service did not become ready.");
}

function defaultDelay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
