<template>
  <div v-if="segments && segments.length > 0" class="mt-2 mb-2">
    <p class="pt-2 mb-2">
      <span class="fw-lighter">Segments</span>
      <span class="text-secondary ms-1" style="font-size: 0.85rem">({{ segments.length }})</span>
    </p>
    <div style="overflow-x: auto">
      <table class="table table-sm" style="font-size: 0.83rem; margin-bottom: 0">
        <thead>
          <tr>
            <th style="min-width: 180px">Name</th>
            <th class="text-end" style="width: 65px">Time</th>
            <th class="text-end" style="width: 40px">PR</th>
            <th class="text-end" style="width: 65px">Dist</th>
            <th class="text-end" style="width: 55px">Grade</th>
            <th class="text-end" style="width: 50px">HR</th>
            <th class="text-end" style="width: 50px">W</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="seg in segments"
            :key="seg.id"
            style="cursor: pointer"
            @mouseenter="$emit('segmentHover', seg)"
            @mouseleave="$emit('segmentLeave')"
            :class="{ 'custom-segment-hover': hoveredId === seg.id }"
          >
            <td>{{ seg.name }}</td>
            <td class="text-end">{{ fmtTime(seg.elapsed_time) }}</td>
            <td class="text-end">
              <span
                v-if="seg.pr_rank === 1"
                style="color: #f59e0b; font-weight: 700"
                title="Personal Record"
                >PR</span
              >
              <span v-else-if="seg.pr_rank">{{ seg.pr_rank }}</span>
              <span v-else>&mdash;</span>
            </td>
            <td class="text-end">{{ fmtDist(seg.segment_distance) }}</td>
            <td class="text-end">{{ fmtGrade(seg.average_grade) }}</td>
            <td class="text-end">{{ fmtNum(seg.average_heartrate) }}</td>
            <td class="text-end">{{ fmtNum(seg.average_watts) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  segments: {
    type: Array,
    default: () => [],
  },
  hoveredId: {
    type: Number,
    default: null,
  },
})

defineEmits(['segmentHover', 'segmentLeave'])

function fmtTime(seconds) {
  if (!seconds && seconds !== 0) return '\u2014'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

function fmtDist(meters) {
  if (!meters) return '\u2014'
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`
}

function fmtGrade(g) {
  return g !== null && g !== undefined ? `${g.toFixed(1)}%` : '\u2014'
}

function fmtNum(n) {
  return n ? Math.round(n).toString() : '\u2014'
}
</script>

<style scoped>
:deep(.custom-segment-hover),
:deep(.custom-segment-hover) td {
  background-color: rgba(249, 115, 22, 0.35) !important;
}
</style>
