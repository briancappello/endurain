import { fetchGetRequest } from '@/utils/serviceUtils'
import { fetchPublicGetRequest } from '@/utils/servicePublicUtils'

export const customSegments = {
  // Custom segments, trails and laps authenticated
  async getActivitySegments(activityId) {
    return fetchGetRequest(`custom/activities/${activityId}/segments`)
  },
  async getActivityTrailDescription(activityId) {
    return fetchGetRequest(`custom/activities/${activityId}/trail-description`)
  },
  async getActivityLaps(activityId) {
    return fetchGetRequest(`custom/activities/${activityId}/laps`)
  },
  // Custom segments, trails and laps public
  async getPublicActivitySegments(activityId) {
    return fetchPublicGetRequest(`public/custom/activities/${activityId}/segments`)
  },
  async getPublicActivityTrailDescription(activityId) {
    return fetchPublicGetRequest(`public/custom/activities/${activityId}/trail-description`)
  },
  async getPublicActivityLaps(activityId) {
    return fetchPublicGetRequest(`public/custom/activities/${activityId}/laps`)
  }
}
