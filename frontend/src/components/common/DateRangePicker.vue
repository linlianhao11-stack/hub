<template>
  <div class="relative" ref="rootRef">
    <!-- 触发器 -->
    <button type="button" class="drp-trigger flex items-center gap-1.5" @click="toggle">
      <Calendar :size="14" class="text-muted flex-shrink-0" />
      <span v-if="start || end" class="truncate">
        {{ start ? dispDate(start) : '...' }}
        <span class="text-muted mx-0.5">&ndash;</span>
        {{ end ? dispDate(end) : '...' }}
      </span>
      <span v-else class="text-muted">选择日期</span>
      <ChevronDown :size="12" class="text-muted ml-auto flex-shrink-0" />
    </button>

    <!-- 日历面板 -->
    <teleport to="body">
      <div v-if="open" ref="panelRef" class="drp-panel" :style="panelStyle">
        <!-- 快捷预设 -->
        <div class="drp-presets">
          <button v-for="p in presets" :key="p.label" type="button"
            @click="applyPreset(p)"
            :class="['drp-chip', isActivePreset(p) && 'drp-chip-active']">
            {{ p.label }}
          </button>
        </div>

        <!-- 日历主体 -->
        <div class="drp-cal">
          <div class="drp-cal-nav">
            <button type="button" @click="moveMonth(-1)" class="drp-nav-btn" aria-label="上个月">
              <ChevronLeft :size="14" />
            </button>
            <span class="drp-nav-title">{{ viewYear }}年{{ viewMonth + 1 }}月</span>
            <button type="button" @click="moveMonth(1)" class="drp-nav-btn" aria-label="下个月">
              <ChevronRight :size="14" />
            </button>
          </div>

          <div class="drp-weekdays">
            <span v-for="d in ['一','二','三','四','五','六','日']" :key="d">{{ d }}</span>
          </div>

          <div class="drp-grid" @mouseleave="onGridLeave">
            <button v-for="(c, i) in cells" :key="i"
              type="button"
              :disabled="!c.cur"
              @click="c.cur && pickDate(c.d)"
              @mouseenter="c.cur && onHover(c.d)"
              :class="cellCls(c)">
              <span class="drp-num">{{ c.n }}</span>
            </button>
          </div>
        </div>

        <!-- 底部 -->
        <div class="drp-footer">
          <span class="drp-hint">
            <template v-if="picking">请选择结束日期</template>
            <template v-else-if="start && end">{{ fmtFull(start) }} &ndash; {{ fmtFull(end) }}</template>
          </span>
          <button v-if="start || end || picking" type="button" @click="doClear" class="drp-clear">清除</button>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Calendar, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-vue-next'

const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' }
})

const emit = defineEmits(['update:start', 'update:end', 'change'])

// 面板状态
const open = ref(false)
const rootRef = ref(null)
const panelRef = ref(null)
const panelStyle = ref({})

// 日历视图
const viewYear = ref(new Date().getFullYear())
const viewMonth = ref(new Date().getMonth())

// 选择状态
const picking = ref(false)
const anchor = ref('')
const hovered = ref('')

/* ──── 工具函数 ──── */
const pad = n => String(n).padStart(2, '0')
const iso = (y, m, d) => `${y}-${pad(m + 1)}-${pad(d)}`
const todayISO = () => new Date().toISOString().slice(0, 10)
const dispDate = s => { const p = s.split('-'); return `${p[1]}/${p[2]}` }
const fmtFull = s => { const p = s.split('-'); return `${p[0]}/${p[1]}/${p[2]}` }

/* ──── 快捷预设 ──── */
const addDays = (base, n) => { const d = new Date(base); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10) }
const sow = () => { const d = new Date(); const w = d.getDay() || 7; d.setDate(d.getDate() - w + 1); return d.toISOString().slice(0, 10) }
const som = (o = 0) => { const d = new Date(); d.setMonth(d.getMonth() + o, 1); return d.toISOString().slice(0, 10) }
const eom = (o = 0) => { const d = new Date(); d.setMonth(d.getMonth() + o + 1, 0); return d.toISOString().slice(0, 10) }
const soq = () => { const d = new Date(); return new Date(d.getFullYear(), Math.floor(d.getMonth() / 3) * 3, 1).toISOString().slice(0, 10) }

