<template>
  <div class="picker">
    <el-select
      v-model="tenant"
      clearable
      filterable
      placeholder="全部授权企业"
      aria-label="选择企业"
      @change="search"
    >
      <el-option v-for="item in tenants" :key="item.id" :label="item.name" :value="item.id" />
    </el-select>
    <el-input
      v-model="query"
      clearable
      placeholder="搜索姓名或登录名"
      aria-label="搜索用户"
      @keyup.enter="search"
    />
    <el-button :loading="loading" @click="search">查找用户</el-button>
    <el-select
      v-model="userId"
      clearable
      placeholder="选择用户"
      aria-label="选择用户"
      :loading="loading"
      @change="select"
    >
      <el-option
        v-for="item in users.items || []"
        :key="item.id"
        :value="item.id"
        :label="`${item.display_name || item.username} · ${item.tenant_name || '企业未返回'}`"
      />
    </el-select>
    <el-button :disabled="page === 1 || loading" @click="move(-1)">上一页用户</el-button>
    <el-button
      :disabled="users.total == null || page * 20 >= users.total || loading"
      @click="move(1)"
      >下一页用户</el-button
    >
  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <p v-if="!loading && users.total === 0">没有找到符合条件的用户。</p>
</template>
<script setup lang="ts">
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { getConsole } from '@/api/aether'
import { errorMessage } from './console.mjs'
const emit = defineEmits<{ change: [user: any] }>()
const tenant = ref(''),
  query = ref(''),
  userId = ref(''),
  page = ref(1),
  loading = ref(false),
  error = ref('')
const users = ref<any>({}),
  tenants = ref<any[]>([])
let generation = 0
function clearSelection() {
  userId.value = ''
  emit('change', null)
}
function search() {
  page.value = 1
  clearSelection()
  load()
}
function move(delta: number) {
  page.value += delta
  clearSelection()
  load()
}
function select() {
  emit('change', users.value.items?.find((item: any) => item.id === userId.value) || null)
}
async function load() {
  const request = ++generation
  loading.value = true
  error.value = ''
  users.value = {}
  try {
    const result = await getConsole('users', {
      q: query.value.trim(),
      tenant_id: tenant.value || undefined,
      limit: 20,
      offset: (page.value - 1) * 20
    })
    if (request === generation) {
      users.value = result
      tenants.value = result.tenant_choices || []
    }
  } catch (e) {
    if (request === generation) error.value = errorMessage(e)
  } finally {
    if (request === generation) loading.value = false
  }
}
onMounted(load)
onActivated(() => {
  if (!loading.value && !users.value.items) load()
})
onDeactivated(() => {
  generation++
  loading.value = false
  users.value = {}
  clearSelection()
})
onBeforeUnmount(() => {
  generation++
  users.value = {}
  tenants.value = []
})
</script>
<style scoped>
.picker {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.picker .el-select,
.picker .el-input {
  width: 220px;
}
</style>
