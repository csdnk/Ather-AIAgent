<template>
  <ContentWrap>
    <el-result v-if="denied" icon="info" title="调度监测仅供平台管理员查看" />
    <template v-else>
      <div class="heading"
        ><h1>调度监测</h1><el-button :loading="loading" @click="load">刷新</el-button></div
      >
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <p class="muted">最近更新 {{ formatTime(data.observed_at) }}</p>
      <div class="cards">
        <button
          v-for="item in issueCards"
          :key="item.state"
          class="issue-card"
          :class="{ selected: problemState === item.state }"
          @click="selectState(item.state)"
        >
          <span>{{ item.label }}</span
          ><strong>{{
            data.task_summary?.by_state?.[item.state] ?? (data.task_summary ? 0 : '—')
          }}</strong
          ><span>查看任务 →</span>
        </button>
      </div>
      <el-card shadow="never">
        <template #header
          ><div class="heading"
            ><h2>{{ statusText(problemState) }}任务</h2
            ><el-button @click="openTaskList">打开任务处理</el-button></div
          ></template
        >
        <el-table
          :data="data.tasks?.items || []"
          v-loading="loading"
          :empty-text="loading ? '正在读取任务' : error ? '读取失败，请刷新' : '没有此状态的任务'"
        >
          <el-table-column label="业务 / 影响范围" min-width="190"
            ><template #default="{ row }"
              ><div>{{ taskKindLabel(row.kind) }}</div
              ><div class="muted">{{ taskAudience(row) || '影响范围待核实' }}</div></template
            ></el-table-column
          >
          <el-table-column label="问题原因" min-width="190"
            ><template #default="{ row }">{{ taskTriage(row).cause }}</template></el-table-column
          >
          <el-table-column label="业务结果" min-width="190"
            ><template #default="{ row }">{{ taskTriage(row).effect }}</template></el-table-column
          >
          <el-table-column label="建议处理人" min-width="150"
            ><template #default="{ row }">{{ taskTriage(row).owner }}</template></el-table-column
          >
          <el-table-column label="创建时间" min-width="170"
            ><template #default="{ row }">{{
              formatTime(row.created_at)
            }}</template></el-table-column
          >
          <el-table-column label="处理" width="135" fixed="right"
            ><template #default="{ row }"
              ><el-button link type="primary" @click="openTask(row)"
                >查看原因与处理</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <div class="pager"
          ><span
            >共
            {{ data.task_summary?.by_state?.[problemState] ?? (data.task_summary ? 0 : '—') }} 条 ·
            第 {{ cursors.length }} 页</span
          ><el-space
            ><el-button :disabled="loading || cursors.length === 1" @click="previous"
              >上一页</el-button
            ><el-button :disabled="loading || !data.tasks?.next_cursor" @click="next"
              >下一页</el-button
            ></el-space
          ></div
        >
      </el-card>
      <el-card shadow="never" class="block">
        <template #header><h2>业务执行情况</h2></template>
        <el-table :data="queues" :empty-text="error ? '读取失败' : observation(data.queue_metrics)">
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
      </el-card>
      <el-collapse class="block"
        ><el-collapse-item title="查看历史任务统计" name="history">
          <p
            >累计 {{ data.task_summary?.total ?? '—' }} 个任务，最近 24 小时新增
            {{ data.task_summary?.created_last_24h ?? '—' }} 个。</p
          >
          <Echart
            v-if="data.task_summary"
            :options="categoryChart(data.task_summary.by_kind, taskKindLabel, appStore.getIsDark)"
            :height="Math.max(270, Object.keys(data.task_summary.by_kind || {}).length * 32) + 'px'"
          /> </el-collapse-item
      ></el-collapse>
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Echart } from '@/components/Echart'
import { useAppStore } from '@/store/modules/app'
import { getConsole, getIdentity } from '@/api/aether'
import { readTaskDiagnostics } from '../taskAccess.mjs'
import { categoryChart, taskKindLabel } from '../dashboard.mjs'
import { observation, taskAudience, errorMessage } from '../console.mjs'
import { businessQueues, taskTriage } from '../triage.mjs'
import { formatTime, statusText } from '../presentation.mjs'
defineOptions({ name: 'AetherScheduling' })
const appStore = useAppStore(),
  router = useRouter()
const data = ref<any>({}),
  loading = ref(false),
  denied = ref(false),
  error = ref('')
const problemState = ref('attention_required'),
  cursors = ref<any[]>([undefined])
const queues = computed(() => businessQueues(data.value.queue_metrics))
const issueCards = [
  { state: 'attention_required', label: '已中断，待排查' },
  { state: 'failed', label: '处理失败' },
  { state: 'recovery_wait', label: '等待恢复检查' },
  { state: 'retry_wait', label: '等待自动重试' }
]
let sequence = 0
function selectState(state: string) {
  problemState.value = state
  cursors.value = [undefined]
  load()
}
function previous() {
  cursors.value.pop()
  load()
}
function next() {
  if (data.value.tasks?.next_cursor) {
    cursors.value.push(data.value.tasks.next_cursor)
    load()
  }
}
function openTaskList() {
  router.push({ path: '/aether/tasks', query: { state: problemState.value } })
}
function openTask(row: any) {
  router.push({ path: '/aether/tasks', query: { state: problemState.value, task_id: row.task_id } })
}
async function load() {
  const request = ++sequence
  loading.value = true
  error.value = ''
  data.value = {}
  try {
    const result = await readTaskDiagnostics(getIdentity, getConsole, {
      limit: 10,
      state: problemState.value,
      cursor: cursors.value.at(-1)
    })
    if (request === sequence) {
      data.value = result
      denied.value = false
    }
  } catch (e: any) {
    if (request === sequence) {
      denied.value = Number(e?.code || e?.status) === 403
      error.value = errorMessage(e)
    }
  } finally {
    if (request === sequence) loading.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => {
  sequence++
})
</script>
<style scoped>
.heading,
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
h1 {
  font-size: 26px;
  margin: 0;
}
h2 {
  font-size: 18px;
  margin: 0;
}
.muted {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
.cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 20px 0;
}
.issue-card {
  text-align: left;
  padding: 20px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  cursor: pointer;
  font: inherit;
}
.issue-card strong {
  display: block;
  font-size: 30px;
  margin: 12px 0;
}
.issue-card.selected {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.issue-card:hover {
  border-color: var(--el-color-primary);
}
.block,
.pager {
  margin-top: 20px;
}
@media (max-width: 900px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
