<template>
  <div ref="chartContainer" class="position-relative" style="width: 100%">
    <canvas ref="chartCanvas" :style="{ height: height + 'px', width: '100%' }"></canvas>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { Chart, registerables } from 'chart.js'

Chart.register(...registerables)

// Shared state: which side tooltips should appear on. All charts read this
// so they flip at the same time when any chart crosses the 80% threshold.
let _tooltipSide = 'right'

const props = defineProps({
  streamData: { type: Array, default: () => [] },
  fieldName: { type: String, required: true }, // 'ele', 'vel', or 'hr'
  label: { type: String, default: '' },
  color: { type: String, default: 'rgba(34, 197, 94, 0.8)' },
  gradientColor: { type: String, default: '34, 197, 94' }, // rgb without alpha
  totalDistance: { type: Number, default: 0 }, // meters
  units: { type: String, default: 'metric' },
  height: { type: Number, default: 120 },
  segments: { type: Array, default: () => [] },
  hoveredIndex: { type: Number, default: null }, // synced from sibling chart
  showXAxis: { type: Boolean, default: true }, // only bottom chart shows x-axis labels
})

const emit = defineEmits(['positionHover', 'positionLeave'])

const chartCanvas = ref(null)
const chartContainer = ref(null)
let chartInstance = null
let isSelfHovering = false // flag to prevent self-triggering from hoveredIndex watch

function getData() {
  if (!props.streamData || props.streamData.length === 0) return []
  return props.streamData.map((pt) => {
    let val = pt[props.fieldName]
    if (val === undefined || val === null) return null
    val = Number.parseFloat(val)
    if (props.fieldName === 'vel') {
      // Convert m/s to km/h or mph
      val = props.units === 'imperial' ? val * 2.23694 : val * 3.6
    } else if (props.fieldName === 'ele' && props.units === 'imperial') {
      val = val * 3.28084 // meters to feet
    }
    return val
  })
}

function getLabels() {
  const n = props.streamData.length
  if (n === 0) return []
  const totalKm = props.totalDistance / (props.units === 'imperial' ? 1609.34 : 1000)
  const interval = totalKm / n
  return props.streamData.map((_, i) => (i * interval).toFixed(1))
}

function getSegmentAnnotations() {
  // Create colored background bands for segments
  if (!props.segments || props.segments.length === 0) return []
  const n = props.streamData.length
  return props.segments
    .filter((s) => s.start_index != null && s.end_index != null)
    .map((s, i) => ({
      startIdx: Math.min(s.start_index, n - 1),
      endIdx: Math.min(s.end_index, n - 1),
      name: s.name,
    }))
}

