<template>
  <ContentWrap>
    <el-result
      v-if="denied"
      icon="info"
      title="调度监测仅供平台管理员查看"
      sub-title="企业账号可在用户与业务中查看本企业请求和记忆的处理情况。"
    />
    <template v-else>
      <div class="heading"
        ><div
          ><h1>调度监测</h1
          ><p>查看任务是否及时执行、队列是否积压，以及冷热分层动作是否得到确认。</p></div
        >
        <el-space
          ><el-button @click="$router.push('/aether/tasks')">进入任务处理</el-button
          ><el-button type="primary" :loading="loading" @click="load"
            >刷新调度观测</el-button
          ></el-space
        ></div
      >
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <p class="muted"
        >{{ loading ? '正在读取调度状态…' : observation(data) }} · 最近采样
        {{ formatTime(data.observed_at) }}</p
      >
      <div class="cards"
        ><el-card v-for="card in schedulingCards(data)" :key="card.label" shadow="never"
          ><span class="muted">{{ card.label }}</span
          ><strong>{{ card.value }}</strong
          ><p class="muted">{{ card.note }}</p></el-card
        ></div
      >
      <div class="charts">
        <el-card shadow="never"
          ><template #header>任务状态分布</template
          ><Echart
            v-if="data.task_summary?.total"
            :options="categoryChart(data.task_summary.by_state)"
            height="270px" /><el-empty
            v-else
            :description="data.task_summary ? '当前部署没有已绑定任务' : '尚未取得任务统计'"
            :image-size="70"
        /></el-card>
        <el-card shadow="never"
          ><template #header>各业务累计任务量</template
          ><Echart
            v-if="data.task_summary?.total"
            :options="categoryChart(data.task_summary.by_flow, flowLabel)"
            height="270px" /><el-empty v-else description="暂无可展示的任务分布" :image-size="70"
        /></el-card>
      </div>
      <p class="muted"
        >以上是当前部署全部绑定任务的累计记录，包含历史终态；任务完成与业务效果确认分别判断。</p
      >
      <el-card shadow="never" class="block"
        ><template #header>执行器与队列</template>
        <p class="muted"
          >轮询记录表示执行器曾向队列取任务。请结合最后联系时间判断；无轮询者不直接等于服务宕机。积压是
          Temporal 返回的估计值。</p
        >
        <el-table
          :data="sectionItems(data.queue_metrics)"
          :empty-text="loading ? '正在查询队列' : observation(data.queue_metrics)"
        >
          <el-table-column label="负责业务" min-width="180"
            ><template #default="{ row }">{{
              queueBusiness(row.task_queue)
            }}</template></el-table-column
          >
          <el-table-column label="执行类型" min-width="130"
            ><template #default="{ row }">{{ readable(row.task_type) }}</template></el-table-column
          >
          <el-table-column label="观测结果" min-width="120"
            ><template #default="{ row }">{{ statusText(row.status) }}</template></el-table-column
          >
          <el-table-column label="执行器轮询记录" min-width="145"
            ><template #default="{ row }">{{
              row.pollers == null
                ? '尚未确认'
                : `${row.pollers.length}${row.pollers_truncated ? '+' : ''} 个`
            }}</template></el-table-column
          >
          <el-table-column label="最近联系" min-width="175"
            ><template #default="{ row }">{{ lastPoll(row) }}</template></el-table-column
          >
          <el-table-column label="排队积压" min-width="120"
            ><template #default="{ row }">{{
              row.backlog_count_hint == null ? '尚未采集' : `约 ${row.backlog_count_hint} 个`
            }}</template></el-table-column
          >
        </el-table>
      </el-card>
      <el-card shadow="never" class="block"
        ><template #header>周期维护</template>
        <div class="periodic"
          ><div
            ><span class="muted">周期任务绑定</span
            ><h3>{{ statusText(data.periodic?.status) }}</h3></div
          ><div
            ><span class="muted">工作流执行情况</span
            ><h3>{{
              statusText(data.periodic?.workflow?.state || data.periodic?.workflow?.status)
            }}</h3></div
          ><div
            ><span class="muted">下一次执行</span
            ><h3>{{
              data.periodic?.next_run_at
                ? formatTime(data.periodic.next_run_at)
                : '尚未返回计划时间'
            }}</h3></div
          ></div
        >
        <p class="muted"
          >周期工作流可能长期保持运行状态。缺少下一次执行时间时，不推算或编造调度计划。</p
        >
      </el-card>
      <el-card shadow="never" class="block"
        ><template #header>冷热分层调度</template>
        <p class="muted"
          >当前部署累计记录 {{ placement?.total ?? '未知' }} 个动作。下表展示最近
          {{ placement?.items?.length ?? 0 }}
          条；按原任务绑定筛选，状态未知表示仍需查询原操作结果。</p
        >
        <el-table
          :data="placement?.items || []"
          :empty-text="placement ? '当前部署尚无已记录的冷热迁移动作' : '尚未取得分层调度记录'"
        >
          <el-table-column label="调度意图" min-width="145"
            ><template #default="{ row }">{{ actionLabel(row.outcome) }}</template></el-table-column
          >
          <el-table-column label="原层级 → 目标层级" min-width="180"
            ><template #default="{ row }"
              >{{ statusText(row.current_tier) }} → {{ statusText(row.target_tier) }}</template
            ></el-table-column
          >
          <el-table-column label="动作进度" min-width="130"
            ><template #default="{ row }">{{ statusText(row.state) }}</template></el-table-column
          >
          <el-table-column label="存储执行回执" min-width="150"
            ><template #default="{ row }">{{
              statusText(row.feedback_state)
            }}</template></el-table-column
          >
          <el-table-column label="执行方式" min-width="145"
            ><template #default="{ row }">{{
              row.provider_mode === 'real'
                ? '真实存储执行'
                : row.provider_mode === 'simulated'
                  ? '模拟执行'
                  : '未识别执行方式'
            }}</template></el-table-column
          >
          <el-table-column label="发起时间" min-width="175"
            ><template #default="{ row }">{{
              formatTime(row.created_at)
            }}</template></el-table-column
          >
        </el-table>
        <p class="muted"
          >发出迁移意图不代表已经完成迁移；请结合动作进度、真实存储回执和执行方式核对。</p
        >
      </el-card>
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Echart } from '@/components/Echart'
import { getConsole, getIdentity } from '@/api/aether'
import { readTaskDiagnostics } from '../taskAccess.mjs'
import { categoryChart, schedulingCards, flowLabel, actionLabel } from '../dashboard.mjs'
import { observation, queueBusiness, sectionItems, errorMessage } from '../console.mjs'
import { formatTime, statusText, readable } from '../presentation.mjs'
defineOptions({ name: 'AetherScheduling' })
const data = ref<any>({}),
  loading = ref(false),
  denied = ref(false),
  error = ref('')
const placement = computed(() => data.value.memory_observations?.placement)
let sequence = 0
function lastPoll(row: any) {
  const times = (row.pollers || [])
    .map((p: any) => p.last_access_time)
    .filter(Boolean)
    .sort()
  return times.length ? formatTime(times[times.length - 1]) : '尚无联系记录'
}
async function load() {
  const request = ++sequence
  loading.value = true
  error.value = ''
  data.value = {}
  try {
    const result = await readTaskDiagnostics(getIdentity, getConsole, { limit: 1 })
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
.heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}
h1 {
  font-size: 26px;
  margin: 0;
}
.heading p,
.muted {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
.muted {
  font-size: 13px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 20px 0;
}
.cards strong {
  display: block;
  font-size: 30px;
  margin: 14px 0;
}
.charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.block {
  margin-top: 20px;
}
.periodic {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}
@media (max-width: 900px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .charts,
  .periodic {
    grid-template-columns: 1fr;
  }
}
</style>
