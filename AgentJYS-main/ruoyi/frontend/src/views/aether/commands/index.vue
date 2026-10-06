<template>
  <ResourcePanel
    resource="commands"
    title="命令记录"
    description="按原命令编号追踪状态，避免超时重复执行。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherCommands' })
</script>
