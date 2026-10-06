<template>
  <ResourcePanel
    resource="backups"
    title="备份与恢复"
    description="创建平台数据库备份并进行隔离恢复演练。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherBackups' })
</script>
