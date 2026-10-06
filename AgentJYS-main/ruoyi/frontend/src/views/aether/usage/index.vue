<template>
  <ResourcePanel
    resource="usage"
    title="容量与用量"
    description="查看最近 24 小时请求、模型计量与已知费用。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherUsage' })
</script>
