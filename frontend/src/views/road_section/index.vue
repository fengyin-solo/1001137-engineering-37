<template>
  <section class="page" data-module="road_section">
    <header class="page-head">
      <div>
        <h2>路段管理管理</h2>
        <p class="page-desc">维护管养路段，围绕路段编号、路段名称、起止桩号、道路等级做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记管养路段</button>
        <button class="btn" type="button" @click="exportRows">导出路段管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无路段管理数据，可先登记管养路段</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条路段管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="todo-panel">
      <header class="todo-head">
        <h3>路段待办（启动门禁检查结论同步）</h3>
        <label class="todo-filter">
          <input v-model="todoRoad" placeholder="按路段编号筛选，如 ROAD-0005" @keyup.enter="loadTodos" />
          <button class="btn" type="button" @click="loadTodos">筛选</button>
          <button class="btn ghost" type="button" @click="clearTodoFilter">全部</button>
        </label>
      </header>
      <table class="data-table compact">
        <thead>
          <tr><th>路段编号</th><th>路段名称</th><th>工程编号</th><th>受阻阶段</th><th>阻断场景</th><th>处置路径</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="todo in todos" :key="String(todo.id)">
            <td>{{ todo['路段编号'] }}</td>
            <td>{{ todo['路段名称'] }}</td>
            <td>{{ todo['工程编号'] }}</td>
            <td>{{ todo['阶段'] }}</td>
            <td class="todo-scene">{{ todo['场景'] }}</td>
            <td class="todo-path">{{ todo['处置路径'] }}</td>
            <td>{{ todo.status === '待办' ? '待办' : '已闭环' }}</td>
          </tr>
          <tr v-if="!todos.length">
            <td colspan="7" class="empty-state">暂无路段待办：门禁通过后待办自动闭环</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/road_section'
const columns = ["路段编号", "路段名称", "起止桩号", "道路等级", "车道数", "路面类型", "管养单位", "路段状态"]
const actions = ["设置施工", "设置限行", "恢复通行"]
const statuses = ["正常", "施工", "限行", "封闭"]
const stats = [{"label": "正常路段", "value": 0}, {"label": "施工路段", "value": 0}, {"label": "限行路段", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const todos = ref<Row[]>([])
const todoRoad = ref('')

async function loadTodos() {
  const query = new URLSearchParams()
  if (todoRoad.value.trim()) query.set('road_code', todoRoad.value.trim())
  try {
    const response = await request(`/api/readiness/todos?${query.toString()}`)
    if (!response.ok) throw new Error('路段待办读取失败')
    const payload = await response.json()
    todos.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路段待办读取失败'
  }
}

function clearTodoFilter() {
  todoRoad.value = ''
  void loadTodos()
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '管养路段登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('路段管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路段管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('管养路段列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路段管理列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadTodos()
})
</script>

<style scoped>
.todo-panel {
  margin-top: 18px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px 16px;
  background: #fff;
}
.todo-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.todo-head h3 { margin: 4px 0; font-size: 15px; }
.todo-filter { display: inline-flex; gap: 8px; align-items: center; }
.todo-filter input { padding: 6px 8px; border: 1px solid #d1d5db; border-radius: 4px; min-width: 220px; }
.data-table.compact { font-size: 13px; }
.todo-scene { color: #b91c1c; }
.todo-path { color: #475569; max-width: 360px; }
</style>
