/**
 * Service for fetching custom segment and trail data from our custom API endpoints.
 */

const API_BASE = `${window.env.ENDURAIN_HOST}/api/v1/custom`

async function fetchJson(url) {
  const resp = await fetch(url)
  if (!resp.ok) return null
  return resp.json()
}

export const customSegments = {
  async getActivitySegments(activityId) {
    return fetchJson(`${API_BASE}/activities/${activityId}/segments`)
  },

  async getActivityTrailDescription(activityId) {
    return fetchJson(`${API_BASE}/activities/${activityId}/trail-description`)
  },

  async getActivityLaps(activityId) {
    return fetchJson(`${API_BASE}/activities/${activityId}/laps`)
  },
}
