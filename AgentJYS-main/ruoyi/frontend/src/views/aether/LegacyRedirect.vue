<template><el-empty description="正在转到合并后的工作入口…" /></template>
<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
const props = defineProps<{ to: string; tab?: string }>()
const router = useRouter()
onMounted(() =>
  router.replace({ path: `/aether/${props.to}`, query: props.tab ? { tab: props.tab } : {} })
)
</script>
