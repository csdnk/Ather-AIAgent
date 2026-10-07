<template>
  <ContentWrap>
    <h1>记忆管理</h1>
    <el-alert v-if="identityError" :title="identityError" type="error" :closable="false" />
    <template v-if="canReadMemory(identity)">
      <MemoryUserPicker @change="selectUser" />
      <div class="toolbar memory-types">
        <span id="memory-type-label">记忆类型</span>
        <el-radio-group v-model="kind" aria-labelledby="memory-type-label" @change="filter">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="working">工作记忆</el-radio-button>
          <el-radio-button value="episodic">情景记忆</el-radio-button>
          <el-radio-button value="semantic">语义记忆</el-radio-button>
        </el-radio-group>
      </div>
      <MemoryCommandNotice
        :pending="command.pending.value"
        :busy="command.busy.value"
        :message="command.message.value"
        @query="command.query"
        @abandon="command.abandon"
      />
      <el-empty v-if="!selected" description="请选择需要管理记忆的用户" />
      <template v-else>
        <div class="toolbar">
          <h2>{{ selected.display_name || selected.username }}</h2>
          <el-button
            v-if="allowed('memory_create')"
            type="primary"
            :disabled="command.blocked.value"
            @click="create"
            >新增记忆</el-button
          >
        </div>
        <div class="toolbar filters">
          <el-select
            v-model="state"
            clearable
            placeholder="全部状态"
            aria-label="记忆状态"
            @change="filter"
          >
            <el-option
              v-for="item in ['active', 'archived', 'superseded', 'expired', 'deleted']"
              :key="item"
              :label="statusText(item)"
              :value="item"
            />
          </el-select>
          <el-button :loading="loading" @click="load">刷新</el-button>
        </div>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
        <el-table
          v-loading="loading"
          :data="records.items || []"
          :empty-text="tableEmptyText(records, loading)"
        >
          <el-table-column label="记忆内容" min-width="320"
            ><template #default="{ row }"
              ><div class="summary">{{ summary(row) }}</div></template
            ></el-table-column
          >
          <el-table-column label="类型" width="200"
            ><template #default="{ row }">{{ memoryKind(row.kind) }}</template></el-table-column
          >
          <el-table-column label="状态" width="150"
            ><template #default="{ row }">{{ statusText(row.status) }}</template></el-table-column
          >
          <el-table-column label="检索状态" width="170"
            ><template #default="{ row }">{{ projectionText(row) }}</template></el-table-column
          >
          <el-table-column label="创建时间" width="170"
            ><template #default="{ row }">{{
              formatTime(row.created_at)
            }}</template></el-table-column
          >
          <el-table-column label="操作" width="100"
            ><template #default="{ row }"
              ><el-button link type="primary" @click="openDetail(row)">查看</el-button></template
            ></el-table-column
          >
        </el-table>
        <div class="pager"
          ><el-button :disabled="page === 1 || loading" @click="previous">上一页</el-button
          ><span>第 {{ page }} 页</span
          ><el-button :disabled="!records.next_cursor || loading" @click="next"
            >下一页</el-button
          ></div
        >
      </template>
    </template>
    <el-drawer v-model="detailOpen" title="记忆详情" size="min(760px, 95vw)">
      <div v-loading="detailLoading">
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" />
        <template v-if="detail.memory">
          <div class="toolbar"
            ><el-tag>{{ statusText(detail.memory.status) }}</el-tag
            ><span>{{ memoryKind(detail.memory.kind) }}</span
            ><el-button :loading="detailLoading" @click="loadDetail">刷新详情</el-button></div
          >
          <h3>记忆全文</h3><div class="body">{{ memoryText(detail) }}</div>
          <div class="toolbar actions">
            <el-button
              v-if="allowed('memory_update')"
              :disabled="!canEdit || command.blocked.value"
              @click="edit"
              >修改正文</el-button
            >
            <el-button
              v-if="allowed('memory_archive') && detail.memory.status === 'active'"
              :disabled="!hasRevision || command.blocked.value"
              @click="lifecycle('memory_archive')"
              >归档</el-button
            >
            <el-button
              v-if="allowed('memory_activate') && detail.memory.status === 'archived'"
              :disabled="!hasRevision || command.blocked.value"
              @click="lifecycle('memory_activate')"
              >恢复使用</el-button
            >
            <el-button
              v-if="
                allowed('memory_delete') &&
                !['deleted', 'superseded', 'expired'].includes(detail.memory.status)
              "
              type="danger"
              :disabled="
                !hasRevision || detail.memory.object_revision == null || command.blocked.value
              "
              @click="lifecycle('memory_delete')"
              >删除</el-button
            >
          </div>
        </template>
      </div>
    </el-drawer>
    <el-dialog
      v-model="editor"
      :title="editing ? '修改记忆' : '新增记忆'"
      width="min(680px, 95vw)"
      :close-on-click-modal="false"
      @closed="clearForm"
    >
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="记忆正文" required
          ><el-input
            v-model="form.text"
            type="textarea"
            :rows="10"
            maxlength="12000"
            show-word-limit
        /></el-form-item>
        <el-form-item v-if="!editing" label="内容类别"
          ><el-select v-model="form.category"
            ><el-option
              v-for="item in categories"
              :key="item.value"
              :label="item.label"
              :value="item.value" /></el-select
        ></el-form-item>
      </el-form>
      <template #footer
        ><el-button @click="editor = false">取消</el-button
        ><el-button
          type="primary"
          :loading="command.busy.value"
          :disabled="!form.text.trim() || command.blocked.value"
          @click="save"
          >保存</el-button
        ></template
      >
    </el-dialog>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, onMounted, reactive, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { getConsole, getIdentity } from '@/api/aether'
