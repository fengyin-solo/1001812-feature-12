<template>
  <section class="page" data-module="fund">
    <header class="page-head">
      <div>
        <h2>养护资金管理</h2>
        <p class="page-desc">提交审批后按批复金额重算已用金额与剩余额度，超支记录单独标出；卡片合计与列表逐行合计同源。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记资金记录</button>
        <button class="btn" type="button" @click="exportRows">导出养护资金清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card" :class="{ 'stat-warn': item.warn }">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="{ 'money-negative': item.warn && item.value > 0 }">{{ item.display }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>资金状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
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
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-overspent': isOverspent(row) }">
          <td v-for="column in columns" :key="column" :class="{ 'money-negative': isMoneyColumn(column) && isNegative(row[column]) }">
            {{ formatCell(column, row[column]) }}
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              :class="{ 'link-danger': action === '标记超支' }"
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
      <span>共 {{ total }} 条养护资金记录</span>
      <span v-if="feedback.message" :class="feedback.ok ? 'success-text' : 'error-text'">{{ feedback.message }}</span>
    </footer>

    <div v-if="approve.open" class="modal-mask" @click.self="closeApprove">
      <div class="modal">
        <h3>提交审批 · {{ approve.row?.['资金编号'] }}</h3>
        <p class="modal-tip">提交后按「批复金额 − 已用金额」重算剩余额度；同一笔资金重复审批只生效一次。</p>
        <label class="modal-field">
          <span>批复金额 <em>*</em></span>
          <input v-model="approve.approved" type="number" min="0" step="0.01" placeholder="例如 500000.00" />
        </label>
        <label class="modal-field">
          <span>已用金额（留空按 0）</span>
          <input v-model="approve.used" type="number" min="0" step="0.01" placeholder="例如 120000.00" />
        </label>
        <label class="modal-field">
          <span>审批人员</span>
          <input v-model="approve.approver" type="text" placeholder="审批人员姓名" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeApprove">取消</button>
          <button class="btn primary" type="button" :disabled="approve.submitting" @click="submitApprove">
            {{ approve.submitting ? '提交中…' : '确认提交审批' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="detail.open" class="modal-mask" @click.self="closeDetail">
      <div class="modal">
        <h3>资金记录详情</h3>
        <div v-if="detail.loading" class="modal-tip">明细加载中…</div>
        <table v-else-if="detail.entry" class="detail-table">
          <tbody>
            <tr v-for="column in columns" :key="column">
              <th>{{ column }}</th>
              <td :class="{ 'money-negative': isMoneyColumn(column) && isNegative(detail.entry[column]) }">
                {{ formatCell(column, detail.entry[column]) }}
              </td>
            </tr>
            <tr>
              <th>审批锁定</th>
              <td>{{ detail.entry.approved ? '已审批，金额已固化（重复提交不再生效）' : '尚未审批' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="closeDetail">返回列表</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/fund'
const columns = ['资金编号', '费用类别', '项目名称', '批复金额', '已用金额', '剩余额度', '审批人员', '资金状态']
const moneyColumns = ['批复金额', '已用金额', '剩余额度']
const statuses = ['待审批', '已批复', '执行中', '已超支']

const rows = ref<Row[]>([])
const total = ref(0)
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const filterFields = columns.slice(0, 3)
const feedback = reactive<{ ok: boolean; message: string }>({ ok: true, message: '' })

const stats = ref([
  { label: '批复总额', value: 0, display: '¥0.00', warn: false },
  { label: '已用金额', value: 0, display: '¥0.00', warn: false },
  { label: '剩余额度合计', value: 0, display: '¥0.00', warn: false },
  { label: '超支项目', value: 0, display: '0', warn: true },
])

const approve = reactive({
  open: false,
  submitting: false,
  row: null as Row | null,
  approved: '',
  used: '',
  approver: '',
})

const detail = reactive({
  open: false,
  loading: false,
  entry: null as Row | null,
})

function isMoneyColumn(column: string) {
  return moneyColumns.includes(column)
}

function isNegative(value: unknown) {
  return typeof value === 'number' ? value < 0 : typeof value === 'string' && value.trim().startsWith('-')
}

function formatMoney(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  const num = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(num)) return String(value)
  return `¥${num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatCell(column: string, value: unknown) {
  if (isMoneyColumn(column)) return formatMoney(value)
  return value ?? '—'
}

function isOverspent(row: Row) {
  return row['资金状态'] === '已超支' || row.abnormal === true || isNegative(row['剩余额度'])
}

function availableActions(row: Row) {
  // 已固化批复的资金不再露出「提交审批」，从入口处避免重复审批。
  const list = row.approved === true ? [] : ['提交审批']
  if (row['资金状态'] === '已批复') list.push('确认批复')
  if (row['资金状态'] === '执行中') list.push('标记超支')
  return list
}

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  feedback.ok = false
  feedback.message = '资金记录登记入口尚未接入审批流'
}

function openApprove(row: Row) {
  approve.row = row
  // 已登记但未审批的记录可能已预填批复金额，带出来便于核对。
  approve.approved = typeof row['批复金额'] === 'number' ? String(row['批复金额']) : ''
  approve.used = typeof row['已用金额'] === 'number' ? String(row['已用金额']) : ''
  approve.approver = typeof row['审批人员'] === 'string' ? row['审批人员'] : ''
  approve.open = true
  approve.submitting = false
}

function closeApprove() {
  approve.open = false
  approve.row = null
}

async function openDetail(row: Row) {
  detail.open = true
  detail.loading = true
  detail.entry = null
  // 每次都从服务端取明细：页面重开、再进一次看到的额度都以存储为准。
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('资金明细读取失败')
    detail.entry = (await response.json()) as Row
  } catch (error) {
    feedback.ok = false
    feedback.message = error instanceof Error ? error.message : '资金明细读取失败'
  } finally {
    detail.loading = false
  }
}

function closeDetail() {
  detail.open = false
  detail.entry = null
  // 从详情返回列表时重新拉取，保证剩余额度与列表合计仍然对得上。
  void reload()
}

function runAction(action: string, row: Row) {
  if (action === '提交审批') {
    openApprove(row)
    return
  }
  void postAction(row, action, { action })
}

async function submitApprove() {
  if (!approve.row || approve.submitting) return
  const values: Record<string, string> = { action: '提交审批' }
  if (approve.approved.trim()) values['批复金额'] = approve.approved.trim()
  if (approve.used.trim()) values['已用金额'] = approve.used.trim()
  if (approve.approver.trim()) values['审批人员'] = approve.approver.trim()
  approve.submitting = true
  const ok = await postAction(approve.row, '提交审批', values)
  approve.submitting = false
  if (ok) closeApprove()
}

async function postAction(row: Row, action: string, values: Record<string, unknown>): Promise<boolean> {
  feedback.message = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload) {
      throw new Error('养护资金动作未生效，请稍后重试')
    }
    feedback.ok = payload.ok !== false
    feedback.message = payload.message || (payload.ok ? '操作已生效' : '操作未生效')
    await reload()
    return payload.ok !== false
  } catch (error) {
    feedback.ok = false
    feedback.message = error instanceof Error ? error.message : '养护资金操作失败'
    return false
  }
}

function buildQuery() {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) params.set(key, value)
  }
  if (statusFilter.value) params.set('status', statusFilter.value)
  const query = params.toString()
  return query ? `?${query}` : ''
}

async function reload() {
  feedback.message = ''
  const query = buildQuery()
  try {
    const [listResponse, summaryResponse] = await Promise.all([
      request(`${ENDPOINT}${query}`),
      request(`${ENDPOINT}/summary${query}`),
    ])
    if (!listResponse.ok) throw new Error('资金记录列表读取失败')
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length

    if (summaryResponse.ok) {
      const summary = await summaryResponse.json()
      stats.value[0].value = Number(summary['批复总额'] ?? 0)
      stats.value[0].display = formatMoney(summary['批复总额'])
      stats.value[1].value = Number(summary['已用金额合计'] ?? 0)
      stats.value[1].display = formatMoney(summary['已用金额合计'])
      stats.value[2].value = Number(summary['剩余额度合计'] ?? 0)
      stats.value[2].display = formatMoney(summary['剩余额度合计'])
      stats.value[3].value = Number(summary['超支项目'] ?? 0)
      stats.value[3].display = String(stats.value[3].value)
    }
  } catch (error) {
    feedback.ok = false
    feedback.message = error instanceof Error ? error.message : '养护资金列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.row-overspent {
  background: #fef3f2;
}
.row-overspent td {
  border-color: #f2c4bf;
}
.money-negative,
.link-danger {
  color: #b42318;
  font-weight: 600;
}
.stat-warn .stat-value {
  color: #b42318;
}
.success-text {
  color: #067647;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 420px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  box-shadow: 0 12px 32px rgba(16, 24, 40, 0.2);
}
.modal h3 {
  margin: 0 0 8px;
  font-size: 16px;
}
.modal-tip {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--muted);
}
.modal-field {
  display: block;
  margin-bottom: 10px;
}
.modal-field span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
.modal-field em {
  color: #b42318;
  font-style: normal;
}
.modal-field input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.detail-table {
  width: 100%;
}
.detail-table th {
  width: 110px;
  color: var(--muted);
  font-weight: normal;
  white-space: nowrap;
}
.detail-table th,
.detail-table td {
  border: 1px solid var(--border);
  padding: 7px 9px;
  font-size: 13px;
  text-align: left;
}
</style>
