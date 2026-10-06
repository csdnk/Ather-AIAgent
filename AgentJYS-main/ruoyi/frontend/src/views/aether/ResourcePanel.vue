<template>
  <ContentWrap>
    <div class="toolbar">
      <div
        ><h2>{{ title }}</h2
        ><p>{{ description }}</p></div
      >
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-4" />
    <el-alert
      v-if="data.status && data.status !== 'ok'"
      :title="`数据状态：${data.status} ${data.code || ''}`"
      type="warning"
      :closable="false"
      show-icon
      class="mb-4"
    />
    <el-alert
      v-if="data.scope_note || data.metering_scope"
      :title="data.scope_note || data.metering_scope"
      type="info"
      :closable="false"
      class="mb-4"
    />
    <div class="metadata"
      ><span>采集时间 {{ displayValue(data.observed_at) }}</span
      ><span>记录 {{ data.total ?? '未知' }}</span
      ><span v-if="data.window">统计窗口 {{ data.window }}</span></div
    >
    <div class="toolbar actions">
      <el-input v-model="search" clearable placeholder="筛选当前页记录" style="max-width: 280px" />
      <el-space v-if="canExecute">
        <el-button
          v-if="recordFields.length"
          type="primary"
          :disabled="blocked"
          @click="editRecord()"
          >新增记录</el-button
        >
        <el-button
          v-if="resource === 'backups'"
          type="primary"
          :disabled="blocked"
          @click="openAction('create')"
          >创建备份</el-button
        >
        <el-button
          v-if="resource === 'configuration'"
          :disabled="blocked"
          @click="openAction('activate')"
          >激活 P3 配置快照</el-button
        >
      </el-space>
    </div>
    <el-alert v-if="pending" type="warning" :closable="false" class="mb-4">
      <template #title>命令 {{ pending.command_id }} · {{ pending.state }}</template>
      <div>{{ pending.message || '命令受理与执行完成分开记录，请查询原命令结果。' }}</div>
      <el-button size="small" @click="queryPending">查询原命令</el-button>
      <el-button v-if="!blocked" size="small" @click="clearPending">关闭记录</el-button>
    </el-alert>
    <el-table
      v-loading="loading"
      :data="visibleRows"
      stripe
      border
      empty-text="当前查询没有记录"
      @row-dblclick="showDetail"
    >
      <el-table-column
        v-for="key in columns"
        :key="key"
        :prop="key"
        :label="labels[key] || key"
        min-width="150"
        show-overflow-tooltip
      >
        <template #default="scope">{{ displayValue(scope.row[key]) }}</template>
      </el-table-column>
      <el-table-column label="操作" fixed="right" :width="resource === 'tasks' ? 250 : 180">
        <template #default="scope"
          ><el-button link type="primary" @click="showDetail(scope.row)">详情</el-button>
          <template v-if="canExecute">
            <el-button
              v-if="recordFields.length"
              link
              type="primary"
              :disabled="blocked"
              @click="editRecord(scope.row)"
              >编辑</el-button
            >
            <el-button
              v-if="resource === 'tasks'"
              link
              type="warning"
              :disabled="blocked"
              @click="openAction('cancel', scope.row)"
              >取消</el-button
            >
            <el-button
              v-if="resource === 'tasks'"
              link
              type="primary"
              :disabled="blocked"
              @click="openAction('reconcile', scope.row)"
              >协调</el-button
            >
            <el-button
              v-if="resource === 'incidents'"
              link
              type="primary"
              :disabled="blocked"
              @click="openAction('acknowledge', scope.row)"
              >认领</el-button
            >
            <el-button
              v-if="resource === 'incidents'"
              link
              type="warning"
              :disabled="blocked"
              @click="openAction('silence', scope.row)"
              >静默</el-button
            >
            <el-button
              v-if="resource === 'backups'"
              link
              type="primary"
              :disabled="blocked"
              @click="openAction('restore_drill', scope.row)"
              >恢复演练</el-button
            >
          </template>
        </template>
      </el-table-column>
    </el-table>
    <el-space v-if="resource === 'tasks'" class="mt-4"
      ><el-button :disabled="page <= 1" @click="previousTasks">上一页</el-button
      ><span>第 {{ page }} 页</span
      ><el-button :disabled="!data.next_cursor" @click="nextTasks">下一页</el-button></el-space
    >
    <el-pagination
      v-else
      class="mt-4"
      layout="prev, pager, next, total"
      :total="data.total || 0"
      :page-size="50"
      v-model:current-page="page"
      @current-change="load"
    />
    <template v-if="resource === 'incidents' && data.deliveries"
      ><h3>通知投递</h3
      ><el-table :data="data.deliveries" border
        ><el-table-column
          v-for="key in ['alert_id', 'channel', 'state', 'code', 'created_at']"
          :key="key"
          :prop="key"
          :label="labels[key] || key"
          min-width="140"
          show-overflow-tooltip /></el-table
    ></template>
    <template v-if="resource === 'usage' && data.model_usage"
      ><h3>模型计量</h3><p>{{ data.metering_status }}</p
      ><el-table :data="data.model_usage" border
        ><el-table-column
          v-for="key in [
            'model_name',
            'measured_attempts',
            'prompt_tokens',
            'completion_tokens',
            'total_tokens',
            'estimated_cost',
            'currency'
          ]"
          :key="key"
          :prop="key"
          :label="labels[key] || key"
          min-width="140"
          ><template #default="scope">{{ displayValue(scope.row[key]) }}</template></el-table-column
        ></el-table
      ></template
    >
    <el-dialog
      v-model="dialog"
      :title="action === 'save' ? '维护记录' : actionNames[action]"
      width="660px"
    >
      <el-alert
        v-if="resource === 'backups'"
        title="范围为平台 PostgreSQL；恢复演练使用隔离数据库。其他存储需独立备份。"
        type="info"
        :closable="false"
        class="mb-4"
      />
      <el-form label-position="top">
        <el-form-item label="记录 / 任务编号"
          ><el-input v-model="form.target_id" :disabled="editing"
        /></el-form-item>
        <el-form-item v-if="action === 'save' && editing" label="当前版本"
          ><el-input :model-value="String(form.expected_version)" disabled
        /></el-form-item>
        <template v-if="action === 'save'"
          ><el-form-item v-for="field in recordFields" :key="field" :label="labels[field] || field">
            <el-switch v-if="field === 'enabled'" v-model="form.parameters[field]" />
            <el-input-number
              v-else-if="
                [
                  'threshold',
                  'daily_requests',
                  'concurrent_turns',
                  'observed_token_budget'
                ].includes(field)
              "
              v-model="form.parameters[field]"
              :min="0"
              :max="field === 'threshold' ? 86400 : 1000000000"
            />
            <el-select v-else-if="field === 'metric'" v-model="form.parameters[field]"
              ><el-option label="15 分钟失败请求数" value="request_failures_15m" /><el-option
                label="最早积压时长（秒）"
                value="oldest_pending_seconds"
            /></el-select>
            <el-input
              v-else
              v-model="form.parameters[field]"
              :type="['description', 'resolution', 'purpose'].includes(field) ? 'textarea' : 'text'"
            /> </el-form-item
        ></template>
        <template v-if="['cancel', 'reconcile'].includes(action)"
          ><el-form-item label="当前任务版本"
            ><el-input-number v-model="form.expected_version" :min="1" /></el-form-item
          ><el-form-item label="操作原因"
            ><el-input v-model="form.parameters.reason" type="textarea" /></el-form-item
        ></template>
        <el-form-item v-if="['acknowledge', 'silence'].includes(action)" label="处置说明"
          ><el-input v-model="form.parameters.note" type="textarea"
        /></el-form-item>
        <el-form-item v-if="action === 'silence'" label="静默分钟数"
          ><el-input-number v-model="form.parameters.minutes" :min="1" :max="1440"
        /></el-form-item>
        <template v-if="action === 'activate'"
          ><el-form-item label="当前 P3 配置版本"
            ><el-input v-model="form.expected_version" /></el-form-item
          ><el-form-item label="新配置快照（JSON）"
            ><el-input v-model="snapshot" type="textarea" :rows="8" /></el-form-item
        ></template>
      </el-form>
      <template #footer
        ><el-button @click="dialog = false">取消</el-button
        ><el-button type="primary" :loading="submitting" :disabled="blocked" @click="submit"
          >提交一次</el-button
        ></template
      >
    </el-dialog>
    <el-drawer v-model="detailOpen" title="记录详情" size="55%"
      ><el-descriptions :column="1" border
        ><el-descriptions-item
          v-for="(value, key) in detail"
          :key="key"
          :label="labels[key] || String(key)"
        >
          <pre>{{
            typeof value === 'object' && value !== null
              ? JSON.stringify(value, null, 2)
              : displayValue(value)
          }}</pre>
        </el-descriptions-item></el-descriptions
      ></el-drawer
    >
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { getIdentity, getOperations, sendCommand } from '@/api/aether'
import {
  commandLookupParams,
  createCommand,
  displayValue,
  normalizeRows,
  maySubmit
} from './operations.mjs'
const props = defineProps<{ resource: string; title: string; description: string }>()
const data = ref<any>({}),
  loading = ref(false),
  error = ref(''),
  search = ref(''),
  page = ref(1)
