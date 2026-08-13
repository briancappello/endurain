<template>
  <div v-if="activity && laps && laps.length > 0" class="mt-2 mb-2">
    <div style="overflow-x: auto">
      <table class="table table-sm" style="font-size: 0.83rem; margin-bottom: 0">
        <thead>
          <tr>
            <th class="text-start" style="width: 45px">Lap</th>
            <th class="text-start" style="min-width: 70px">Intensity</th>
            <th class="text-end" style="width: 75px">Distance</th>
            <th class="text-end" style="width: 75px">Time</th>
            <th class="text-end" style="width: 80px">
              {{ activityTypeIsCycling(activity) ? 'Speed' : 'Pace' }}
            </th>
            <th class="text-end" style="width: 70px">Elev</th>
            <th class="text-end" style="width: 60px">HR</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(lap, index) in laps"
            :key="lap.id"
            style="cursor: pointer"
            @mouseenter="$emit('lapHover', lap)"
            @mouseleave="$emit('lapLeave')"
            :class="{ 'custom-segment-hover': hoveredId === lap.id }"
          >
            <td class="text-start">{{ index + 1 }}</td>
            <td class="text-start">{{ lap.intensity || '\u2014' }}</td>
            <td class="text-end">{{ formatDistance(t, activity, units, lap) }}</td>
            <td class="text-end">{{ formatSecondsToMinutes(lap.total_elapsed_time) }}</td>
            <td class="text-end">
              {{
                activityTypeIsCycling(activity)
                  ? formatAverageSpeed(t, activity, units, lap)
                  : formatPace(t, activity, units, lap)
              }}
            </td>
            <td class="text-end">{{ formatElevation(t, lap.total_ascent, units) }}</td>
            <td class="text-end">{{ fmtNum(lap.avg_heart_rate) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
// Reuse upstream's lap-aware formatters rather than rolling our own. They
// already handle the metric/imperial switch and the raw FIT units:
// enhanced_avg_speed is m/s and enhanced_avg_pace is sec/METRE (fit/utils.py
// sets pace = 1 / speed), both of which are easy to get wrong by 3.6x / 1000x.
import {
  formatDistance,
  formatElevation,
  formatPace,
  formatAverageSpeed,
  activityTypeIsCycling,
} from '@/utils/activityUtils'
import { formatSecondsToMinutes } from '@/utils/dateTimeUtils'

defineProps({
  laps: { type: Array, default: () => [] },
  hoveredId: { type: Number, default: null },
  // 'metric' | 'imperial' -- upstream calls this a unitSystem.
  units: { type: String, default: 'metric' },
  activity: { type: Object, default: null },
})

defineEmits(['lapHover', 'lapLeave'])

const { t } = useI18n()

function fmtNum(n) {
  return n !== null && n !== undefined ? Math.round(Number(n)).toString() : '\u2014'
}
</script>

<style scoped>
:deep(.custom-segment-hover),
:deep(.custom-segment-hover) td {
  background-color: rgba(249, 115, 22, 0.35) !important;
}
</style>
