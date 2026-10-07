<template>
  <section class="handling">
    <TaskCasePanel :task-id="(data.task || data).task_id" :data="data" />
    <h3>怎么处理这条任务</h3>
    <el-descriptions :column="1" border>
      <el-descriptions-item label="问题">{{ guidance.cause }}</el-descriptions-item>
      <el-descriptions-item label="建议处理人">{{ guidance.owner }}</el-descriptions-item>
      <el-descriptions-item label="业务结果">{{ guidance.effect }}</el-descriptions-item>
      <el-descriptions-item label="还会自动重试吗">{{ guidance.retry }}</el-descriptions-item>
      <el-descriptions-item label="后台可执行操作">{{
        guidance.controls.reason
      }}</el-descriptions-item>
    </el-descriptions>
    <ol
      ><li v-for="step in guidance.steps" :key="step">{{ step }}</li></ol
    >
    <el-space
      ><el-button @click="copy">复制排查信息</el-button
      ><el-button @click="$router.push('/aether/faults?tab=task_cases')"
        >打开工单跟进</el-button
      ></el-space
    >
    <p v-if="copyResult">{{ copyResult }}</p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { taskTriage } from './triage.mjs'
import { taskAudience } from './console.mjs'
import { taskKindLabel } from './dashboard.mjs'
import { statusText } from './presentation.mjs'
import TaskCasePanel from './TaskCasePanel.vue'
const props = defineProps<{ data: any }>()
const guidance = computed(() => taskTriage(props.data))
const copyResult = ref('')
async function copy() {
  const task = props.data.task || props.data
  const text = [
    `任务：${task.task_id || '未返回'}`,
    `业务：${taskKindLabel(task.kind)}`,
    `影响范围：${taskAudience(task) || '待核实'}`,
    `状态：${statusText(task.state)}`,
    `问题：${guidance.value.cause}`,
    `原因代码：${task.error_code || '未记录'}`,
    `业务结果：${guidance.value.effect}`,
    `建议处理人：${guidance.value.owner}`,
    ...guidance.value.steps
  ].join('\n')
  try {
    await navigator.clipboard.writeText(text)
    copyResult.value = '已复制，可粘贴到工单说明。'
  } catch {
    copyResult.value = '复制失败，请根据本页信息填写工单。'
  }
}
</script>
<style scoped>
.handling {
  margin: 16px 0;
}
li {
  margin: 10px 0;
  line-height: 1.65;
}
</style>
