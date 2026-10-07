<template>
  <ContentWrap>
    <div class="heading"
      ><div><h1>操作历史</h1></div
      ><el-button :loading="loading" @click="load">刷新</el-button></div
    >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p class="note">{{ observation(data) }} · {{ formatTime(data.observed_at) }}</p>
    <el-table v-loading="loading" :data="data.items || []" :empty-text="observation(data)">
      <el-table-column label="时间" width="180"
        ><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column
      >
      <el-table-column label="记录类型" width="120"
        ><template #default="{ row }">{{
          row.kind === 'command' ? '系统操作' : '内容访问'
        }}</template></el-table-column
      >
      <el-table-column label="操作人" min-width="140"
        ><template #default="{ row }">{{
          row.actor_name || row.actor_username || row.actor_id || '未记录'
        }}</template></el-table-column
      >
      <el-table-column label="事项" min-width="180"
        ><template #default="{ row }"
          >{{ readable(row.resource) }} ·
          {{ row.kind === 'command' ? readable(row.action) : '查看内容' }}</template
        ></el-table-column
      >
      <el-table-column label="处理状态" min-width="150"
        ><template #default="{ row }">{{ statusText(row.status) }}</template></el-table-column
      >
      <el-table-column label="说明" min-width="200"
        ><template #default="{ row }">{{
          row.reason ||
          (row.kind === 'command' ? explainRow('commands', row) : '授权内容读取；日志不保存正文')
        }}</template></el-table-column
      >
      <el-table-column label="详情" width="80"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="showDetail(row)">查看</el-button></template
        ></el-table-column
      >
    </el-table>
    <div class="pager"
      ><el-button :disabled="page === 1 || loading" @click="previous">上一页</el-button
      ><span
        >第 {{ page }} 页<span v-if="data.total != null"> · 共 {{ data.total }} 条</span></span
      ><el-button :disabled="!hasNext || loading" @click="next">下一页</el-button></div
    >
    <el-drawer v-model="open" title="操作与访问记录" size="min(720px, 92vw)"
      ><el-alert v-if="detailError" :title="detailError" type="error" :closable="false" /><el-button
        v-if="selected.kind === 'command'"
        :loading="detailLoading"
        @click="showDetail(selected)"
        >查询原操作结果</el-button
      ><EvidencePanel title="处理结果" :data="selected" /><EvidencePanel
        v-if="selected.kind === 'command'"
        title="执行回执"
        :data="selected.result"
    /></el-drawer>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { getConsole, getOperations } from '@/api/aether'
import EvidencePanel from '../EvidencePanel.vue'
import { errorMessage, observation } from '../console.mjs'
import { explainRow, formatTime, readable, statusText } from '../presentation.mjs'
defineOptions({ name: 'AetherHistory' })
const data = ref<any>({}),
  error = ref(''),
  loading = ref(false),
  page = ref(1),
  cursors = ref<any[]>([undefined]),
  selected = ref<any>({}),
  open = ref(false)
const detailError = ref(''),
  detailLoading = ref(false)
let requestNumber = 0,
  detailNumber = 0
async function showDetail(row: any) {
  const request = ++detailNumber
  selected.value = { ...row, result: undefined }
  detailError.value = ''
  open.value = true
  if (row.kind !== 'command') return
  detailLoading.value = true
  try {
    const response = await getOperations('commands', { command_id: row.id })
    const command = response.items?.find((item: any) => item.id === row.id)
    if (request === detailNumber) {
      if (command) selected.value = { ...row, ...command, kind: 'command' }
      else detailError.value = '原操作结果尚未返回，请继续查询原操作，不能重复提交。'
    }
  } catch (e) {
    if (request === detailNumber) detailError.value = errorMessage(e)
  } finally {
    if (request === detailNumber) detailLoading.value = false
  }
}
const hasNext = computed(
  () => !!data.value.next_cursor || (data.value.total != null && page.value * 50 < data.value.total)
)
function previous() {
  page.value--
  load()
}
function next() {
  cursors.value[page.value] = data.value.next_cursor
  page.value++
  load()
}
async function load() {
  const request = ++requestNumber
  loading.value = true
  error.value = ''
  data.value = {}
  try {
    const response = await getConsole('history', {
      limit: 50,
      offset: (page.value - 1) * 50,
      cursor: cursors.value[page.value - 1]
    })
    if (request === requestNumber) data.value = response
  } catch (e) {
    if (request === requestNumber) {
      error.value = errorMessage(e)
      data.value = { status: 'unavailable' }
    }
  } finally {
    if (request === requestNumber) loading.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => {
  requestNumber++
  detailNumber++
  selected.value = {}
  data.value = {}
})
</script>
<style scoped>
.heading,
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}
h1 {
  font-size: 26px;
  margin: 0;
}
.heading p,
.note {
  color: var(--el-text-color-secondary);
}
.pager {
  margin-top: 20px;
}
</style>
