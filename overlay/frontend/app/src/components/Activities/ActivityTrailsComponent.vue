<template>
  <div v-if="activity && trails && trails.length > 0" class="mt-2 mb-2">
    <div style="overflow-x: auto">
      <table class="table table-sm" style="font-size: 0.83rem; margin-bottom: 0">
        <thead>
          <tr>
            <th style="min-width: 180px">Trail</th>
            <th style="width: 80px">Type</th>
            <th class="text-end" style="width: 80px">Distance</th>
            <th class="text-end" style="width: 55px">%</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="trail in trails"
            :key="trail.name"
            style="cursor: pointer"
            @mouseenter="$emit('trailHover', trail)"
            @mouseleave="$emit('trailLeave')"
            :class="{ 'custom-segment-hover': hoveredId === trail.name }"
          >
            <td>{{ trail.name }}</td>
            <td class="text-secondary">{{ trail.highway || '\u2014' }}</td>
            <td class="text-end">{{ fmtTrailDistance(trail) }}</td>
            <td class="text-end">{{ fmtPercent(trail) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
// Reuse upstream's formatter so the user's metric/imperial setting is honoured.
// formatDistance takes a lap-shaped object, so pass an adapter. Do NOT use
// formatDistanceRaw: it forces maximumFractionDigits: 0, which would render a
// 4120 m trail as "4 km" and a sub-500 m trail as "0 km".
import { formatDistance } from '@/utils/activityUtils'

const props = defineProps({
  trails: { type: Array, default: () => [] },
  // Trails have no database id, so the hovered key is the trail NAME.
  hoveredId: { type: String, default: null },
  units: { type: String, default: 'metric' },
  activity: { type: Object, default: null },
})

defineEmits(['trailHover', 'trailLeave'])

const { t } = useI18n()

function fmtTrailDistance(trail) {
  if (trail.distance_m === null || trail.distance_m === undefined) return '\u2014'
  return formatDistance(t, props.activity, props.units, {
    total_distance: trail.distance_m,
  })
}

function fmtPercent(trail) {
  const total = props.activity?.distance
  if (!total || trail.distance_m === null || trail.distance_m === undefined) {
    return '\u2014'
  }
  // Percentages intentionally do not sum to 100%: trails overlap where they run
  // concurrently or where OSM has parallel ways, and stretches on no named
  // trail count toward neither.
  return `${Math.round((trail.distance_m / total) * 100)}%`
}
</script>

<style scoped>
:deep(.custom-segment-hover),
:deep(.custom-segment-hover) td {
  background-color: rgba(249, 115, 22, 0.35) !important;
}
</style>
