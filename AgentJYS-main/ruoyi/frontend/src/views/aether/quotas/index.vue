<template>
  <ResourcePanel
    resource="quotas"
    title="配额管理"
    description="按业务租户编号管理滚动 24 小时请求、并发和已观测模型 Token 预算。历史缺失计量不视为零。"
  />
</template>
<script setup lang="ts">
import ResourcePanel from '../ResourcePanel.vue'
defineOptions({ name: 'AetherQuotas' })
</script>