import MemoryUserPicker from '../MemoryUserPicker.vue'
import MemoryCommandNotice from '../MemoryCommandNotice.vue'
import { canReadMemory, canManageMemory, editableBody, memoryMutation } from '../memoryAdmin.mjs'
import { useMemoryCommand } from '../useMemoryCommand'
import {
  errorMessage,
  memoryId,
  memoryKind,
  memoryText,
  projectionText,
  tableEmptyText
} from '../console.mjs'
import { formatTime, statusText } from '../presentation.mjs'
defineOptions({ name: 'AetherMemories' })
const identity = ref<any>({}),
  identityError = ref(''),
  selected = ref<any>(null)
const records = ref<any>({}),
  loading = ref(false),
  error = ref(''),
  kind = ref(''),
  state = ref(''),
  page = ref(1),
  cursors = ref<any[]>([undefined])
const detail = ref<any>({}),
  detailOpen = ref(false),
  detailLoading = ref(false),
  detailError = ref(''),
  detailId = ref('')
const editor = ref(false),
  editing = ref(false),
  form = reactive({ text: '', category: 'fact' })
let listGeneration = 0,
  detailGeneration = 0,
  alive = true
const categories = [
  { value: 'fact', label: '事实' },
  { value: 'observation', label: '观察' },
  { value: 'event', label: '事件' },
  { value: 'decision', label: '决策' },
  { value: 'explicit_constraint', label: '明确约束' }
]
const allowed = (action: string) => canManageMemory(identity.value, action)
const hasRevision = computed(
  () => detail.value.memory?.ref?.version != null && detail.value.memory?.object_revision != null
)
const canEdit = computed(
  () =>
    hasRevision.value &&
    editableBody(detail.value) !== null &&
    ['active', 'archived'].includes(detail.value.memory?.status)
)
const command = useMemoryCommand(identity, async (reply, reference) => {
  if (!alive || selected.value?.id !== reference.user_id) return
  editor.value = false
  clearForm()
  await load()
  if (!alive || selected.value?.id !== reference.user_id) return
  const created = reference.action === 'memory_create' ? reply.result?.memories?.[0] : null
  if (created && memoryId(created)) {
    openDetail(created)
    return
  }
  if (detailOpen.value) await loadDetail()
})
function summary(row: any) {
  if (typeof row.summary === 'string') return row.summary
  if (typeof row.summary?.text === 'string') return row.summary.text
  if (typeof row.summary?.body === 'string') return row.summary.body
  return ['deleted', 'expired', 'superseded'].includes(row.status)
    ? '当前状态下正文不可读取'
    : '点击查看全文'
}
function clearForm() {
  form.text = ''
  form.category = 'fact'
}
function clearDetail() {
  detailGeneration++
  detail.value = {}
  detailLoading.value = false
  detailError.value = ''
  detailOpen.value = false
  editor.value = false
  clearForm()
}
function selectUser(user: any) {
  selected.value = user
  clearDetail()
  filter()
}
function filter() {
  page.value = 1
  cursors.value = [undefined]
  clearDetail()
  load()
}
async function load() {
  const request = ++listGeneration
  records.value = {}
  error.value = ''
  loading.value = false
  if (!selected.value) return
  loading.value = true
  try {
    const result = await getConsole('memories', {
      user_id: selected.value.id,
      kind: kind.value || undefined,
      status: state.value || undefined,
      limit: 20,
      cursor: cursors.value[page.value - 1]
    })
    if (request === listGeneration) records.value = result
  } catch (e) {
    if (request === listGeneration) {
      error.value = errorMessage(e)
      records.value = { status: 'unavailable' }
    }
  } finally {
    if (request === listGeneration) loading.value = false
  }
}
function previous() {
  page.value--
  load()
}
function next() {
  cursors.value[page.value] = records.value.next_cursor
  page.value++
  load()
}
function openDetail(row: any) {
  clearDetail()
  detailId.value = memoryId(row)
  detailOpen.value = true
  loadDetail()
}
async function loadDetail() {
  const request = ++detailGeneration
  detail.value = {}
  detailError.value = ''
  detailLoading.value = true
  try {
    const result = await getConsole(`memories/${encodeURIComponent(detailId.value)}`, {
      user_id: selected.value.id
    })
    if (request === detailGeneration) detail.value = result
  } catch (e) {
    if (request === detailGeneration) detailError.value = errorMessage(e)
  } finally {
    if (request === detailGeneration) detailLoading.value = false
  }
}
function create() {
  clearForm()
  editing.value = false
  editor.value = true
}
function edit() {
  if (!canEdit.value) return
  form.text = editableBody(detail.value)
  editing.value = true
  editor.value = true
}
async function save() {
  if (!selected.value || !form.text.trim() || (editing.value && !canEdit.value)) return
  const mutation = editing.value
    ? memoryMutation(detail.value, { text: form.text })
    : { parameters: { text: form.text, category: form.category }, expected_version: undefined }
  const sent = await command.submit(
    editing.value ? 'memory_update' : 'memory_create',
    selected.value.id,
    mutation.parameters,
    editing.value ? detailId.value : undefined,
    mutation.expected_version
  )
  if (sent && alive) {
    editor.value = false
    clearForm()
  }
}
async function lifecycle(action: string) {
  const user = selected.value?.id,
    id = detailId.value
  if (!user || !hasRevision.value) return
  const mutation = memoryMutation(detail.value)
  try {
    await ElMessageBox.confirm(
      action === 'memory_delete'
        ? '确定删除这条记忆？删除后无法通过此页面恢复正文。'
        : action === 'memory_archive'
          ? '确定归档这条记忆？'
          : '确定恢复使用这条记忆？',
      '确认操作',
      {
        type: action === 'memory_delete' ? 'warning' : 'info',
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      }
    )
  } catch {
    return
  }
  if (!alive || selected.value?.id !== user || detailId.value !== id) return
  await command.submit(action, user, mutation.parameters, id, mutation.expected_version)
}
onMounted(async () => {
  try {
    const result = await getIdentity()
    if (!alive) return
    identity.value = result
    command.restore()
    if (!canReadMemory(result)) identityError.value = '当前账号无权查看记忆内容。'
  } catch (e) {
    if (alive) identityError.value = errorMessage(e)
  }
})
onBeforeUnmount(() => {
  alive = false
  listGeneration++
  clearDetail()
  records.value = {}
  selected.value = null
})
onDeactivated(() => selectUser(null))
</script>
<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}

.toolbar h2 {
  flex: 1;
}

.filters .el-select {
  width: 220px;
}

.body {
  padding: 16px;
  line-height: 1.8;
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  overflow-wrap: anywhere;
}

.summary {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.actions {
  margin-top: 24px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 14px;
  margin-top: 18px;
}
</style>
