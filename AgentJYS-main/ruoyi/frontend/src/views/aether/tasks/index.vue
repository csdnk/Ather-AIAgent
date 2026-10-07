<template>
  <ContentWrap>
    <el-result
      v-if="denied"
      icon="info"
      title="此页面仅供平台管理员查看"
      sub-title="任务与调度包含整个平台的工作流、执行器和队列。当前企业账号可在“用户会话”查看本企业用户的请求与记忆保存情况。"
    >
      <template #extra
        ><el-button type="primary" @click="$router.push('/aether/business')"
          >查看本企业用户会话</el-button
        ></template
      >
    </el-result>
    <template v-else>
      <div class="heading"
        ><div><h1>任务与调度</h1></div
        ><el-button :loading="loading" @click="load">刷新</el-button></div
      >
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <p class="note">{{ observation(data) }} · 采样 {{ formatTime(data.observed_at) }}</p>
      <div class="status-cards">
        <button
          v-for="item in statusCards"
          :key="item.state"
          type="button"
          class="status-card"
          :class="{ selected: stateFilter === item.state }"
          :aria-pressed="stateFilter === item.state"
          :disabled="loading || !data.task_summary"
          @click="selectState(item.state)"
        >
          <span>{{ item.label }}</span>
          <strong>{{
            data.task_summary?.by_state?.[item.state] ?? (data.task_summary ? 0 : '—')
          }}</strong>
          <span>查看任务 →</span>
        </button>
      </div>
      <div class="charts">
        <el-card shadow="never"
          ><template #header><h2>累计任务状态分布</h2></template
          ><Echart
            v-if="data.task_summary"
            :options="categoryChart(data.task_summary.by_state, statusText, appStore.getIsDark)"
            height="280px"
        /></el-card>
        <el-card shadow="never"
          ><template #header><h2>累计任务构成</h2></template
          ><Echart
            v-if="data.task_summary"
            :options="categoryChart(data.task_summary.by_kind, taskKindLabel, appStore.getIsDark)"
            height="280px"
        /></el-card>
      </div>
      <el-tabs v-model="activeTab">
        <el-tab-pane label="业务任务" name="tasks" lazy>
          <el-form inline class="mt-4"
            ><el-form-item label="业务类别"
              ><el-select
                v-model="flowFilter"
                clearable
                placeholder="全部业务"
                style="width: 180px"
                @change="filterTasks"
                ><el-option label="记忆写入与整理" value="remember" /><el-option
                  label="记忆召回"
                  value="recall" /><el-option label="存储维护" value="operate" /><el-option
                  label="运行维护"
                  value="runtime" /></el-select></el-form-item
            ><el-form-item label="任务状态"
              ><el-select
                v-model="stateFilter"
                clearable
                placeholder="全部状态"
                style="width: 170px"
                @change="filterTasks"
                ><el-option
                  v-for="state in taskStates"
                  :key="state"
                  :label="statusText(state)"
                  :value="state" /></el-select></el-form-item
          ></el-form>
          <div class="task-actions"
            ><el-button @click="$router.push('/aether/faults?tab=task_cases')"
              >查看处置工单</el-button
            ></div
          >
          <ResourcePanel
            resource="tasks"
            title="业务任务与处理"
            description="点击详情查看执行步骤与对应工作流。申请取消或核对结果需要当前版本和操作原因；结果未知时继续查询原操作。"
            :task-source="{ ...data.tasks, status: data.status, observed_at: data.observed_at }"
            :task-loading="loading"
            :task-page="page"
            @refresh="load"
            @previous="previous"
            @next="nextPage"
        /></el-tab-pane>
        <el-tab-pane label="执行器与队列" name="queues" lazy>
          <p class="note"
            >本机运行状态与远端实际轮询者分开显示。队列积压是服务端估计值，无采样时显示未知。</p
          >
          <EvidencePanel title="本机运行环境" :data="data.runtime" />
          <p>{{ observation(data.queue_metrics) }}</p>
          <el-table
            :data="queues"
            :empty-text="error ? '读取失败' : observation(data.queue_metrics)"
          >
            <el-table-column prop="label" label="业务" min-width="185" />
            <el-table-column label="任务流执行器" min-width="180"
              ><template #default="{ row }">{{ row.workflow.label }}</template></el-table-column
            >
            <el-table-column label="业务步骤执行器" min-width="180"
              ><template #default="{ row }">{{ row.activity.label }}</template></el-table-column
            >
            <el-table-column label="待取工作项（估计）" min-width="190"
              ><template #default="{ row }"
                >流程 {{ row.workflow.backlog ?? '未知' }} / 步骤
                {{ row.activity.backlog ?? '未知' }}</template
              ></el-table-column
            >
            <el-table-column label="最近联系" min-width="170"
              ><template #default="{ row }">{{ formatTime(row.recent) }}</template></el-table-column
            >
            <el-table-column label="检查提示" min-width="190"
              ><template #default="{ row }">{{
                row.needsCheck ? '检查积压、最近联系或缺失采样' : '近期有联系，未见积压'
              }}</template></el-table-column
            >
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="周期维护" name="periodic" lazy
          ><p class="note"
            >展示已绑定的周期工作流。下次执行：{{
              data.periodic?.next_run_at
                ? formatTime(data.periodic.next_run_at)
                : '尚无可靠调度依据'
            }}。</p
          ><EvidencePanel title="长期整理与周期维护" :data="data.periodic"
        /></el-tab-pane>
        <el-tab-pane label="持续业务采样" name="sampling" lazy
          ><p class="note"
            >独立于浏览器持续采集的平台对话回执。15
            分钟指标按请求创建时间统计；等待指标覆盖当前未结束请求，不能代表全部 P3 业务。</p
          ><p>{{ observation(data.business) }} · {{ formatTime(data.business?.observed_at) }}</p
          ><el-table :data="sectionItems(data.business)" :empty-text="observation(data.business)"
            ><el-table-column prop="tenant_name" label="企业" min-width="150" /><el-table-column
              label="指标"
              min-width="220"
              ><template #default="{ row }">{{
                businessMetric(row.metric)
              }}</template></el-table-column
            ><el-table-column label="值" min-width="100"
              ><template #default="{ row }">{{ row.value ?? '未知' }}</template></el-table-column
            ><el-table-column label="数据时效" min-width="140"
              ><template #default="{ row }">{{
                row.stale ? '采样已过期' : '本次采样有效'
              }}</template></el-table-column
            ><el-table-column label="观测时间" min-width="180"
              ><template #default="{ row }">{{
                formatTime(row.observed_at)
              }}</template></el-table-column
            ></el-table
          ></el-tab-pane
        >
      </el-tabs>
      <el-drawer v-model="open" title="执行详情" size="min(780px, 92vw)" @closed="clearDetail"
        ><el-skeleton v-if="detailLoading" :rows="5" animated /><el-alert
          v-if="detailError"
          :title="detailError"
          type="error"
          :closable="false" /><template v-else-if="!detailLoading"
          ><TaskHandlingPanel v-if="selected.task" :data="selected" /><p>{{
            taskOutcome(selected)
          }}</p
          ><EvidencePanel title="业务与执行观测" :data="selected.task || selected" /><EvidencePanel
            v-if="selected.workflow"
            title="工作流状态"
            :data="selected.workflow" /><EvidencePanel
            v-if="selected.progress"
            title="执行步骤"
            :data="selected.progress" /><EvidencePanel
            v-if="selected.traces"
            title="关联业务追踪"
            :data="selected.traces" /></template
      ></el-drawer>
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Echart } from '@/components/Echart'
import { useAppStore } from '@/store/modules/app'
import { categoryChart, taskKindLabel } from '../dashboard.mjs'
import { getIdentity, getConsole } from '@/api/aether'
import { readTaskDiagnostics } from '../taskAccess.mjs'
import ResourcePanel from '../ResourcePanel.vue'
import TaskHandlingPanel from '../TaskHandlingPanel.vue'
import { businessQueues, taskStateFilter } from '../triage.mjs'
import EvidencePanel from '../EvidencePanel.vue'
import { errorMessage, observation, sectionItems, taskOutcome } from '../console.mjs'
import { formatTime, statusText } from '../presentation.mjs'
defineOptions({ name: 'AetherTasks' })
const route = useRoute()
const appStore = useAppStore()
const activeTab = ref('tasks')
const statusCards = [
  { state: 'pending', label: '排队等待' },
  { state: 'running', label: '正在执行' },
  { state: 'retry_wait', label: '等待自动重试' },
  { state: 'recovery_wait', label: '等待恢复检查' },
  { state: 'failed', label: '处理失败' },
  { state: 'attention_required', label: '需要人工处理' }
]
function selectState(state: string) {
  stateFilter.value = stateFilter.value === state ? '' : state
  flowFilter.value = ''
  activeTab.value = 'tasks'
  filterTasks()
}
const data = ref<any>({}),
  denied = ref(false),
  error = ref(''),
  loading = ref(false),
  selected = ref<any>({}),
  open = ref(false),
  detailLoading = ref(false),
  detailError = ref(''),
  page = ref(1),
  cursors = ref<any[]>([undefined])
