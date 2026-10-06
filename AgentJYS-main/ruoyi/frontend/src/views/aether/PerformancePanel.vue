<template>
  <section class="performance">
    <div class="section-heading"
      ><div
        ><h2>性能与合同目标</h2><p>合同目标与实测证据分别展示。当前未形成完整性能验收结论。</p></div
      >
      <el-tag type="info">合同指标参考</el-tag></div
    >
    <div class="contract-grid">
      <el-card v-for="card in contractCards(memoryMetrics)" :key="card.label" shadow="never">
        <div class="muted">{{ card.label }}</div
        ><strong class="metric-value">{{ card.value }}</strong>
        <div class="target">{{ card.target }}</div
        ><p class="muted">{{ card.note }}</p>
      </el-card>
    </div>
    <p class="muted">{{ memoryNote }}</p>
    <div class="section-heading"
      ><div
        ><h2>业务运行趋势</h2
        ><p>按请求创建时间统计；成功率只计算已完成和失败请求。首字响应包含召回与模型等待。</p></div
      >
      <el-radio-group v-model="windowKey" aria-label="统计时间范围"
        ><el-radio-button value="1h">最近 1 小时</el-radio-button
        ><el-radio-button value="24h">最近 24 小时</el-radio-button
        ><el-radio-button value="7d">最近 7 天</el-radio-button></el-radio-group
      ></div
    >
    <template v-if="selected">
      <div class="business-grid">
        <el-card v-for="item in businessCards" :key="item.label" shadow="never"
          ><span class="muted">{{ item.label }}</span
          ><strong class="metric-value">{{ item.value }}</strong></el-card
        >
      </div>
      <div class="chart-grid">
        <el-card shadow="never"
          ><template #header>请求量与处理结果</template
          ><Echart :options="charts.requests" height="290px" /><p class="muted"
            >每 {{ selected.bucket_seconds / 60 }} 分钟一组；首尾分组可能不足完整时段。</p
          ></el-card
        >
        <el-card shadow="never"
          ><template #header>开始回答需要多久</template
          ><Echart
            v-if="selected.summary.latency_samples"
            :options="charts.latency"
            height="290px"
          /><el-empty v-else description="此时间范围内尚无首字响应样本" :image-size="90" /><p
            class="muted"
            >95% 的已观测请求在此时间内开始回答。无样本处留空；这不是短期记忆接口的 P99 指标。</p
          ></el-card
        >
      </div>
    </template>
    <el-empty
      v-else
      :description="loading ? '正在读取业务统计' : '暂未取得业务统计，请刷新重试'"
      :image-size="70"
    />
    <el-card v-if="compression?.samples" class="compression" shadow="never"
      ><template #header>已发布记忆正文：压缩前后体积</template
      ><Echart :options="compressionChart" height="190px" /><p class="muted"
        >{{ compression.samples }} 条压缩记录，包含历史版本。统计正文 UTF-8
        字节数，原文可能仍保留；不代表数据库、索引和备份的实际磁盘占用。</p
      ></el-card
    >
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { Echart } from '@/components/Echart'
import { useAppStore } from '@/store/modules/app'
import { contractCards, businessCharts, categoryChart } from './dashboard.mjs'
const props = defineProps<{
  performance?: any
  memoryMetrics?: any
  memoryNote?: string
  loading?: boolean
}>()
const windowKey = ref('24h')
const appStore = useAppStore()
const selected = computed(() => props.performance?.windows?.[windowKey.value])
const charts = computed(() => businessCharts(selected.value?.series || [], appStore.getIsDark))
const compression = computed(() => props.memoryMetrics?.compression)
const compressionChart = computed(() =>
  categoryChart(
    {
      '原始正文（KB）': compression.value.original_bytes / 1024,
      '压缩正文（KB）': compression.value.stored_bytes / 1024
    },
    (v: string) => v,
    appStore.getIsDark
  )
)
const businessCards = computed(() => {
  const s = selected.value.summary
  return [
    { label: '请求总数', value: s.requests },
    {
      label: '已结束请求成功率',
      value: s.success_rate == null ? '暂无样本' : `${s.success_rate.toFixed(1)}%`
    },
    { label: '失败请求', value: s.failed },
    { label: '记忆已保存', value: s.saved }
  ]
})
</script>
<style scoped>
.performance {
  margin: 22px 0;
}
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
  margin: 24px 0 16px;
}
h2 {
  margin: 0 0 8px;
  font-size: 20px;
}
p {
  line-height: 1.65;
  margin: 10px 0 0;
}
.muted,
.section-heading p {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.contract-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 14px;
}
.metric-value {
  display: block;
  margin: 14px 0;
  font-size: 25px;
  line-height: 1.25;
}
.target {
  font-size: 13px;
  color: var(--el-color-primary);
}
.business-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.compression {
  margin-top: 16px;
}
@media (max-width: 1250px) {
  .contract-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 800px) {
  .contract-grid,
  .business-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
