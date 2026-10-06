<template>
  <ContentWrap>
    <div class="header"
      ><div
        ><h1>值班总览</h1
        ><p>查看性能目标、业务运行趋势和待处理事项，按需要切换统计时间范围。</p></div
      ><el-button :loading="loading" @click="load">刷新数据</el-button></div
    >
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb-4" />
    <el-alert
      v-if="forbidden"
      title="当前账号用于 Agent 业务，未分配平台运维权限。"
      type="info"
      :closable="false"
    />
    <template v-else>
      <div class="stamp"
        >最近更新 {{ formatTime(data.observed_at) }} · {{ statusText(data.status) }}</div
      >
      <PerformancePanel
        :performance="data.performance"
        :memory-metrics="memoryMetrics"
        :memory-note="memoryNote"
        :loading="loading"
      />
      <el-alert
        :title="attention"
        :type="needsAttention ? 'warning' : 'info'"
        :closable="false"
        show-icon
        class="mb-4"
      />
      <div class="quick-links">
        <el-button type="primary" @click="$router.push('/aether/business')"
          >查找用户与记忆</el-button
        >
        <el-button v-if="platform" @click="$router.push('/aether/tasks')">检查任务与调度</el-button>
        <el-button v-if="platform" @click="$router.push('/aether/scheduling')"
          >查看调度监测</el-button
        >
        <el-button @click="$router.push('/aether/faults')">处理异常告警</el-button>
        <el-button @click="$router.push('/aether/history')">核对操作结果</el-button>
      </div>
      <el-alert
        title="当前请求和连接观测不能覆盖全部记忆召回、长期整理、存储维护及执行器在线情况。未采集项需进入任务与业务详情核实。"
        type="info"
        :closable="false"
        class="mb-4"
      />
      <div class="panels"
        ><el-card shadow="never"
          ><template #header>服务可用性</template>
          <p>业务接入：{{ statusText(data.p3?.readiness) }}。检查结果只反映采集时刻。</p>
          <div v-for="(item, index) in healthItems(data.p3)" :key="index" class="service-row">
            <div
              ><strong>{{ item.label }}</strong
              ><p>{{ item.note }}</p></div
            >
            <el-tag :type="item.text === '可用' ? 'success' : 'info'">{{ item.text }}</el-tag>
          </div>
        </el-card>
        <el-card shadow="never"
          ><template #header>最近告警与处理建议</template>
          <el-table
            :data="data.alerts || []"
            :empty-text="
              data.status === 'ok'
                ? '本次观测没有告警记录，未覆盖业务仍需检查'
                : '告警观测未确认，不能判断无异常'
            "
          >
            <el-table-column label="异常项目" min-width="150"
              ><template #default="scope">{{
                readable(scope.row.metric)
              }}</template></el-table-column
            >
            <el-table-column label="处理状态" min-width="110"
              ><template #default="scope">{{
                statusText(scope.row.state)
              }}</template></el-table-column
            >
            <el-table-column label="情况说明" min-width="260"
              ><template #default="scope">{{
                explainRow('incidents', scope.row)
              }}</template></el-table-column
            >
            <el-table-column label="最近触发" min-width="180"
              ><template #default="scope">{{
                formatTime(scope.row.last_seen)
              }}</template></el-table-column
            >
          </el-table>
          <p class="stamp">已恢复的告警保留供追溯；失败请求数包含统计窗口内的历史失败。</p>
        </el-card>
      </div>
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { getIdentity, getOperations, getConsole } from '@/api/aether'
import PerformancePanel from '../PerformancePanel.vue'
import { canReadTaskDiagnostics } from '../taskAccess.mjs'
import { explainRow, formatTime, healthItems, readable, statusText } from '../presentation.mjs'
const data = ref<any>({}),
  error = ref(''),
  loading = ref(false),
  platform = ref(false),
  forbidden = ref(false)
const memoryMetrics = ref<any>({}),
  memoryNote = ref('')
let sequence = 0
const needsAttention = computed(
  () =>
    data.value.status !== 'ok' ||
    (data.value.alerts || []).some((row: any) => row.state !== 'resolved')
)
const attention = computed(() => {
  if (data.value.status !== 'ok') return '部分运行数据尚未确认，请先刷新并检查服务连接。'
  const count = (data.value.alerts || []).filter((row: any) => row.state !== 'resolved').length
  return count
    ? `有 ${count} 条告警尚未恢复，请优先查看下方告警记录。`
    : '当前没有未恢复的告警。请继续关注失败请求及记忆保存进度。'
})
async function load() {
  const request = ++sequence
  loading.value = true
  platform.value = false
  error.value = ''
  memoryMetrics.value = {}
  data.value = {}
  memoryNote.value = ''
  try {
    const who = await getIdentity()
    if (request !== sequence) return
    platform.value = canReadTaskDiagnostics(who)
    forbidden.value = !who.permissions?.includes('aether:ops:read')
    if (!forbidden.value) {
      memoryNote.value = platform.value
        ? '正在读取当前部署的压缩记录…'
        : '当前企业账号仅显示本企业业务统计；部署级压缩与调度观测由平台管理员查看。'
      const overview = await getOperations('overview')
      if (request !== sequence) return
      data.value = overview
      const memoryRead = platform.value
        ? getConsole('diagnostics', { limit: 1 })
            .then((result) => {
              if (request !== sequence) return
              memoryMetrics.value = result.memory_observations || {}
              memoryNote.value = `部署级记忆观测更新于 ${formatTime(result.memory_observations?.observed_at)}。其余合同指标仍需独立测量。`
            })
            .catch(() => {
              if (request === sequence)
                memoryNote.value = '暂时无法读取部署级压缩记录；请稍后刷新。业务趋势可独立查看。'
            })
        : Promise.resolve()
      await memoryRead
    }
  } catch (e: any) {
    if (request !== sequence) return
    error.value = '暂时无法读取运行情况，请检查登录状态和服务连接后刷新。'
    data.value = {}
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
.service-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.quick-links {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin: 0 0 20px;
}
.service-row p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
}
.header h1 {
  margin: 0;
  font-size: 26px;
}
.header p,
.stamp,
.caption {
  color: var(--el-text-color-secondary);
}
.stamp {
  margin: 20px 0;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}
.metrics strong {
  display: block;
  font-size: 32px;
  margin-top: 12px;
}
.panels {
  display: grid;
  grid-template-columns: 1fr 1.5fr;
  gap: 20px;
}
@media (max-width: 1000px) {
  .metrics {
    grid-template-columns: repeat(2, 1fr);
  }
  .panels {
    grid-template-columns: 1fr;
  }
}
</style>
