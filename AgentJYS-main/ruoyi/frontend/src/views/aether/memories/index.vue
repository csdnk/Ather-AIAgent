<template>
  <ResourcePanel
    resource="memories"
    title="记忆状态"
    description="检查会话保存与召回回执，定位业务链路。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherMemories' })
</script>
