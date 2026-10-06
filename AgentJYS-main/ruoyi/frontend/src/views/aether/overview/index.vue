<template>
  <ContentWrap>
    <div class="header"
      ><div><h1>Aether 运行总览</h1><p>请求、记忆与基础服务的当前运行情况</p></div
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
        >采集时间 {{ data.observed_at || '未知' }} · {{ data.status || '未知' }} · 统计窗口 24
        小时</div
      >
      <div class="metrics"
        ><el-card v-for="card in cards" :key="card.key" shadow="never"
          ><div class="caption">{{ card.label }}</div
          ><strong>{{ metric(card.key) }}</strong></el-card
        ></div
      >
      <div class="panels"
        ><el-card shadow="never"
          ><template #header>基础服务</template
          ><el-descriptions :column="1" border
            ><el-descriptions-item
              v-for="(value, key) in data.p3 || { status: '当前权限未提供基础服务数据' }"
              :key="key"
              :label="String(key)"
              >{{ displayValue(value) }}</el-descriptions-item
            ></el-descriptions
          ></el-card
        >
        <el-card shadow="never"
          ><template #header>最近告警</template
          ><el-table :data="data.alerts || []" empty-text="当前无告警记录"
            ><el-table-column prop="metric" label="指标" /><el-table-column
              prop="state"
              label="状态" /><el-table-column prop="value" label="当前值" /><el-table-column
              prop="last_seen"
              label="最近触发"
              min-width="180" /></el-table></el-card
      ></div>
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getIdentity, getOperations } from '@/api/aether'
import { displayValue } from '../operations.mjs'
const data = ref<any>({}),
  error = ref(''),
  loading = ref(false),
  forbidden = ref(false)
const cards = [
  { key: 'requests', label: '请求总数' },
  { key: 'complete', label: '已完成' },
  { key: 'failed', label: '失败请求' },
  { key: 'pending', label: '等待处理' },
  { key: 'saved', label: '记忆已保存' }
]
function metric(key: string) {
  const rows = data.value.usage?.items
  if (!Array.isArray(rows)) return '未知'
  return rows.reduce((sum: number, row: any) => sum + Number(row[key] || 0), 0)
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    const who = await getIdentity()
    forbidden.value = !who.permissions?.includes('aether:ops:read')
    if (!forbidden.value) data.value = await getOperations('overview')
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)
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
