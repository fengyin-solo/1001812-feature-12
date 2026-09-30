<template>
  <section class="page" data-module="fund">
    <header class="page-head">
      <div>
        <h2>养护资金管理</h2>
        <p class="page-desc">维护资金记录，围绕资金编号、费用类别、项目名称、批复金额做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记资金记录</button>
        <button class="btn" type="button" @click="exportRows">导出养护资金清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="{ 'over-text': item.over }">{{ item.value }}</strong>
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
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'over-row': isOverspent(row) }">
          <td v-for="column in columns" :key="column" :class="{ 'over-text': column === '剩余额度' && isOverspent(row) }">
            <template v-if="moneyColumns.includes(column)">{{ formatMoney(row[column]) }}</template>
            <template v-else-if="column === '资金状态'">
              <span>{{ row['资金状态'] ?? row['status'] ?? '—' }}</span>
              <span v-if="isOverspent(row)" class="over-tag">超支</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无养护资金数据，可先登记资金记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条养护资金记录，剩余额度合计与上表逐行合计一致</span>
      <span v-if="infoMessage" class="info-text">{{ infoMessage }}</span>
      <span v-else-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Summary = {
  批复总额: number | null
  已用金额合计: number | null
  剩余额度合计: number | null
  超支项目: number
}

const ENDPOINT = '/api/fund'
const columns = ["资金编号", "费用类别", "项目名称", "批复金额", "已用金额", "剩余额度", "审批人员", "资金状态"]
const moneyColumns = ["批复金额", "已用金额", "剩余额度"]
const actions = ["提交审批", "确认批复", "标记超支"]
const statuses = ["待审批", "已批复", "执行中", "已超支"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const infoMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
// 合计直接取后端按当前列表行汇总的 summary，保证与逐行合计对得上。
const stats = ref<{ label: string; value: string | number; over?: boolean }[]>([
  { label: "批复总额", value: 0 },
  { label: "已用金额", value: 0 },
  { label: "剩余额度合计", value: 0 },
  { label: "超支项目", value: 0 },
])

function formatMoney(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '—'
  }
  const amount = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(amount) ? amount.toFixed(2) : String(value)
}

function isOverspent(row: Row): boolean {
  if (row['abnormal'] === true) {
    return true
  }
  const remaining = Number(row['剩余额度'])
  return row['剩余额度'] !== null && row['剩余额度'] !== '' && Number.isFinite(remaining) && remaining < 0
}

function applySummary(summary?: Summary) {
  const remaining = summary?.['剩余额度合计'] ?? 0
  stats.value = [
    { label: "批复总额", value: formatMoney(summary?.['批复总额']) },
    { label: "已用金额", value: formatMoney(summary?.['已用金额合计']) },
    { label: "剩余额度合计", value: formatMoney(remaining), over: remaining < 0 },
    { label: "超支项目", value: summary?.['超支项目'] ?? 0 },
  ]
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '资金记录登记入口尚未接入审批流'
  infoMessage.value = ''
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  infoMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload) {
      throw new Error('养护资金动作未生效，请稍后重试')
    }
    // 动作被拦下（如重复审批、额度算不出来）时用后端消息说明，不误报成功。
    if (payload.ok === false) {
      errorMessage.value = payload.message || '养护资金动作未生效'
    } else {
      infoMessage.value = payload.message || '操作已生效'
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护资金操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('资金记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    applySummary(payload.summary)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护资金列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.over-row {
  background: #fef3f2;
}

.over-text {
  color: #b42318;
  font-weight: 600;
}

.over-tag {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  background: #b42318;
  color: #fff;
  font-size: 11px;
  line-height: 18px;
}

.info-text {
  color: #1f6feb;
}
</style>
