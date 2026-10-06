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
      v-if="dataNotice(data)"
      :title="dataNotice(data)"
      type="warning"
      :closable="false"
      show-icon
      class="mb-4"
    />
    <el-alert
      v-if="data.scope_note || data.metering_scope"
      :title="scopeDescription"
      type="info"
      :closable="false"
      class="mb-4"
    />
    <div class="metadata"
      ><span>最近更新 {{ formatTime(data.observed_at) }}</span
      ><span>记录 {{ data.total ?? '未知' }}</span
      ><span v-if="resource === 'configuration'"
        >当前记忆服务配置版本
        {{
          data.current_status === 'available'
            ? (data.active_snapshot?.version ?? '尚未激活')
            : '无法读取当前配置'
        }}</span
      ><span v-if="data.window">统计窗口 最近 24 小时</span></div
    >
    <div class="toolbar actions">
      <el-input
        v-model="search"
        clearable
        placeholder="搜索本页名称、状态、说明或编号"
        style="max-width: 280px"
      />
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
          :disabled="blocked || data.current_status !== 'available'"
          @click="openAction('activate')"
          >启用记忆服务配置快照</el-button
        >
      </el-space>
    </div>
    <el-alert v-if="pending" type="warning" :closable="false" class="mb-4">
      <template #title>{{ statusText(pending.state) }}</template>
      <div>{{ explainRow('commands', { ...pending, status: pending.state }) }}</div>
      <el-button size="small" @click="queryPending">查询执行结果</el-button>
      <el-button v-if="!blocked" size="small" @click="clearPending">收起结果</el-button>
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
        v-for="column in columns"
        :key="column.key"
        :prop="column.key"
        :label="column.label"
        :min-width="column.width"
      >
        <template #default="scope"
          ><span class="cell-text">{{ cellText(resource, column.key, scope.row) }}</span>
          <div
            v-if="resource === 'tasks' && column.key === 'kind' && taskAudience(scope.row)"
            class="row-time"
            >{{ taskAudience(scope.row) }}</div
          >
          <div
            v-if="
              column.key === 'explanation' && ['requests', 'tasks', 'memories'].includes(resource)
            "
            class="row-time"
            >{{ formatTime(scope.row.created_at) }}</div
          ></template
        >
      </el-table-column>
      <el-table-column
        label="操作"
        fixed="right"
        :width="
          resource === 'tasks'
            ? 190
            : canExecute && (recordFields.length || ['incidents', 'backups'].includes(resource))
              ? 150
              : 80
        "
      >
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
              >核对状态</el-button
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
              >暂停提醒</el-button
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
          v-for="key in ['channel', 'state', 'code', 'created_at']"
          :key="key"
          :prop="key"
          :label="labels[key] || key"
          min-width="140"
          ><template #default="scope">{{
            cellText(resource, key, scope.row)
          }}</template></el-table-column
        ></el-table
      ></template
    >
    <template v-if="resource === 'usage' && data.model_usage"
      ><h3>模型调用用量</h3><p>{{ statusText(data.metering_status) }}</p
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
          ><template #default="scope">{{
            cellText(resource, key, scope.row)
          }}</template></el-table-column
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
        <el-form-item v-if="resource === 'quotas'" label="企业 / 团队">
          <el-select
            v-model="form.target_id"
            :disabled="editing"
            placeholder="请选择需要配置用量上限的企业"
            class="w-full"
          >
            <el-option
              v-for="tenant in data.tenant_choices || []"
              :key="tenant.id"
              :label="tenant.name"
              :value="tenant.id"
            />
          </el-select>
        </el-form-item>
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
            <el-select
              v-else-if="['state', 'status', 'severity'].includes(field)"
              v-model="form.parameters[field]"
            >
              <el-option
                v-for="option in formOptions[field]"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-input
              v-else
              v-model="form.parameters[field]"
              :type="['description', 'resolution', 'purpose'].includes(field) ? 'textarea' : 'text'"
            /> </el-form-item
        ></template>
        <template v-if="['cancel', 'reconcile'].includes(action)"
          ><el-form-item label="当前任务版本"
            ><el-input-number v-model="form.expected_version" :min="1" disabled /></el-form-item
          ><el-form-item label="操作原因"
            ><el-input v-model="form.parameters.reason" type="textarea" /></el-form-item
        ></template>
        <el-form-item v-if="['acknowledge', 'silence'].includes(action)" label="处置说明"
          ><el-input v-model="form.parameters.note" type="textarea"
        /></el-form-item>
        <el-form-item v-if="action === 'silence'" label="暂停提醒时长（分钟）"
          ><el-input-number v-model="form.parameters.minutes" :min="1" :max="1440"
        /></el-form-item>
        <template v-if="action === 'activate'"
          ><el-form-item label="当前记忆服务配置版本"
            ><el-input
              :model-value="form.expected_version ?? ''"
              placeholder="尚未激活时留空"
              @update:model-value="
                form.expected_version = $event === '' ? null : $event
              " /></el-form-item
          ><el-form-item label="新配置快照（JSON）"
            ><el-input v-model="snapshot" type="textarea" :rows="8" /></el-form-item
        ></template>
      </el-form>
      <template #footer
        ><el-button @click="dialog = false">取消</el-button
        ><el-button type="primary" :loading="submitting" :disabled="blocked" @click="submit"
          >确认提交</el-button
        ></template
      >
    </el-dialog>
    <el-drawer v-model="detailOpen" title="处理详情" size="60%">
      <template v-if="resource === 'tasks'">
        <p v-if="taskAudience(taskDetail.task)">影响范围：{{ taskAudience(taskDetail.task) }}</p>
        <el-alert :title="taskOutcome(taskDetail)" type="info" :closable="false" />
        <el-alert v-if="taskDetailError" :title="taskDetailError" type="error" :closable="false" />
        <el-button :loading="taskDetailLoading" class="mt-4" @click="loadTaskDetail"
          >刷新执行进度</el-button
        >
        <EvidencePanel title="当前执行步骤" :data="taskDetail.progress" />
        <EvidencePanel title="Temporal 工作流" :data="taskDetail.workflow" />
      </template>
      <el-alert
        :title="explainRow(resource, detail)"
        type="info"
        :closable="false"
        show-icon
        class="mb-4"
      />
      <el-descriptions :column="1" border>
        <el-descriptions-item v-for="column in columns" :key="column.key" :label="column.label">
          {{ cellText(resource, column.key, detail) }}
        </el-descriptions-item>
      </el-descriptions>
      <el-collapse class="mt-4">
        <el-collapse-item title="技术详情（编号、错误码与原始回执，供排障使用）" name="technical">
          <pre>{{ JSON.stringify(detail, null, 2) }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-drawer>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  cellText,
  columnsFor,
  dataNotice,
  explainRow,
  formatTime,
  statusText
} from './presentation.mjs'
import { getIdentity, getOperations, getConsole, sendCommand } from '@/api/aether'
import EvidencePanel from './EvidencePanel.vue'
import { errorMessage, taskOutcome, taskAudience, scopeText } from './console.mjs'
import {
  commandLookupParams,
  createCommand,
  displayValue,
  normalizeRows,
  maySubmit
} from './operations.mjs'
const props = defineProps<{
  resource: string
  title: string
  description: string
  taskSource?: any
  taskLoading?: boolean
  taskPage?: number
}>()
const emit = defineEmits(['refresh', 'previous', 'next'])
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
watch(
  () => props.taskSource,
  (value) => {
    if (value !== undefined) data.value = value
  },
  { immediate: true }
)
watch(
  () => props.taskLoading,
  (value) => {
    if (props.taskSource !== undefined) loading.value = !!value
  },
  { immediate: true }
)
watch(
  () => props.taskPage,
  (value) => {
    if (value) page.value = value
  },
  { immediate: true }
)
const taskDetail = ref<any>({}),
  taskDetailError = ref(''),
  taskDetailLoading = ref(false)
