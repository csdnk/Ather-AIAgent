<template>
  <ContentWrap>
    <div class="header"
      ><div><h1>首页指标</h1></div
      ><el-button :loading="loading" @click="load">刷新数据</el-button></div
    >
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb-4" />
    <el-alert
      v-if="forbidden"
      title="当前账号没有查看首页指标的权限。"
      type="info"
      :closable="false"
    />
    <template v-else-if="authorized">
      <div class="stamp"
        ><el-tag>{{ platform ? '平台全局' : '当前企业' }}</el-tag> 最近更新
        {{ formatTime(data.performance?.observed_at) }}</div
      >
      <PerformancePanel
        :performance="data.performance"
        :memory-metrics="memoryMetrics"
        :platform="platform"
        :loading="loading"
      />
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { getIdentity, getOperations, getConsole } from '@/api/aether'
import PerformancePanel from '../PerformancePanel.vue'
import { canReadTaskDiagnostics, canReadOperations } from '../taskAccess.mjs'
import { formatTime } from '../presentation.mjs'
const data = ref<any>({}),
  error = ref(''),
  loading = ref(false),
  platform = ref(false),
  forbidden = ref(false),
  authorized = ref(false)
const memoryMetrics = ref<any>({})
let sequence = 0
async function load() {
  const request = ++sequence
  loading.value = true
  platform.value = false
  error.value = ''
  memoryMetrics.value = {}
  data.value = {}
  authorized.value = false
  forbidden.value = false
  try {
    const who = await getIdentity()
    if (request !== sequence) return
    platform.value = canReadTaskDiagnostics(who)
    authorized.value = canReadOperations(who)
    forbidden.value = !authorized.value
    if (!forbidden.value) {
      const overview = await getOperations('overview')
      if (request !== sequence) return
      data.value = overview
      const memoryRead = platform.value
        ? getConsole('diagnostics', { limit: 1 })
            .then((result) => {
              if (request !== sequence) return
              memoryMetrics.value = result.memory_observations || {}
            })
            .catch(() => {
              if (request === sequence) error.value = '压缩指标读取失败，请刷新重试。'
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
