<template>
  <div v-if="laps && laps.length > 0" class="mt-2 mb-2">
    <div style="overflow-x: auto">
      <table class="table table-sm" style="font-size: 0.83rem; margin-bottom: 0">
        <thead>
          <tr>
            <th class="text-start" style="width: 45px">Lap</th>
            <th class="text-start" style="min-width: 70px">Intensity</th>
            <th class="text-end" style="width: 75px">Distance</th>
            <th class="text-end" style="width: 75px">Time</th>
            <th class="text-end" style="width: 80px">Speed</th>
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
            <td class="text-end">{{ fmtDist(lap.total_distance) }}</td>
            <td class="text-end">{{ fmtTime(lap.total_elapsed_time) }}</td>
            <td class="text-end">{{ fmtSpeed(lap) }}</td>
            <td class="text-end">{{ fmtElev(lap.total_ascent) }}</td>
            <td class="text-end">{{ fmtNum(lap.avg_heart_rate) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
defineProps({
  laps: { type: Array, default: () => [] },
  hoveredId: { type: Number, default: null },
  units: { type: String, default: 'metric' },
  activity: { type: Object, default: null },
})

defineEmits(['lapHover', 'lapLeave'])

function fmtTime(seconds) {
  if (!seconds && seconds !== 0) return '\u2014'
  const total = Math.round(Number(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  return `${m}:${s.toString().padStart(2, '0')}`
}

function fmtDist(meters) {
  if ((!meters && meters !== 0) || meters === null) return '\u2014'
  const m = Number(meters)
  if (m === 0) return '0 m'
  if (m >= 1000) return `${(m / 1000).toFixed(2)} km`
  return `${Math.round(m)} m`
}

function fmtSpeed(lap) {
  // Prefer enhanced_avg_speed when present (cycling/speed activities)
  const speed = lap.enhanced_avg_speed
  if (speed !== null && speed !== undefined && speed !== 0) {
    const val = Number(speed)
    const display = val > 0 ? val : 0
    return `${display.toFixed(1)} km/h`
  }
  const pace = lap.enhanced_avg_pace
  if (pace !== null && pace !== undefined && pace !== 0) {
    const secPerKm = Number(pace)
    if (secPerKm <= 0) return '\u2014'
    const min = Math.floor(secPerKm / 60)
    const sec = Math.round(secPerKm % 60)
    return `${min}:${sec.toString().padStart(2, '0')}/km`
  }
  return '\u2014'
}

function fmtElev(meters) {
  if ((!meters && meters !== 0) || meters === null) return '\u2014'
  return `${Math.round(Number(meters))} m`
}

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
