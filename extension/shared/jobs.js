export async function trackedJobExists(jobId, loadJob) {
  try {
    await loadJob(jobId);
    return true;
  } catch (error) {
    if (error?.status === 404) {
      return false;
    }
    throw error;
  }
}
