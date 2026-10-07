<template><el-empty description="正在打开任务与调度…" /></template>
<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
defineOptions({ name: 'AetherScheduling' })
const route = useRoute()
const router = useRouter()
onMounted(() => router.replace({ path: '/aether/tasks', query: route.query, hash: route.hash }))
</script>
