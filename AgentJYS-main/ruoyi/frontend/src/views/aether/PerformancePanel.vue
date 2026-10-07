<template>
  <section class="performance">
    <template v-if="platform">
      <div class="section-heading"><h2>实时性能观测</h2></div>
      <div class="observation-grid">
        <el-card v-for="card in contractCards(memoryMetrics)" :key="card.label" shadow="never">
          <div class="muted">{{ card.label }}</div>
          <el-tooltip :content="card.note" placement="top" :show-after="300">
            <strong class="metric-value">{{ card.value }}</strong>
          </el-tooltip>
        </el-card>
      </div>
      <div class="section-heading">
        <el-tooltip
          content="最近 24 小时成功调用；仅统计有阶段记录的样本。阶段包含内部子步骤，数据库事务包含锁等待，向量计算可能包含证据写入，各行不可相加。过程日志不含最后一条返回日志自身的写入耗时。"
          placement="top"
          :show-after="300"
          ><h2>耗时分解</h2></el-tooltip
        >
        <el-radio-group v-model="timingPath" size="small" aria-label="耗时调用类型">
          <el-radio-button value="向量化">向量化</el-radio-button>
          <el-radio-button value="正文读取">正文读取</el-radio-button>
        </el-radio-group>
      </div>
      <el-table :data="timingRows" size="small" empty-text="暂无阶段耗时样本">
        <el-table-column prop="label" label="阶段" min-width="160" />
        <el-table-column prop="samples" label="样本数" width="90" align="right" />
        <el-table-column prop="avg" label="平均（毫秒）" min-width="130" align="right" />
        <el-table-column prop="p95" label="P95（毫秒）" min-width="130" align="right" />
      </el-table>
      <div class="section-heading"><h2>合同专项验收</h2></div>
      <el-table :data="contractBenchmarks()" size="small">
        <el-table-column prop="label" label="验收项目" min-width="160" />
        <el-table-column prop="target" label="合同目标" min-width="200" />
        <el-table-column label="验收状态" width="100">
          <template #default="{ row }"
            ><el-tag type="info" size="small">{{ row.status }}</el-tag></template
          >
        </el-table-column>
      </el-table>
    </template>
    <div class="section-heading"
      ><h2>业务运行趋势</h2>
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
          ><Echart :options="charts.requests" height="290px"
        /></el-card>
        <el-card shadow="never"
          ><template #header>召回记忆返回耗时（P95）</template
          ><Echart
            v-if="selected.summary.recall_latency_samples"
            :options="charts.latency"
            height="290px" /><el-empty v-else description="暂无召回耗时样本" :image-size="90"
        /></el-card>
      </div>
    </template>
    <el-empty
      v-else
      :description="loading ? '正在读取业务统计' : '暂未取得业务统计，请刷新重试'"
      :image-size="70"
    />
    <el-card v-if="platform && compression?.samples" class="compression" shadow="never"
      ><template #header>已发布记忆正文：压缩前后体积</template
      ><Echart :options="compressionChart" height="190px"
    /></el-card>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { Echart } from '@/components/Echart'
import { useAppStore } from '@/store/modules/app'
import {
  contractCards,
  contractBenchmarks,
  phaseRows,
  businessCharts,
  categoryChart
} from './dashboard.mjs'
const props = defineProps<{
  performance?: any
  memoryMetrics?: any
  platform?: boolean
  loading?: boolean
}>()
const windowKey = ref('24h')
const timingPath = ref('向量化')
const timingRows = computed(() =>
  phaseRows(props.memoryMetrics).filter((row) => row.path === timingPath.value)
)
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
.observation-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.metric-value {
  display: block;
  margin: 14px 0;
  font-size: 25px;
  line-height: 1.25;
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
@media (max-width: 800px) {
  .observation-grid,
  .business-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
