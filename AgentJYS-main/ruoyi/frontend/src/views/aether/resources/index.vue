<template>
  <ResourcePanel
    resource="resources"
    title="资源台账"
    description="维护运行资源、环境、负责人和依赖关系。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherResources' })
</script>
