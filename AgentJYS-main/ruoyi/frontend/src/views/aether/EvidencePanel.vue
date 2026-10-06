<template>
  <section class="evidence">
    <h3>{{ title }}</h3>
    <p class="note">{{ observation(data) }}</p>
    <el-descriptions :column="1" border>
      <el-descriptions-item v-for="field in visibleFields" :key="field.key" :label="field.label">{{
        field.value
      }}</el-descriptions-item>
    </el-descriptions>
    <el-table v-if="rows.length" :data="rows" border>
      <el-table-column label="项目 / 阶段" min-width="150"
        ><template #default="{ row }">{{
          readable(row.kind || row.stage || row.phase || row.name || row.action)
        }}</template></el-table-column
      >
      <el-table-column label="状态" min-width="130"
        ><template #default="{ row }">{{
          statusText(row.status || row.state)
        }}</template></el-table-column
      >
      <el-table-column label="说明" min-width="220"
        ><template #default="{ row }">{{
          row.completed_parts != null
            ? `已完成 ${row.completed_parts} 个处理部分`
            : row.error_code
              ? readable(row.error_code, '服务报告异常，错误码见技术详情')
              : row.message || row.description || row.reason || '该记录未保存说明'
        }}</template></el-table-column
      >
      <el-table-column label="时间" min-width="170"
        ><template #default="{ row }">{{
          formatTime(
            row.updated_at ||
              row.created_at ||
              row.observed_at ||
              row.committed_at ||
              row.occurred_at
          )
        }}</template></el-table-column
      >
    </el-table>
    <template v-if="(depth || 0) < 2"
      ><EvidencePanel
        v-for="section in nestedSections"
        :key="section.key"
        :title="section.label"
        :data="section.value"
        :depth="(depth || 0) + 1"
    /></template>
    <el-collapse
      ><el-collapse-item title="技术详情（原始观测与编号）" name="raw">
        <pre>{{ JSON.stringify(data, null, 2) }}</pre>
      </el-collapse-item></el-collapse
    >
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { observation, sectionItems, scopeText, projectionText } from './console.mjs'
import { formatTime, readable, statusText } from './presentation.mjs'
const props = defineProps<{ title: string; data?: any; depth?: number }>()
const rows = computed(() => sectionItems(props.data))
const visibleFields = computed(() => {
  const data = props.data || {}
  const fields = [
    ['tenant_name', '所属企业', String],
    ['user_name', '受影响用户', String],
    ['status', '读取 / 执行状态', statusText],
    ['state', '处理状态', statusText],
    ['phase', '当前阶段', readable],
    ['stage', '当前步骤', readable],
    ['current_step', '当前步骤', readable],
    ['attempt', '已尝试次数', String],
    ['retry_count', '重试次数', String],
    ['max_attempts', '最多尝试次数', String],
    ['effect_status', '业务效果', statusText],
    ['memory_status', '记忆状态', statusText],
    ['projection_state', '检索索引状态', (v: any) => projectionText({ projection_state: v })],
    ['rejected_candidate_count', '未采纳候选数', String],
    ['tier', '当前存储层', readable],
    ['access_count', '已记录访问次数', String],
    ['score', '热度分数', String],
    ['worker', '本机执行器状态', (v: any) => (v === 'running' ? '运行中' : statusText(v))],
    ['temporal', '调度连接状态', statusText],
    ['worker_restarts', '本机执行器重启次数', String],
    ['started_at', '开始等待', formatTime],
    ['next_check_at', '下次状态检查', formatTime],
    ['closed_at', '执行结束', formatTime],
    ['tokens_used', '上下文已用 Token', String],
    ['token_budget', '上下文 Token 预算', String],
    ['outcome', '结果', statusText],
    ['working', '近期记忆覆盖', statusText],
    ['long_term', '长期记忆覆盖', statusText],
    ['next_retry_at', '下次重试', formatTime],
    ['last_run_at', '最近运行', formatTime],
    ['next_run_at', '下次运行（有调度依据）', formatTime],
    ['observed_at', '观测时间', formatTime],
    ['reason', '情况说明', (v: any) => readable(v, '服务报告了处理原因，详见技术记录')],
    [
      'reason_code',
      '处理原因',
      (v: any) => (v === 'READY' ? '执行器与依赖已就绪' : readable(v, '具体原因见技术详情'))
    ],
    ['message', '服务说明', (v: any) => readable(v, '服务返回说明，详见技术记录')],
    ['scope_note', '覆盖范围', scopeText],
    ['error_code', '错误说明', (v: any) => readable(v, '服务报告异常，错误码见技术详情')]
  ] as const
  return fields
    .filter(([key]) => data[key] != null && typeof data[key] !== 'object')
    .map(([key, label, format]) => ({ key, label, value: format(data[key]) }))
})
const nestedSections = computed(() => {
  const fields = [
    ['wait', '等待依赖与原因'],
    ['stages', '阶段进度'],
    ['checkpoints', '已提交步骤'],
    ['tasks', '关联处理任务'],
    ['actions', '实际存储维护动作'],
    ['input', '当前存储位置'],
    ['heat', '访问热度'],
    ['workflow', '周期工作流执行'],
    ['coverage', '召回覆盖范围']
  ]
  return fields
    .filter(([key]) => props.data?.[key] != null)
    .map(([key, label]) => ({
      key,
      label,
      value: Array.isArray(props.data[key])
        ? { status: 'available', items: props.data[key] }
        : props.data[key]
    }))
})
</script>
<style scoped>
.evidence {
  margin: 18px 0;
}
.note {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
