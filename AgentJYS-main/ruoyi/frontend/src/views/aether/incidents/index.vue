<template>
  <ResourcePanel
    resource="incidents"
    title="故障与告警"
    description="查看真实告警、处置记录、静默时间与通知投递。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherIncidents' })
</script>
