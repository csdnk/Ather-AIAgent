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
          ><h1>调度监测</h1><p>查看任务是否及时执行、队列是否积压，以及执行器最近是否联系。</p></div
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
            :options="categoryChart(data.task_summary.by_state, statusText, appStore.getIsDark)"
            height="270px" /><el-empty
            v-else
            :description="data.task_summary ? '当前部署没有已绑定任务' : '尚未取得任务统计'"
            :image-size="70"
        /></el-card>
        <el-card shadow="never"
          ><template #header>累计任务构成</template
          ><Echart
            v-if="data.task_summary?.total"
            :options="categoryChart(data.task_summary.by_kind, taskKindLabel, appStore.getIsDark)"
            :height="
              Math.max(270, Object.keys(data.task_summary.by_kind || {}).length * 32) + 'px'
            " /><el-empty v-else description="暂无可展示的任务分布" :image-size="70"
        /></el-card>
      </div>
      <p class="muted"
        >累计 {{ data.task_summary?.total ?? '未知' }} 个任务，最近 24 小时新建
        {{ data.task_summary?.created_last_24h ?? '未知' }} 个；最近一次创建于
        {{ formatTime(data.task_summary?.latest_task_at) }}。
        分层策略评估用于判断是否需要调整存储，不代表发生搬迁；实际动作与回执请进入独立的“冷热分层调度”页面查看。</p
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
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Echart } from '@/components/Echart'
import { useAppStore } from '@/store/modules/app'
import { getConsole, getIdentity } from '@/api/aether'
import { readTaskDiagnostics } from '../taskAccess.mjs'
import { categoryChart, schedulingCards, taskKindLabel } from '../dashboard.mjs'
import { observation, queueBusiness, sectionItems, errorMessage } from '../console.mjs'
import { formatTime, statusText, readable } from '../presentation.mjs'
defineOptions({ name: 'AetherScheduling' })
const appStore = useAppStore()
const data = ref<any>({}),
  loading = ref(false),
  denied = ref(false),
  error = ref('')
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
@media (max-width: 900px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .charts {
    grid-template-columns: 1fr;
  }
}
</style>
