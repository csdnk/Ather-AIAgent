<template>
  <ResourcePanel
    resource="configuration"
    title="配置与发布"
    description="维护运维配置记录，显式激活 P3 配置快照。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherConfiguration' })
</script>
