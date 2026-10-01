<template>
  <section class="page" data-module="readiness">
    <header class="page-head">
      <div>
        <h2>启动门禁</h2>
        <p class="page-desc">
          基础数据核验 → 配置探测 → 外部服务连通 → 开放发布，只能逐级推进；跳级、倒序与覆盖已批准工程一律阻断。
          结论同步至工程台账、路段待办与运行日志。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="runBatch">执行探测批次</button>
        <button class="btn" type="button" @click="createBatch">建立探测批次</button>
        <button class="btn ghost" type="button" @click="resetProbes">复位未完成项</button>
        <button class="btn ghost" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in summary.cards ?? []" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p v-if="message" class="gate-message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</p>

    <!-- 阻断场景与处置路径 -->
    <section class="gate-panel">
      <h3>阻断场景分布与处置路径</h3>
      <div class="scenario-grid">
        <span v-if="!Object.keys(summary.scenario_counts ?? {}).length" class="empty-state">当前无阻断工程</span>
          <span
          v-for="(count, key) in summary.scenario_counts ?? {}"
          :key="String(key)"
          class="scenario-chip"
          :class="scenarioClass(String(key))"
        >
          {{ String(key) }} × {{ count }}
          <em>{{ (summary.dispositions ?? {})[key] ?? '暂无处置口径' }}</em>
        </span>
      </div>
    </section>

    <!-- 外部服务 / 数据源状态 -->
    <section class="gate-panel">
      <h3>外部服务与数据源连通状态（故障注入：超时与不可达分流验证）</h3>
      <div class="fault-row">
        <table class="data-table compact">
          <thead><tr><th>探测目标</th><th>状态</th><th>注入</th></tr></thead>
          <tbody>
            <tr v-for="(status, name) in faultTargets" :key="String(name)">
              <td>{{ name }}</td>
              <td :class="faultClass(String(status))">{{ faultLabel(String(status)) }}</td>
              <td class="row-actions">
                <button class="link" type="button" @click="inject(String(name), 'ok')">恢复</button>
                <button class="link" type="button" @click="inject(String(name), 'timeout')">置超时</button>
                <button class="link" type="button" @click="inject(String(name), 'unreachable')">置不可达</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 门禁台账 -->
    <section class="gate-panel">
      <h3>工程门禁台账</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>工程编号</th>
            <th>工程名称</th>
            <th v-for="stage in stages" :key="stage">{{ stage }}</th>
            <th>门禁结论 / 处置</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="gate in gates" :key="gate['工程编号']">
            <td>{{ gate['工程编号'] }}</td>
            <td>{{ gate['工程名称'] }}</td>
            <td v-for="(s, idx) in gate.stages" :key="idx" class="stage-cell" :class="stageClass(s.status)">
              <strong>{{ stageLabel(s.status) }}</strong>
              <small v-if="s.scenario">（{{ s.scenario }}）</small>
            </td>
            <td class="conclusion-cell">
              <template v-if="gate.approved">已批准沿用（禁止覆盖）</template>
              <template v-else-if="gate.release_ready">
                <span class="ok-text">可开放：允许批准开工</span>
              </template>
              <template v-else>
                <span class="error-text">{{ gate['阻断场景'] }}</span>
                <small v-if="gate['处置路径']"><br />{{ gate['处置路径'] }}</small>
              </template>
            </td>
            <td class="row-actions">
              <template v-if="!gate.approved && !gate.release_ready">
                <button class="link" type="button" @click="advance(gate)">
                  核验「{{ stages[gate.current_idx] }}」
                </button>
                <button
                  v-if="resolveKinds.includes(String(gate['阻断场景'] ?? ''))"
                  class="link"
                  type="button"
                  @click="resolve(gate)"
                >
                  {{ resolveLabel(gate['阻断场景']) }}
                </button>
              </template>
              <span v-else class="empty-state">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 路段待办 -->
    <section class="gate-panel">
      <h3>路段待办（检查结论同步至施工路段清单）</h3>
      <table class="data-table compact">
        <thead>
          <tr><th>路段编号</th><th>路段名称</th><th>工程编号</th><th>受阻阶段</th><th>场景</th><th>处置路径</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="todo in todos" :key="todo.id">
            <td>{{ todo['路段编号'] }}</td>
            <td>{{ todo['路段名称'] }}</td>
            <td>{{ todo['工程编号'] }}</td>
            <td>{{ todo['阶段'] }}</td>
            <td :class="scenarioClass(String(todo['场景']))">{{ todo['场景'] }}</td>
            <td>{{ todo['处置路径'] }}</td>
            <td>{{ todo.status === '待办' ? '待办' : '已闭环' }}</td>
          </tr>
          <tr v-if="!todos.length">
            <td colspan="7" class="empty-state">暂无路段待办：阻断工程核验通过后待办自动闭环</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 运行日志面板 -->
    <section class="gate-panel">
      <h3>运行日志面板</h3>
      <div class="log-panel">
        <p v-for="log in logs" :key="log.id" class="log-line" :class="`log-${log.level}`">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-level">[{{ log.level }}]</span>
          <span class="log-code">{{ log['工程编号'] }}</span>
          <span class="log-stage">{{ log['阶段'] }}</span>
          <span>{{ log.event }}</span>
          <em>{{ log.detail }}</em>
        </p>
        <p v-if="!logs.length" class="empty-state">暂无运行日志</p>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type GateStage = { stage: string; status: string; scenario: string | null; reason: string | null; disposition: string | null }
