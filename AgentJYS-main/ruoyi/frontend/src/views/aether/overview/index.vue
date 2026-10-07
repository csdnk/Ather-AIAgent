<template>
  <ContentWrap>
    <div class="header"
      ><div><h1>首页指标</h1><p>查看性能指标与业务运行趋势，按需要切换统计时间范围。</p></div
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
      <div class="stamp">最近更新 {{ formatTime(data.performance?.observed_at) }}</div>
      <PerformancePanel
        :performance="data.performance"
        :memory-metrics="memoryMetrics"
        :memory-note="memoryNote"
        :loading="loading"
      />
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { getIdentity, getOperations, getConsole } from '@/api/aether'
import PerformancePanel from '../PerformancePanel.vue'
import { canReadTaskDiagnostics } from '../taskAccess.mjs'
import { formatTime } from '../presentation.mjs'
const data = ref<any>({}),
  error = ref(''),
  loading = ref(false),
  platform = ref(false),
  forbidden = ref(false)
const memoryMetrics = ref<any>({}),
  memoryNote = ref('')
let sequence = 0
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
.stamp {
  color: var(--el-text-color-secondary);
}
.stamp {
  margin: 20px 0;
}
</style>