const presets = [
  { label: '今天', s: () => todayISO(), e: () => todayISO() },
  { label: '本周', s: sow, e: todayISO },
  { label: '本月', s: () => som(), e: todayISO },
  { label: '上月', s: () => som(-1), e: () => eom(-1) },
  { label: '近7天', s: () => addDays(todayISO(), -6), e: todayISO },
  { label: '近30天', s: () => addDays(todayISO(), -29), e: todayISO },
  { label: '本季度', s: soq, e: todayISO },
]

const isActivePreset = p => props.start === p.s() && props.end === p.e()

const applyPreset = p => {
  picking.value = false
  emit('update:start', p.s())
  emit('update:end', p.e())
  emit('change')
  open.value = false
}

/* ──── 日历单元格 ──── */
const cells = computed(() => {
  const y = viewYear.value, m = viewMonth.value
  const firstDow = (() => { let d = new Date(y, m, 1).getDay() - 1; return d < 0 ? 6 : d })()
  const lastDate = new Date(y, m + 1, 0).getDate()
  const td = todayISO()
  const arr = []

  // 上月尾部
  const prevLast = new Date(y, m, 0).getDate()
  for (let i = firstDow - 1; i >= 0; i--) {
    const n = prevLast - i
    const pm = m === 0 ? 11 : m - 1, py = m === 0 ? y - 1 : y
    arr.push({ n, d: iso(py, pm, n), cur: false })
  }

  // 当月
  for (let d = 1; d <= lastDate; d++) {
    const s = iso(y, m, d)
    arr.push({ n: d, d: s, cur: true, today: s === td })
  }

  // 下月头部（填满至 42 格，保持高度稳定）
  let nd = 1
  while (arr.length < 42) {
    const nm = m === 11 ? 0 : m + 1, ny = m === 11 ? y + 1 : y
    arr.push({ n: nd, d: iso(ny, nm, nd), cur: false })
    nd++
  }

  return arr
})

/* ──── 范围计算 ──── */
const range = computed(() => {
  let s, e
  if (picking.value) {
    s = anchor.value
    e = hovered.value || anchor.value
  } else {
    s = props.start
    e = props.end
  }
  if (!s || !e) return ['', '']
  return s <= e ? [s, e] : [e, s]
})

const cellCls = c => {
  const cls = ['drp-cell']
  if (!c.cur) { cls.push('drp-other'); return cls }
  if (c.today) cls.push('drp-today')

  const [rs, re] = range.value
  if (!rs || !re) return cls

  if (rs === re && c.d === rs) {
    cls.push('drp-edge')
  } else if (c.d === rs) {
    cls.push('drp-edge', 'drp-range-start')
  } else if (c.d === re) {
    cls.push('drp-edge', 'drp-range-end')
  } else if (c.d > rs && c.d < re) {
    cls.push('drp-mid')
  }

  if (picking.value && (c.d >= rs && c.d <= re)) cls.push('drp-picking')
  return cls
}

/* ──── 交互 ──── */
const pickDate = dateStr => {
  if (!picking.value) {
    // 第一次点击：设定起点
    anchor.value = dateStr
    picking.value = true
    hovered.value = dateStr
  } else {
    // 第二次点击：完成选择
    const a = anchor.value, b = dateStr
    const s = a <= b ? a : b, e = a <= b ? b : a
    picking.value = false
    anchor.value = ''
    hovered.value = ''
    emit('update:start', s)
    emit('update:end', e)
    emit('change')
    open.value = false
  }
}

const onHover = d => {
  if (picking.value) hovered.value = d
}

const onGridLeave = () => {
  if (picking.value) hovered.value = anchor.value
}

const doClear = () => {
  picking.value = false
  anchor.value = ''
  hovered.value = ''
  emit('update:start', '')
  emit('update:end', '')
  emit('change')
  open.value = false
}

/* ──── 月份导航 ──── */
const moveMonth = delta => {
  let m = viewMonth.value + delta, y = viewYear.value
  if (m < 0) { m = 11; y-- }
  if (m > 11) { m = 0; y++ }
  viewMonth.value = m
  viewYear.value = y
}

/* ──── 面板定位 ──── */
const updatePos = async () => {
  await nextTick()
  if (!rootRef.value || !panelRef.value) return
  const r = rootRef.value.getBoundingClientRect()
  const ph = panelRef.value.offsetHeight
  const pw = panelRef.value.offsetWidth
  const below = window.innerHeight - r.bottom
  const top = below < ph + 8 ? r.top - ph - 4 : r.bottom + 4
  const left = Math.min(Math.max(8, r.left), window.innerWidth - pw - 8)
  panelStyle.value = { top: `${top}px`, left: `${left}px` }
}

