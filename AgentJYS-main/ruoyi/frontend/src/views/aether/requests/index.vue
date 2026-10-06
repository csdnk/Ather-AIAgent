<template>
  <ResourcePanel
    resource="requests"
    title="请求诊断"
    description="追踪请求阶段、尝试次数、首字延迟与记忆回执。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherRequests' })
</script>
