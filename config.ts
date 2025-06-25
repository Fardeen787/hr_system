// Configuration for job data directory path
// In production, this would come from environment variables
export const CONFIG = {
  JOBS_DATA_DIR_PATH: process.env.JOBS_DATA_DIR_PATH || "/jobs-data",
  // You can set this in your .env file:
  // JOBS_DATA_DIR_PATH=/path/to/your/jobs-data-directory
}