const toggle = () => {
  open.value = !open.value
  if (open.value) {
    picking.value = false
    anchor.value = ''
    hovered.value = ''
    if (props.start) {
      const p = props.start.split('-')
      viewYear.value = +p[0]
      viewMonth.value = +p[1] - 1
    } else {
      const now = new Date()
      viewYear.value = now.getFullYear()
      viewMonth.value = now.getMonth()
    }
  }
}

watch(open, v => { if (v) updatePos() })

// 点击外部关闭
const onDocClick = e => {
  if (open.value && rootRef.value && !rootRef.value.contains(e.target) && panelRef.value && !panelRef.value.contains(e.target)) {
    open.value = false
    picking.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<style scoped>
/* 面板 */
.drp-panel {
  position: fixed;
  z-index: 50;
  background: var(--surface);
  border-radius: var(--r-xl);
  box-shadow: var(--sh-lg);
  border: 1px solid var(--border);
  padding: 12px;
  width: 300px;
  user-select: none;
}

/* 预设标签 */
.drp-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 10px;
}
.drp-chip {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: var(--r-md);
  font-weight: 500;
  background: var(--elevated);
  color: var(--text-secondary);
  border: none;
  cursor: pointer;
  transition: all var(--duration-fast);
}
.drp-chip:hover { color: var(--text); }
.drp-chip-active { background: var(--primary); color: var(--on-primary); }

/* 日历导航 */
.drp-cal-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.drp-nav-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.drp-nav-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-md);
  border: none;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: all var(--duration-fast);
}
.drp-nav-btn:hover { background: var(--elevated); color: var(--text); }

/* 星期标题 */
.drp-weekdays {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  margin-bottom: 2px;
}
.drp-weekdays span {
  text-align: center;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
  padding: 4px 0;
}

/* 日期网格 */
.drp-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
}

/* 单元格基础 */
.drp-cell {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 36px;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 0;
}
.drp-cell:disabled { cursor: default; }
.drp-num {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 13px;
  color: var(--text);
  position: relative;
  z-index: 1;
  transition: background var(--duration-fast), color var(--duration-fast);
}

/* 悬停效果（非选中状态） */
.drp-cell:not(.drp-edge):not(.drp-mid):not(:disabled):hover .drp-num {
  background: var(--elevated);
}

/* 非当月日期 */
.drp-other { pointer-events: none; }
.drp-other .drp-num { color: var(--text-muted); opacity: 0.3; }

/* 今天标记 */
.drp-today .drp-num { font-weight: 700; }
.drp-today:not(.drp-edge) .drp-num::after {
  content: '';
  position: absolute;
  bottom: 2px;
  left: 50%;
  transform: translateX(-50%);
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--primary);
}

/* 范围中间（色带） */
.drp-mid { background: var(--primary-muted); }

/* 范围起点（色带右半） */
.drp-range-start {
  background: linear-gradient(to right, transparent 50%, var(--primary-muted) 50%);
}
.drp-range-start .drp-num { background: var(--primary); color: var(--on-primary); }

/* 范围终点（色带左半） */
.drp-range-end {
  background: linear-gradient(to left, transparent 50%, var(--primary-muted) 50%);
}
.drp-range-end .drp-num { background: var(--primary); color: var(--on-primary); }

/* 单日选中（起点=终点） */
.drp-edge:not(.drp-range-start):not(.drp-range-end) .drp-num {
  background: var(--primary);
  color: var(--on-primary);
}

/* 选择中状态：稍透明 */
.drp-picking.drp-range-start .drp-num,
.drp-picking.drp-range-end .drp-num {
  opacity: 0.85;
}

/* 选中日期隐藏今天圆点 */
.drp-edge .drp-num::after { display: none; }

/* 底部 */
.drp-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
  min-height: 24px;
}
.drp-hint { font-size: 12px; color: var(--text-muted); }
.drp-clear {
  font-size: 12px;
  color: var(--text-muted);
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--r-sm);
  transition: all var(--duration-fast);
  margin-left: auto;
}
.drp-clear:hover { color: var(--text); background: var(--elevated); }

/* 触发器按钮（替代 toolbar-select） */
.drp-trigger {
  appearance: none;
  -webkit-appearance: none;
  background: var(--elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 5px 10px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  white-space: nowrap;
  min-height: 32px;
  transition: border-color var(--duration-fast);
}
.drp-trigger:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 2px var(--primary-ring);
}
</style>