const flowFilter = ref(''),
  stateFilter = ref(taskStateFilter(route.query.state))
const queues = computed(() => businessQueues(data.value.queue_metrics))
const taskStates = [
  'pending',
  'running',
  'retry_wait',
  'recovery_wait',
  'succeeded',
  'failed',
  'cancelled',
  'attention_required'
]
function filterTasks() {
  page.value = 1
  cursors.value = [undefined]
  load()
}
let requestNumber = 0,
  detailNumber = 0
function businessMetric(key: string) {
  return (
    (
      {
        request_failures_15m: '15 分钟内创建的失败请求',
        oldest_pending_seconds: '最久等待（秒）',
        chat_turns_15m: '15 分钟内创建的请求',
        memory_save_failures_15m: '15 分钟内创建请求的保存失败',
        memory_save_pending: '当前等待记忆保存',
        stale_pending_requests: '当前长时间未结束请求'
      } as any
    )[key] || '指标含义待核实'
  )
}
function previous() {
  page.value--
  load()
}
function nextPage() {
  cursors.value[page.value] = data.value.tasks.next_cursor
  page.value++
  load()
}
async function load() {
  const request = ++requestNumber
  loading.value = true
  error.value = ''
  denied.value = false
  data.value = {}
  try {
    const result = await readTaskDiagnostics(getIdentity, getConsole, {
      flow: flowFilter.value || undefined,
      state: stateFilter.value || undefined,
      limit: 30,
      cursor: cursors.value[page.value - 1]
    })
    if (request === requestNumber) data.value = result
  } catch (e: any) {
    if (request === requestNumber) {
      denied.value = Number(e?.code ?? e?.response?.status) === 403
      error.value = errorMessage(e)
      data.value = { status: denied.value ? 'forbidden' : 'unavailable' }
    }
  } finally {
    if (request === requestNumber) loading.value = false
  }
}
function clearDetail() {
  detailNumber++
  selected.value = {}
  detailError.value = ''
  detailLoading.value = false
}
async function showTask(row: any) {
  clearDetail()
  const request = detailNumber
  open.value = true
  detailLoading.value = true
  try {
    const result = await getConsole(`tasks/${encodeURIComponent(row.task_id)}`)
    if (request === detailNumber) selected.value = result
  } catch (e) {
    if (request === detailNumber) detailError.value = errorMessage(e)
  } finally {
    if (request === detailNumber) detailLoading.value = false
  }
}
async function openLinkedTask() {
  const id = route.query.task_id
  if (!denied.value && typeof id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(id)) {
    await showTask({ task_id: id })
  }
}
onMounted(async () => {
  await load()
  if (!error.value) await openLinkedTask()
})
watch(() => route.query.task_id, openLinkedTask)
watch(
  () => route.query.state,
  (value) => {
    stateFilter.value = taskStateFilter(value)
    filterTasks()
  }
)
onBeforeUnmount(() => {
  requestNumber++
  clearDetail()
})
</script>
<style scoped>
.heading,
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}

.pager {
  margin-top: 20px;
}

h1 {
  margin: 0;
  font-size: 26px;
}

.heading p,
.note {
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}

.status-cards {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin: 20px 0;
}

.status-card {
  padding: 16px;
  font: inherit;
  color: var(--el-text-color-primary);
  text-align: left;
  cursor: pointer;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}

.status-card strong {
  display: block;
  margin: 10px 0;
  font-size: 28px;
}

.status-card.selected,
.status-card:hover {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
}

.status-card:disabled {
  cursor: wait;
}

.charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.charts h2 {
  margin: 0;
  font-size: 18px;
}

.task-actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

@media (width <= 1100px) {
  .status-cards {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (width <= 650px) {
  .status-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .charts {
    grid-template-columns: 1fr;
  }
}
</style>
