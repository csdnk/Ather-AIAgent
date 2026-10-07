<template>
  <div v-if="pending" class="notice">
    <el-tag
      :type="
        pending.status === 'succeeded'
          ? 'success'
          : ['failed', 'rejected'].includes(pending.status)
            ? 'danger'
            : 'warning'
      "
      >{{ actionText(pending.action) }}：{{ outcomeText }}</el-tag
    >
    <el-button v-if="!commandTerminal(pending.status)" :loading="busy" @click="$emit('query')"
      >查询原操作</el-button
    >
    <el-button
      v-if="pending.unaccepted && !commandTerminal(pending.status)"
      :disabled="busy"
      @click="$emit('abandon')"
      >撤销未受理请求</el-button
    >
    <span v-if="!commandTerminal(pending.status)">结果确认前暂不能提交新操作。</span>
  </div>
  <el-alert v-if="message" :title="message" type="warning" :closable="false" />
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { actionText, commandTerminal } from './memoryAdmin.mjs'
import { statusText } from './presentation.mjs'
const props = defineProps<{ pending: any; busy: boolean; message: string }>()
const outcomeText = computed(() => {
  if (props.pending?.status !== 'succeeded') return statusText(props.pending?.status)
  if (props.pending.action === 'memory_delete' && props.pending.deletion_blocked)
    return props.pending.cleanup_state === 'completed'
      ? '删除已生效，清理已完成'
      : '删除已生效，后台正在清理'
  return '处理完成'
})
defineEmits<{ query: []; abandon: [] }>()
</script>
<style scoped>
.notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 16px 0;
}
</style>