type Gate = {
  id: number
  工程编号: string
  工程名称: string
  approved: boolean
  current_idx: number
  release_ready: boolean
  阻断场景: string | null
  处置路径: string | null
  stages: GateStage[]
}
type Todo = Record<string, string | number>
type LogLine = Record<string, string>

const ENDPOINT = '/api/readiness'
const resolveKinds = ['边界缺失', '材料缺失', '前置件缺失', '配置缺失', '外部超时', '数据源不可达']

const gates = ref<Gate[]>([])
const todos = ref<Todo[]>([])
const logs = ref<LogLine[]>([])
const message = ref('')
const messageOk = ref(true)
const summary = ref<Record<string, any>>({})

const stages = computed<string[]>(() => summary.value.stages ?? [])
const faultTargets = computed<Record<string, string>>(() => ({
  ...(summary.value.service_status ?? {}),
  ...(summary.value.datasource_status ?? {}),
}))

function stageLabel(status: string): string {
  return { 通过: '✓ 通过', 阻断: '✗ 阻断', 待检: '○ 待检', 待复检: '↻ 待复检', 已批准: '★ 已批准' }[status] ?? status
}

function stageClass(status: string): string {
  if (status === '通过' || status === '已批准') return 'stage-pass'
  if (status === '阻断') return 'stage-block'
  if (status === '待复检') return 'stage-recheck'
  return 'stage-pending'
}

function scenarioClass(key: string): string {
  if (key.includes('超时')) return 'chip-timeout'
  if (key.includes('不可达')) return 'chip-unreachable'
  if (key.includes('无工程') || key.includes('空')) return 'chip-empty'
  return 'chip-missing'
}

function faultClass(status: string): string {
  if (status === 'ok') return 'ok-text'
  if (status === 'timeout') return 'warn-text'
  return 'error-text'
}

function faultLabel(status: string): string {
  return { ok: '已连通', timeout: '超时（超时处置）', unreachable: '不可达（不可达处置）', manual: '人工核验中' }[status] ?? status
}

function resolveLabel(kind: string | null): string {
  return {
    边界缺失: '补录边界',
    材料缺失: '补齐材料',
    前置件缺失: '补交前置件',
    配置缺失: '补录配置',
    外部超时: '重试连通',
    数据源不可达: '转人工核验',
  }[kind ?? ''] ?? '处置'
}

function flash(ok: boolean, text: string) {
  messageOk.value = ok
  message.value = text
}

async function post(path: string, body?: unknown) {
  const response = await request(`${ENDPOINT}${path}`, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  })
  return response.json()
}

async function reload() {
  message.value = ''
  try {
    const [sum, gateList, todoList, logList] = await Promise.all([
      request(`${ENDPOINT}/summary`).then((r) => r.json()),
      request(`${ENDPOINT}/gates`).then((r) => r.json()),
      request(`${ENDPOINT}/todos`).then((r) => r.json()),
      request(`${ENDPOINT}/logs?limit=60`).then((r) => r.json()),
    ])
    summary.value = sum
    gates.value = gateList.items ?? []
    todos.value = todoList.items ?? []
    logs.value = logList.items ?? []
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '门禁数据读取失败')
  }
}

async function advance(gate: Gate) {
  const stage = stages.value[gate.current_idx]
  const response = await request(`${ENDPOINT}/gates/${encodeURIComponent(gate.工程编号)}/advance`, {
    method: 'POST',
    body: JSON.stringify({ values: { stage } }),
  })
  const payload = await response.json()
  if (!response.ok) {
    // 409：跳级 / 倒序 / 覆盖批准件被状态机拦下
    flash(false, `已阻断：${payload.message}`)
  } else if (payload.ok) {
    flash(true, `${gate.工程编号}「${payload.stage}」核验通过${payload.gate?.release_ready ? '，已开放发布' : ''}`)
  } else {
    flash(false, `${gate.工程编号}「${stage}」阻断：${payload.scenario}。${payload.disposition ?? ''}`)
  }
  await reload()
}