function createChart() {
  if (!chartCanvas.value) return
  if (chartInstance) {
    chartInstance.destroy()
    chartInstance = null
  }

  const data = getData()
  const labels = getLabels()
  if (data.length === 0) return

  const ctx = chartCanvas.value.getContext('2d')

  // Gradient fill
  const gradient = ctx.createLinearGradient(0, 0, 0, props.height)
  gradient.addColorStop(0, `rgba(${props.gradientColor}, 0.4)`)
  gradient.addColorStop(1, `rgba(${props.gradientColor}, 0.02)`)

  const unitLabels = {
    ele: props.units === 'imperial' ? 'ft' : 'm',
    vel: props.units === 'imperial' ? 'mph' : 'km/h',
    hr: 'bpm',
  }
  const unitLabel = unitLabels[props.fieldName] || ''

  const distUnit = props.units === 'imperial' ? 'mi' : 'km'

  // Crosshair + position sync plugin
  const positionPlugin = {
    id: 'positionSync',
    afterEvent(chart, args) {
      const event = args.event
      if (event.type === 'mousemove' && args.inChartArea) {
        const x = event.x
        const xScale = chart.scales.x
        const idx = Math.round(xScale.getValueForPixel(x))
        if (idx >= 0 && idx < data.length) {
          isSelfHovering = true
          emit('positionHover', idx)

          // Update shared tooltip side based on cursor position.
          const area = chart.chartArea
          const pct = (x - area.left) / (area.right - area.left)
          _tooltipSide = pct < 0.8 ? 'right' : 'left'
        }
      } else if (event.type === 'mouseout') {
        isSelfHovering = false
        emit('positionLeave')
      }

      // nothing here - tooltip alignment is handled in beforeDraw
    },
    beforeDraw(chart) {
      // Force tooltip alignment before draw.
      // xAlign 'left' = tooltip body to the RIGHT of the anchor point
      // xAlign 'right' = tooltip body to the LEFT of the anchor point
      const tt = chart.tooltip
      if (tt && tt.opacity > 0 && tt.dataPoints && tt.dataPoints.length > 0) {
        const desired = _tooltipSide === 'right' ? 'left' : 'right'
        if (tt.xAlign !== desired) {
          tt.xAlign = desired
          tt.yAlign = 'center'
          // Recalculate x position based on new alignment
          const caretX = tt.caretX
          const width = tt.width
          if (desired === 'left') {
            // tooltip to the right: x = caretX + caret padding
            tt.x = caretX + 8
          } else {
            // tooltip to the left: x = caretX - width - caret padding
            tt.x = caretX - width - 8
          }
          tt.y = (chart.chartArea.top + chart.chartArea.bottom) / 2 - tt.height / 2
        }
      }
    },
    afterDraw(chart) {
      // Draw crosshair line at hover position
      if (chart.tooltip && chart.tooltip.dataPoints && chart.tooltip.dataPoints.length > 0) {
        const x = chart.tooltip.dataPoints[0].element.x
        const ctx = chart.ctx
        const yAxis = chart.scales.y
        ctx.save()
        ctx.beginPath()
        ctx.setLineDash([4, 4])
        ctx.strokeStyle = 'rgba(180, 180, 180, 0.6)'
        ctx.lineWidth = 1
        ctx.moveTo(x, yAxis.top)
        ctx.lineTo(x, yAxis.bottom)
        ctx.stroke()
        ctx.restore()
      }
    },
  }

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          data,
          borderColor: props.color,
          backgroundColor: gradient,
          fill: true,
          borderWidth: 1.5,
          tension: 0.3,
          pointRadius: 0,
          pointHitRadius: 10,
          pointHoverRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { left: 0, right: 0, top: 0, bottom: 0 } },
      scales: {
        x: {
          display: true,
          ticks: {
            display: props.showXAxis,
            maxTicksLimit: 8,
            autoSkip: true,
            font: { size: 10 },
            callback: (val, idx) => {
              const label = labels[idx]
              return label ? `${label}${distUnit}` : ''
            },
          },
          grid: { display: false },
        },
        y: {
          display: true,
          position: 'right',
          beginAtZero: false,
          ticks: {
            font: { size: 10 },
            maxTicksLimit: 4,
            callback: (val) => `${Math.round(val)}`,
          },
          grid: { color: 'rgba(128,128,128,0.1)' },
          title: {
            display: true,
            text: unitLabel,
            font: { size: 10 },
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: true,
          callbacks: {
            label: (ctx) => `${props.label}: ${ctx.parsed.y?.toFixed(1)} ${unitLabel}`,
            title: (items) => items[0] ? `${items[0].label} ${distUnit}` : '',
          },
        },
      },
    },
    plugins: [positionPlugin],
  })
}

onMounted(() => {
  nextTick(() => createChart())
})

onUnmounted(() => {
  if (chartInstance) {
    chartInstance.destroy()
    chartInstance = null
  }
})

watch(() => [props.streamData, props.units], () => {
  nextTick(() => createChart())
}, { deep: true })

// Sync tooltip from sibling chart via hoveredIndex prop.
// Skip if this chart originated the hover (isSelfHovering).
watch(() => props.hoveredIndex, (idx) => {
  if (!chartInstance || isSelfHovering) return
  if (idx === null || idx === undefined) {
    chartInstance.setActiveElements([])
    chartInstance.tooltip.setActiveElements([], { x: 0, y: 0 })
    chartInstance.update('none')
    return
  }
  const meta = chartInstance.getDatasetMeta(0)
  if (!meta || !meta.data || idx >= meta.data.length) return
  const point = meta.data[idx]
  chartInstance.setActiveElements([{ datasetIndex: 0, index: idx }])
  chartInstance.tooltip.setActiveElements(
    [{ datasetIndex: 0, index: idx }],
    { x: point.x, y: point.y }
  )
  chartInstance.update('none')
})
</script>
