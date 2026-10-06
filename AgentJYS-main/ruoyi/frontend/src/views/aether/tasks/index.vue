<template>
  <ResourcePanel
    resource="tasks"
    title="任务与积压"
    description="查看任务状态并以当前版本提交受控操作。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherTasks' })
</script>