async function resolve(gate: Gate) {
  const kind = gate.阻断场景
  if (!kind) return
  const response = await request(`${ENDPOINT}/gates/${encodeURIComponent(gate.工程编号)}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ values: { kind } }),
  })
  const payload = await response.json()
  if (!response.ok) {
    flash(false, `处置被阻断：${payload.message}`)
  } else {
    flash(true, `${gate.工程编号}：${payload.message}，请重新核验「${stages.value[gate.current_idx]}」`)
  }
  await reload()
}

async function createBatch() {
  const payload = await post('/probes/batch')
  if (payload.total === 0) {
    flash(true, `空态处置：批次 ${payload.batch_no} 无待启动工程，登记空批次结论，不产生待办`)
  } else if (payload.idempotent) {
    flash(true, `批次 ${payload.batch_no} 已存在（幂等）：${payload.total} 项任务，完成 ${payload.done} 项`)
  } else {
    flash(true, `批次 ${payload.batch_no} 已建立：${payload.total} 项任务，已批准工程不入批`)
  }
  await reload()
}

async function runBatch() {
  const response = await request(`${ENDPOINT}/probes/batch/run`, { method: 'POST', body: '{}' })
  const payload = await response.json()
  if (!response.ok) {
    flash(false, payload.message ?? '探测批次执行被阻断')
  } else {
    flash(true, `批次 ${payload.batch_no}：${payload.status}，完成 ${payload.done ?? 0} 项，受阻 ${payload.blocked ?? 0} 项（完成项不重跑）`)
  }
  await reload()
}

async function resetProbes() {
  const response = await request(`${ENDPOINT}/probes/reset`, { method: 'POST', body: '{}' })
  const payload = await response.json()
  if (!response.ok) {
    flash(false, payload.message ?? '复位被阻断')
  } else {
    flash(true, `批次 ${payload.batch_no} 已复位：未完成项 ${payload.reset} 个回到待执行，完成项保留，已批准工程不覆盖`)
  }
  await reload()
}

async function inject(name: string, status: string) {
  const response = await request(`${ENDPOINT}/faults`, {
    method: 'POST',
    body: JSON.stringify({ values: { target: name, status } }),
  })
  if (!response.ok) {
    flash(false, '故障注入失败')
  }
  await reload()
}

onMounted(reload)
</script>

<style scoped>
.gate-panel {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px 16px;
  margin: 12px 0;
}
.gate-panel h3 {
  margin: 4px 0 10px;
  font-size: 15px;
}
.gate-message {
  padding: 8px 12px;
  border-radius: 6px;
  background: #f8fafc;
}
.ok-text { color: #15803d; }
.error-text { color: #b91c1c; }
.warn-text { color: #b45309; }
.scenario-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.scenario-chip {
  display: inline-flex;
  flex-direction: column;
  max-width: 320px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 13px;
  border: 1px solid;
}
.scenario-chip em {
  font-style: normal;
  font-size: 12px;
  color: #475569;
}
.chip-timeout { border-color: #f59e0b; background: #fffbeb; }
.chip-unreachable { border-color: #ef4444; background: #fef2f2; }
.chip-empty { border-color: #94a3b8; background: #f8fafc; }
.chip-missing { border-color: #f97316; background: #fff7ed; }
.stage-cell { text-align: center; font-size: 13px; }
.stage-cell small { color: #b91c1c; }
.stage-pass { color: #15803d; }
.stage-block { color: #b91c1c; }
.stage-recheck { color: #b45309; }
.stage-pending { color: #94a3b8; }
.conclusion-cell { font-size: 13px; max-width: 300px; }
.conclusion-cell small { color: #64748b; }
.data-table.compact { font-size: 13px; }
.log-panel {
  max-height: 320px;
  overflow: auto;
  background: #0f172a;
  border-radius: 6px;
  padding: 10px;
}
.log-line {
  margin: 2px 0;
  font-size: 12px;
  color: #cbd5e1;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.log-line em { font-style: normal; color: #94a3b8; }
.log-time { color: #64748b; margin-right: 6px; }
.log-level { margin-right: 6px; }
.log-code { color: #7dd3fc; margin-right: 6px; }
.log-stage { color: #c4b5fd; margin-right: 6px; }
.log-INFO .log-level { color: #4ade80; }
.log-WARN .log-level { color: #fbbf24; }
.log-ERROR .log-level { color: #f87171; }
.fault-row { max-width: 720px; }
</style>
