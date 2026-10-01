<template>
  <section class="page" data-module="startup">
    <header class="page-head">
      <div>
        <h2>工程启动检查</h2>
        <p class="page-desc">
          状态机门禁：基础数据核验 → 配置探测 → 外部服务连通 → 开放发布，只能逐级推进，跳级与倒序一律阻断；
          空态、缺失、超时、不可达各有独立处置路径。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="runMigrate">存量工程迁移回填</button>
        <button class="btn ghost" type="button" @click="reload">刷新面板</button>
      </div>
    </header>

    <h3 class="block-title">工程汇总页</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>工程编号</th><th>当前阶段</th><th>门禁状态</th><th>启动检查</th><th>就绪检查</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in gates" :key="item.工程编号" :class="{ 'row-active': item.工程编号 === currentCode }">
          <td>{{ item.工程编号 }}</td>
          <td>{{ item.当前阶段 }}</td>
          <td>
            <span v-if="item.已发布">已开放发布</span>
            <span v-else-if="item.阻断" class="error-text">已阻断</span>
            <span v-else>推进中</span>
          </td>
          <td>{{ item.启动检查 || '—' }}</td>
          <td>{{ item.就绪检查 || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="selectProject(item.工程编号)">门禁明细</button>
          </td>
        </tr>
        <tr v-if="!gates.length">
          <td colspan="6" class="empty-state">工程台账为空，请先在养护工程模块登记</td>
        </tr>
      </tbody>
    </table>

    <template v-if="gate">
      <h3 class="block-title">状态机门禁 · {{ gate.工程编号 }}</h3>
      <div class="stat-row">
        <article
          v-for="stage in gate.stages"
          :key="stage.key"
          class="stat-card stage-card"
          :data-state="stage.状态"
        >
          <span class="stat-label">{{ stage.label }}</span>
          <strong class="stat-value">{{ stage.状态 }}</strong>
          <p v-if="stage.结论" class="stage-detail">{{ stage.结论.明细 }}</p>
          <p v-if="stage.结论 && stage.结论.处置" class="stage-remedy">处置：{{ stage.结论.处置 }}</p>
        </article>
      </div>
      <p v-if="gate.阻断" class="error-text">
        启动已阻断：{{ gate.阻断原因 }}。配置或前置件失败必须阻断启动，修复后请复位门禁继续未完成阶段。
      </p>
      <div class="page-actions gate-actions">
        <button
          v-if="nextStage"
          class="btn primary"
          type="button"
          :disabled="gate.阻断"
          @click="advance(nextStage.key)"
        >
          推进：{{ nextStage.label }}
        </button>
        <span v-else class="stage-done">已全部通过，工程已开放发布</span>
        <button class="btn ghost" type="button" @click="resetGate">复位门禁</button>
      </div>

      <h3 class="block-title">里程碑裁决（以计划基线为准）</h3>
      <table class="data-table">
        <thead>
          <tr><th>里程碑</th><th>基线日期</th><th>填报日期</th><th>裁决日期</th><th>结论</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in milestones" :key="item.里程碑">
            <td>{{ item.里程碑 }}</td>
            <td>{{ item.基线日期 }}</td>
            <td>{{ item.填报日期 || '—' }}</td>
            <td>{{ item.裁决日期 }}</td>
            <td>{{ item.结论 }}</td>
          </tr>
          <tr v-if="!milestones.length">
            <td colspan="5" class="empty-state">该工程尚未建立计划基线</td>
          </tr>
        </tbody>
      </table>
    </template>

    <h3 class="block-title">跨运行空间就绪检查（工程台账 × 施工路段 × 材料计划）</h3>
    <form class="filter-bar" @submit.prevent="runProbe">
      <label class="filter-item">
        <span>批次号</span>
        <input v-model="batchId" placeholder="如 B20261001-1" />
      </label>
      <button class="btn primary" type="submit">运行探测批次</button>
      <button class="btn ghost" type="button" @click="resetProbe">复位批次</button>
    </form>
    <table v-if="batch" class="data-table">
      <thead>
        <tr><th>工程编号</th><th>探测结果</th><th>类别</th><th>明细</th><th>处置路径</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in batchItems" :key="item.工程编号">
          <td>{{ item.工程编号 }}</td>
          <td>{{ item.结果 }}</td>
          <td>{{ categoryLabel(item.类别) }}</td>
          <td>{{ item.明细 }}</td>
          <td>{{ item.处置 || '—' }}</td>
        </tr>
        <tr v-if="!batchItems.length">
          <td colspan="5" class="empty-state">批次 {{ batch.批次号 }} 尚未产生结论</td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">外部服务通道</h3>
    <table class="data-table">
      <thead>
        <tr><th>通道</th><th>当前状态</th><th>切换状态（演练超时与不可达处置路径）</th></tr>
      </thead>
      <tbody>
        <tr v-for="channel in channels" :key="channel.id">
          <td>{{ channel.通道 }}</td>
          <td>{{ channel.状态 }}</td>
          <td class="row-actions">
            <button
              v-for="state in channelStates"
              :key="state"
              class="link"
              type="button"
              @click="setChannel(channel.id, state)"
            >
              {{ state }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">运行日志面板</h3>
    <table class="data-table">
      <thead>
        <tr><th>时间</th><th>来源</th><th>级别</th><th>内容</th></tr>
      </thead>
      <tbody>
        <tr v-for="log in logs" :key="log.id">
          <td>{{ log.时间 }}</td>
          <td>{{ log.来源 }}</td>
          <td>{{ log.级别 }}</td>
          <td>{{ log.内容 }}</td>
        </tr>
        <tr v-if="!logs.length">
          <td colspan="4" class="empty-state">暂无运行日志</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ logTotal }} 条运行日志</span>
      <span v-if="notice" class="notice-text">{{ notice }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

interface Conclusion {
  结果: string
  类别: string
  处置: string
  明细: string
  时间?: string
}

interface StageView {
  key: string
  label: string
  状态: string
  结论: Conclusion | null
}

interface GateView {
  工程编号: string
  当前阶段: string
  阻断: boolean
  阻断原因: string
  已发布: boolean
  启动检查: string
  就绪检查: string
  stages: StageView[]
}

interface Milestone {
  里程碑: string
  基线日期: string
  填报日期: string
  裁决日期: string
  结论: string
}

interface ProbeItem {
  工程编号: string
  结果: string
  类别: string
  处置: string
  明细: string
}

interface ProbeBatch {
  id: number
  批次号: string
  项目: string[]
  已完成: Record<string, ProbeItem>
  状态: string
}

interface Channel {
  id: number
  通道: string
  状态: string
}

interface RunLog {
  id: number
  时间: string
  来源: string
  级别: string
  内容: string
}

interface ActionResult<T> {
  ok: boolean
  message: string
  entry: T | null
}

const CATEGORY_LABELS: Record<string, string> = {
  ok: '相符',
  no_project: '无工程',
  empty: '空态',
  missing: '缺失',
  boundary_missing: '边界缺失',
  timeout: '外部服务超时',
  unreachable: '数据源不可达',
  config: '配置前置件',
}

const channelStates = ['正常', '超时', '不可达']

const gates = ref<GateView[]>([])
const gate = ref<GateView | null>(null)
const currentCode = ref('')
const milestones = ref<Milestone[]>([])
const batchId = ref('')
const batch = ref<ProbeBatch | null>(null)
const channels = ref<Channel[]>([])
const logs = ref<RunLog[]>([])
const logTotal = ref(0)
const notice = ref('')
const errorMessage = ref('')

const nextStage = computed(() => gate.value?.stages.find((stage) => stage.状态 === '待推进') ?? null)
const batchItems = computed(() => (batch.value ? Object.values(batch.value.已完成) : []))

function categoryLabel(category: string) {
  return CATEGORY_LABELS[category] ?? category
}

async function post<T>(path: string, values: Record<string, unknown>): Promise<ActionResult<T>> {
  const response = await request(path, { method: 'POST', body: JSON.stringify({ values }) })
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，操作未生效`)
  }
  return (await response.json()) as ActionResult<T>
}

async function loadGates() {
  const payload = await fetchJson<{ items: GateView[] }>('/api/startup/gates')
  gates.value = payload.items
}

async function loadGate(code: string) {
  gate.value = await fetchJson<GateView>(`/api/startup/gates/${code}`)
  const payload = await fetchJson<{ items: Milestone[] }>(`/api/startup/milestones/${code}`)
  milestones.value = payload.items
}

async function loadChannels() {
  const payload = await fetchJson<{ items: Channel[] }>('/api/startup/channels')
  channels.value = payload.items
}

async function loadLogs() {
  const payload = await fetchJson<{ items: RunLog[]; total: number }>('/api/startup/logs?size=50')
  logs.value = payload.items
  logTotal.value = payload.total
}

async function selectProject(code: string) {
  currentCode.value = code
  errorMessage.value = ''
  try {
    await loadGate(code)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '门禁明细读取失败'
  }
}

async function advance(stage: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await post<{ gate: GateView }>(`/api/startup/gates/${currentCode.value}/advance`, { stage })
    if (result.entry?.gate) {
      gate.value = result.entry.gate
    }
    notice.value = result.message
    if (!result.ok) {
      errorMessage.value = result.message
    }
    await Promise.all([loadGates(), loadLogs(), loadGate(currentCode.value)])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '门禁推进失败'
  }
}

async function resetGate() {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await post<{ gate: GateView }>(`/api/startup/gates/${currentCode.value}/reset`, {})
    if (result.entry?.gate) {
      gate.value = result.entry.gate
    }
    notice.value = result.message
    await Promise.all([loadGates(), loadLogs()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '门禁复位失败'
  }
}

async function runProbe() {
  errorMessage.value = ''
  notice.value = ''
  if (!batchId.value.trim()) {
    errorMessage.value = '请先填写批次号，探测任务按批次幂等'
    return
  }
  try {
    const result = await post<ProbeBatch>('/api/startup/probe', { 批次号: batchId.value.trim() })
    batch.value = result.entry
    notice.value = result.message
    await Promise.all([loadGates(), loadLogs()])
    if (currentCode.value) {
      await loadGate(currentCode.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '就绪探测失败'
  }
}

async function resetProbe() {
  errorMessage.value = ''
  notice.value = ''
  if (!batchId.value.trim()) {
    errorMessage.value = '请先填写要复位的批次号'
    return
  }
  try {
    const result = await post<ProbeBatch>(`/api/startup/probe/${batchId.value.trim()}/reset`, {})
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    batch.value = result.entry
    notice.value = result.message
    await loadLogs()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次复位失败'
  }
}

async function setChannel(id: number, state: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await post<Channel>(`/api/startup/channels/${id}`, { 状态: state })
    notice.value = result.message
    if (!result.ok) {
      errorMessage.value = result.message
    }
    await Promise.all([loadChannels(), loadLogs()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '通道切换失败'
  }
}

async function runMigrate() {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await post<{ migrated: string[]; preserved: string[]; untouched: string[] }>(
      '/api/startup/migrate',
      {},
    )
    notice.value = result.message
    await Promise.all([loadGates(), loadLogs()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '迁移回填失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    await Promise.all([loadGates(), loadChannels(), loadLogs()])
    if (!currentCode.value && gates.value.length) {
      currentCode.value = gates.value[0].工程编号
    }
    if (currentCode.value) {
      await loadGate(currentCode.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '启动检查面板读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.block-title {
  font-size: 14px;
  margin: 18px 0 8px;
}
.stage-card {
  min-height: 96px;
}
.stage-card[data-state='通过'] {
  border-color: #12b76a;
}
.stage-card[data-state='待推进'] {
  border-color: var(--brand);
}
.stage-card[data-state='阻断'] {
  border-color: #b42318;
}
.stage-detail {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--muted);
}
.stage-remedy {
  margin: 4px 0 0;
  font-size: 12px;
  color: #b42318;
}
.stage-done {
  font-size: 13px;
  color: #12b76a;
}
.gate-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 8px 0;
}
.row-active td {
  background: #eff6ff;
}
.notice-text {
  color: #12b76a;
}
</style>