const identity = ref<any>({ permissions: [] }),
  dialog = ref(false),
  submitting = ref(false),
  editing = ref(false),
  action = ref('save'),
  snapshot = ref('')
const form = reactive<any>({ target_id: '', expected_version: null, parameters: {} })
const detailOpen = ref(false),
  detail = ref<any>({}),
  pending = ref<any>(null)
const cursors = ref<(string | undefined)[]>([undefined])
const storageKey = computed(() => `aether-command:${identity.value.user_id}:${props.resource}`)
const blocked = computed(() => !maySubmit(pending.value))
const canExecute = computed(() =>
  identity.value.permissions.includes(`aether:${props.resource}:execute`)
)
const fields: Record<string, string[]> = {
  quotas: ['daily_requests', 'concurrent_turns', 'observed_token_budget'],
  resources: ['name', 'kind', 'environment', 'owner', 'purpose', 'dependency_ids', 'status'],
  support: ['summary', 'request_id', 'severity', 'state', 'owner', 'resolution'],
  configuration: ['name', 'value', 'description'],
  rules: ['metric', 'threshold', 'enabled', 'description']
}
const recordFields = computed(() => fields[props.resource] || [])
const labels: Record<string, string> = {
  daily_requests: '滚动 24 小时请求数',
  concurrent_turns: '并发请求数',
  observed_token_budget: '已观测 Token 预算',
  id: '编号',
  task_id: '任务编号',
  job_id: '作业编号',
  request_id: '请求编号',
  command_id: '命令编号',
  status: '状态',
  state: '状态',
  phase: '阶段',
  created_at: '创建时间',
  updated_at: '更新时间',
  name: '名称',
  kind: '类型',
  environment: '环境',
  owner: '负责人',
  purpose: '用途',
  dependency_ids: '依赖编号（逗号分隔）',
  version: '版本',
  revision: '版本',
  summary: '摘要',
  severity: '级别',
  resolution: '处理结果',
  description: '说明',
  value: '值',
  metric: '指标',
  threshold: '阈值',
  enabled: '启用',
  action: '动作',
  resource: '资源',
  actor_id: '操作主体',
  tenant_id: '租户',
  user_id: '用户',
  target_id: '目标编号',
  attempt: '尝试次数',
  first_token_ms: '首字延迟（毫秒）',
  requests: '请求数',
  complete: '完成',
  failed: '失败',
  pending: '积压',
  saved: '记忆保存',
  first_token_p95_ms: '首字 P95（毫秒）',
  model_name: '模型',
  measured_attempts: '已计量尝试（含重试）',
  prompt_tokens: '输入 Token',
  completion_tokens: '输出 Token',
  total_tokens: '总 Token',
  estimated_cost: '估算费用',
  currency: '币种',
  alert_id: '告警编号',
  channel: '渠道',
  code: '结果码',
  memory_evidence: '记忆回执',
  last_seen: '最近触发',
  result: '执行结果'
}
const actionNames: Record<string, string> = {
  save: '维护记录',
  cancel: '取消任务',
  reconcile: '协调任务',
  acknowledge: '认领告警',
  silence: '维护静默',
  create: '创建备份',
  restore_drill: '隔离恢复演练',
  activate: '激活 P3 配置快照'
}
const rows = computed(() => normalizeRows(data.value))
const columns = computed(
  () =>
    Array.from(new Set(rows.value.flatMap((row: any) => Object.keys(row))))
      .filter((key: any) => !['fingerprint', 'payload', 'result', 'memory_evidence'].includes(key))
      .slice(0, 12) as string[]
)
const visibleRows = computed(() =>
  rows.value.filter(
    (row: any) =>
      !search.value ||
      Object.values(row).some((value) =>
        displayValue(value).toLowerCase().includes(search.value.toLowerCase())
      )
  )
)
function persist() {
  if (pending.value) sessionStorage.setItem(storageKey.value, JSON.stringify(pending.value))
}
function clearPending() {
  pending.value = null
  sessionStorage.removeItem(storageKey.value)
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await getOperations(
      props.resource,
      props.resource === 'tasks'
        ? {
            limit: 50,
            ...(cursors.value[page.value - 1] ? { cursor: cursors.value[page.value - 1] } : {})
          }
        : { limit: 50, offset: (page.value - 1) * 50 }
    )
  } catch (e: any) {
    error.value = e?.message || String(e)
    data.value = { status: 'unavailable' }
  } finally {
    loading.value = false
  }
}
function nextTasks() {
  cursors.value[page.value] = data.value.next_cursor
  page.value++
  load()
}
function previousTasks() {
  if (page.value > 1) {
    page.value--
    load()
  }
}
function showDetail(row: any) {
  detail.value = row
  detailOpen.value = true
}
function editRecord(row?: any) {
  action.value = 'save'
  editing.value = !!row
  form.target_id = row?.id || ''
  form.expected_version = row?.version ?? null
  form.parameters = {}
  for (const key of recordFields.value) {
    const value = row?.[key]
    form.parameters[key] =
      key === 'enabled'
        ? (value ?? true)
        : ['threshold', 'daily_requests', 'concurrent_turns', 'observed_token_budget'].includes(key)
          ? (value ?? 1)
          : Array.isArray(value)
            ? value.join(',')
            : (value ?? '')
  }
  dialog.value = true
}
function openAction(value: string, row?: any) {
  action.value = value
  editing.value = !!row
  form.target_id = row?.task_id || row?.id || ''
  form.expected_version = row?.revision ?? row?.version ?? null
  form.parameters = value === 'silence' ? { note: '', minutes: 30 } : {}
  snapshot.value = ''
  dialog.value = true
}
async function submit() {
  if (blocked.value || !form.target_id.trim()) return
  submitting.value = true
  error.value = ''
  try {
    const parameters = { ...form.parameters }
    if (action.value === 'activate') parameters.snapshot = JSON.parse(snapshot.value)
    if (parameters.dependency_ids !== undefined)
      parameters.dependency_ids = String(parameters.dependency_ids)
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean)
    const command = createCommand(
      props.resource,
      action.value,
      form.target_id,
      form.expected_version,
      parameters
    )
    pending.value = { ...command, state: 'submitting' }
    persist()
    try {
      const result = await sendCommand(command)
      pending.value = {
        ...pending.value,
        state: result.status || 'accepted',
        message: JSON.stringify(result.result || result)
      }
      dialog.value = false
      await load()
    } catch (e: any) {
      pending.value = { ...pending.value, state: 'unknown', message: e?.message || String(e) }
      dialog.value = false
    }
    persist()
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    submitting.value = false
  }
}
async function queryPending() {
  try {
    const result = await getOperations('commands', commandLookupParams(pending.value.command_id))
    const row = result.items?.find((r: any) => r.id === pending.value.command_id)
    if (row) {
      pending.value = {
        ...pending.value,
        state: row.status,
        message: JSON.stringify(row.result || {})
      }
      persist()
    } else error.value = '原命令尚未出现在查询结果中，状态仍未知。'
  } catch (e: any) {
    error.value = e?.message || String(e)
  }
}
onMounted(async () => {
  try {
    identity.value = await getIdentity()
    const stored = sessionStorage.getItem(storageKey.value)
    if (stored) pending.value = JSON.parse(stored)
  } catch (e: any) {
    error.value = e?.message || String(e)
  }
  await load()
})
</script>
<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.toolbar h2 {
  margin: 0;
  font-size: 22px;
}
.toolbar p,
.metadata {
  color: var(--el-text-color-secondary);
}
.metadata {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  margin: 16px 0;
}
.actions {
  margin-bottom: 16px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
  font: inherit;
}
</style>