let taskDetailRequest = 0
const storageKey = computed(() => `aether-command:${identity.value.user_id}:${props.resource}`)
const blocked = computed(() => !maySubmit(pending.value))
const canExecute = computed(() =>
  identity.value.permissions.includes(`aether:${props.resource}:execute`)
)
const formOptions: Record<string, { label: string; value: string }[]> = {
  state: [
    { label: '待处理', value: 'open' },
    { label: '处理中', value: 'in_progress' },
    { label: '已解决', value: 'resolved' }
  ],
  status: [
    { label: '运行中（登记）', value: 'running' },
    { label: '已停止（登记）', value: 'stopped' },
    { label: '维护中（登记）', value: 'maintenance' }
  ],
  severity: [
    { label: '低', value: 'low' },
    { label: '中', value: 'medium' },
    { label: '高', value: 'high' },
    { label: '紧急', value: 'critical' }
  ]
}
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
  prompt_tokens: '输入文本用量',
  completion_tokens: '输出文本用量',
  total_tokens: '文本总用量',
  estimated_cost: '估算费用',
  currency: '币种',
  alert_id: '告警编号',
  channel: '通知类型',
  code: '结果码',
  memory_evidence: '记忆回执',
  last_seen: '最近触发',
  result: '执行结果'
}
const actionNames: Record<string, string> = {
  save: '维护记录',
  cancel: '取消任务',
  reconcile: '核对任务状态',
  acknowledge: '认领告警',
  silence: '暂时停止告警提醒',
  create: '创建备份',
  restore_drill: '隔离恢复演练',
  activate: '启用记忆服务配置快照'
}
const rows = computed(() => normalizeRows(data.value))
const columns = computed(() => columnsFor(props.resource))
const scopeDescription = computed(() =>
  props.resource === 'usage'
    ? '统计最近 24 小时内模型服务已返回的用量，包含重试；尚未计量的请求和记忆服务内部调用未计入。费用未配置时显示未知。'
    : scopeText(data.value.scope_note)
)
const visibleRows = computed(() =>
  rows.value.filter(
    (row: any) =>
      !search.value ||
      [
        ...columns.value.map((column: any) => cellText(props.resource, column.key, row)),
        JSON.stringify(row)
      ].some((value) => value.toLowerCase().includes(search.value.toLowerCase()))
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
  if (props.taskSource !== undefined) {
    emit('refresh')
    return
  }
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
    error.value = '操作暂时无法完成，请检查登录状态和服务连接后重试。'
    data.value = { status: 'unavailable' }
  } finally {
    loading.value = false
  }
}
function nextTasks() {
  if (props.taskSource !== undefined) {
    emit('next')
    return
  }
  cursors.value[page.value] = data.value.next_cursor
  page.value++
  load()
}
function previousTasks() {
  if (props.taskSource !== undefined) {
    emit('previous')
    return
  }
  if (page.value > 1) {
    page.value--
    load()
  }
}
function showDetail(row: any) {
  detail.value = row
  detailOpen.value = true
  if (props.resource === 'tasks') loadTaskDetail()
}
async function loadTaskDetail() {
  const request = ++taskDetailRequest
  taskDetail.value = {}
  taskDetailError.value = ''
  taskDetailLoading.value = true
  try {
    const response = await getConsole(
      `tasks/${encodeURIComponent(detail.value.task_id || detail.value.id)}`
    )
    if (request === taskDetailRequest) taskDetail.value = response
  } catch (e) {
    if (request === taskDetailRequest) taskDetailError.value = errorMessage(e)
  } finally {
    if (request === taskDetailRequest) taskDetailLoading.value = false
  }
}
function editRecord(row?: any) {
  action.value = 'save'
  editing.value = !!row
  form.target_id = row?.id || (props.resource === 'quotas' ? '' : crypto.randomUUID())
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
  if (value === 'activate' && data.value.current_status !== 'available') return
  action.value = value
  editing.value = !!row
  form.target_id = row?.task_id || row?.id || crypto.randomUUID()
  form.expected_version =
    value === 'activate'
      ? (data.value.active_snapshot?.version ?? null)
      : (row?.revision ?? row?.version ?? null)
  form.parameters = value === 'silence' ? { note: '', minutes: 30 } : {}
  snapshot.value = ''
  dialog.value = true
}
async function submit() {
  if (blocked.value || !form.target_id.trim()) return
  if (
    ['cancel', 'reconcile'].includes(action.value) &&
    (!form.expected_version || !form.parameters.reason?.trim())
  ) {
    error.value = '请填写操作原因；未取得当前任务版本时，请刷新任务后再操作。'
    return
  }
  if (action.value === 'activate' && data.value.current_status !== 'available') return
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
    error.value = '操作暂时无法完成，请检查登录状态和服务连接后重试。'
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
    error.value = '操作暂时无法完成，请检查登录状态和服务连接后重试。'
  }
}
onMounted(async () => {
  try {
    identity.value = await getIdentity()
    const stored = sessionStorage.getItem(storageKey.value)
    if (stored) pending.value = JSON.parse(stored)
  } catch (e: any) {
    error.value = '操作暂时无法完成，请检查登录状态和服务连接后重试。'
  }
  if (props.taskSource === undefined) await load()
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
.row-time {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 4px;
}
.cell-text {
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
  font: inherit;
}
</style>
