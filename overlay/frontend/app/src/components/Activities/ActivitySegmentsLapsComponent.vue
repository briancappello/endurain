<template>
  <div v-if="(segments && segments.length > 0) || (laps && laps.length > 0)" class="mt-2 mb-2">
    <ul class="nav nav-pills mb-2 justify-content-center" role="tablist">
      <li v-if="segments && segments.length > 0" class="nav-item" role="presentation">
        <button
          class="nav-link link-body-emphasis py-1 px-3"
          :class="{ active: activeTab === 'segments' }"
          @click="activeTab = 'segments'"
          type="button"
          role="tab"
        >
          Segments
          <span class="text-secondary ms-1" style="font-size: 0.85rem">({{ segments.length }})</span>
        </button>
      </li>
      <li v-if="laps && laps.length > 0" class="nav-item" role="presentation">
        <button
          class="nav-link link-body-emphasis py-1 px-3"
          :class="{ active: activeTab === 'laps' }"
          @click="activeTab = 'laps'"
          type="button"
          role="tab"
        >
          Laps
          <span class="text-secondary ms-1" style="font-size: 0.85rem">({{ laps.length }})</span>
        </button>
      </li>
    </ul>

    <div v-if="activeTab === 'segments' && segments && segments.length > 0">
      <ActivitySegmentsComponent
        :segments="segments"
        :hoveredId="hoveredSegmentId"
        @segmentHover="$emit('segmentHover', $event)"
        @segmentLeave="$emit('segmentLeave')"
      />
    </div>

    <div v-if="activeTab === 'laps' && laps && laps.length > 0">
      <ActivityLapsTableComponent
        :laps="laps"
        :hoveredId="hoveredLapId"
        :units="units"
        :activity="activity"
        @lapHover="$emit('lapHover', $event)"
        @lapLeave="$emit('lapLeave')"
      />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ActivitySegmentsComponent from './ActivitySegmentsComponent.vue'
import ActivityLapsTableComponent from './ActivityLapsTableComponent.vue'

const props = defineProps({
  segments: { type: Array, default: () => [] },
  laps: { type: Array, default: () => [] },
  hoveredSegmentId: { type: Number, default: null },
  hoveredLapId: { type: Number, default: null },
  units: { type: String, default: 'metric' },
  activity: { type: Object, default: null },
})

defineEmits(['segmentHover', 'segmentLeave', 'lapHover', 'lapLeave'])

const activeTab = ref(
  props.segments && props.segments.length > 0 ? 'segments' : 'laps'
)
</script>
