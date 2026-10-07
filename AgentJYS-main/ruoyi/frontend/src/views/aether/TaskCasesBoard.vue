<template>
  <div>
    <div class="tools"
      ><el-select
        v-model="state"
        clearable
        placeholder="全部处置阶段"
        @change="filter"
        style="width: 200px"
        ><el-option v-for="s in states" :key="s" :value="s" :label="caseState(s)" /></el-select
      ><el-button @click="load">刷新</el-button></div
    >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-space wrap class="counts"
      ><el-tag v-for="s in states" :key="s"
        >{{ caseState(s) }} {{ data.by_state?.[s] ?? '—' }}</el-tag
      ></el-space
    >
    <el-table
      :data="data.items || []"
      v-loading="loading"
      :empty-text="loading ? '正在读取' : error ? '读取失败' : '当前没有已建立的任务处置工单'"
    >
      <el-table-column label="业务" min-width="170"
        ><template #default="{ row }">{{
          taskKindLabel(row.original.kind)
        }}</template></el-table-column
      >
      <el-table-column label="影响范围" min-width="190"
        ><template #default="{ row }">{{ taskAudience(row) }}</template></el-table-column
      >
      <el-table-column label="阶段" min-width="150"
        ><template #default="{ row }">{{ caseState(row.state) }}</template></el-table-column
      >
      <el-table-column prop="owner_name" label="负责人" min-width="140" />
      <el-table-column label="更新时间" min-width="170"
        ><template #default="{ row }">{{ formatTime(row.updated_at) }}</template></el-table-column
      >
      <el-table-column label="操作" width="130"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="open(row)">查看与处理</el-button></template
        ></el-table-column
      >
    </el-table>
    <div class="tools"
      ><span>共 {{ data.total ?? '—' }} 条</span
      ><el-button :disabled="loading || offset === 0" @click="move(-20)">上一页</el-button
      ><el-button :disabled="loading || offset + 20 >= data.total" @click="move(20)"
        >下一页</el-button
      ></div
    >
    <el-drawer v-model="drawer" title="任务处置" size="70%" destroy-on-close
      ><TaskCasePanel
        v-if="selected"
        :task-id="selected.task_id"
        :data="selected.latest"
        @changed="load"
      /><el-button
        v-if="selected"
        @click="$router.push({ path: '/aether/tasks', query: { task_id: selected.task_id } })"
        >查看关联业务任务</el-button
      ></el-drawer
    >
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getOperations } from '@/api/aether'
import TaskCasePanel from './TaskCasePanel.vue'
import { caseState, caseError } from './taskCases.mjs'
import { taskKindLabel } from './dashboard.mjs'
import { taskAudience } from './console.mjs'
import { formatTime } from './presentation.mjs'
const states = ['open', 'in_progress', 'escalated', 'awaiting_verification', 'verified', 'closed']
const data = ref<any>({}),
  loading = ref(false),
  error = ref(''),
  state = ref(''),
  offset = ref(0),
  selected = ref<any>(),
  drawer = ref(false)
let seq = 0
function filter() {
  offset.value = 0
  load()
}
function move(delta: number) {
  offset.value += delta
  load()
}
function open(row: any) {
  selected.value = row
  drawer.value = true
}
async function load() {
  const n = ++seq
  loading.value = true
  error.value = ''
  try {
    const r = await getOperations('support', {
      q: 'task_cases',
      status: state.value || undefined,
      limit: 20,
      offset: offset.value
    })
    if (n === seq) data.value = r
  } catch (e) {
    if (n === seq) {
      data.value = {}
      error.value = caseError(e)
    }
  } finally {
    if (n === seq) loading.value = false
  }
}
onMounted(load)
</script>
<style scoped>
.tools {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 16px 0;
}
.counts {
  margin-bottom: 16px;
}
</style>
