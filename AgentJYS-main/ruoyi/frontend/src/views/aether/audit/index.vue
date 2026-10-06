<template>
  <ResourcePanel
    resource="audit"
    title="运维审计"
    description="核对受理、执行结果和未知结果，追踪原命令。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherAudit' })
</script>
