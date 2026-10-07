<template>
  <ContentWrap>
    <h1>召回管理</h1>
    <el-alert v-if="identityError" :title="identityError" type="error" :closable="false" />
    <template v-if="canReadMemory(identity)">
      <MemoryUserPicker @change="selectUser" />
      <MemoryCommandNotice
        :pending="command.pending.value"
        :busy="command.busy.value"
        :message="command.message.value"
        @query="command.query"
        @abandon="command.abandon"
      />
      <el-empty v-if="!selected" description="请选择需要管理召回的用户" />
      <template v-else>
        <div class="toolbar">
          <h2>{{ selected.display_name || selected.username }}</h2>
          <el-button
            v-if="allowed('recall_execute')"
            :disabled="command.blocked.value"
            @click="openEditor('execute')"
            >临时召回</el-button
          >
          <el-button
            v-if="allowed('recall_scheme_create')"
            type="primary"
            :disabled="command.blocked.value"
            @click="openEditor('create')"
            >新增方案</el-button
          >
        </div>
        <el-tabs v-model="tab" @tab-change="filter"
          ><el-tab-pane name="schemes" label="召回方案" /><el-tab-pane
            name="history"
            label="执行记录" /><el-tab-pane name="records" label="历史召回"
        /></el-tabs>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
        <el-table
          v-loading="loading"
          :data="records.items || []"
          :empty-text="tableEmptyText(records, loading)"
        >
          <template v-if="tab === 'schemes'">
            <el-table-column prop="name" label="方案名称" min-width="170" />
            <el-table-column prop="query" label="检索内容" min-width="260" show-overflow-tooltip />
            <el-table-column label="检索范围" width="120"
              ><template #default="{ row }">{{
                sourceText(row.sources)
              }}</template></el-table-column
            >
            <el-table-column label="更新时间" width="175"
              ><template #default="{ row }">{{
                formatTime(row.updated_at)
              }}</template></el-table-column
            >
            <el-table-column label="操作" width="260"
              ><template #default="{ row }">
                <el-button
                  v-if="allowed('recall_execute')"
                  link
                  type="primary"
                  :disabled="command.blocked.value"
                  @click="openEditor('execute', row)"
                  >执行</el-button
                >
                <el-button
                  link
                  @click="openEditor(allowed('recall_scheme_update') ? 'update' : 'view', row)"
                  >{{ allowed('recall_scheme_update') ? '修改' : '查看' }}</el-button
                >
                <el-button
                  v-if="allowed('recall_scheme_delete')"
                  link
                  type="danger"
                  :disabled="row.version == null || command.blocked.value"
                  @click="remove(row)"
                  >删除</el-button
                >
              </template></el-table-column
            >
          </template>
          <template v-else-if="tab === 'history'">
            <el-table-column label="操作" min-width="150"
              ><template #default="{ row }">{{ actionText(row.action) }}</template></el-table-column
            >
            <el-table-column label="状态" width="160"
              ><template #default="{ row }">{{ statusText(row.status) }}</template></el-table-column
            >
            <el-table-column label="提交时间" min-width="180"
              ><template #default="{ row }">{{
                formatTime(row.created_at)
              }}</template></el-table-column
            >
            <el-table-column label="操作" width="160"
              ><template #default="{ row }"
                ><el-button link type="primary" @click="viewHistory(row)"
                  >查看结果</el-button
                ></template
              ></el-table-column
            >
          </template>
          <template v-else>
            <el-table-column prop="query" label="检索内容" min-width="300" show-overflow-tooltip />
            <el-table-column label="状态" width="160"
              ><template #default="{ row }">{{
                statusText(row.state || row.status)
              }}</template></el-table-column
            >
            <el-table-column label="创建时间" width="180"
              ><template #default="{ row }">{{
                formatTime(row.created_at)
              }}</template></el-table-column
            >
            <el-table-column label="操作" width="120"
              ><template #default="{ row }"
                ><el-button link type="primary" @click="viewHistory(row)"
                  >查看结果</el-button
                ></template
              ></el-table-column
            >
          </template>
        </el-table>
        <div class="pager"
          ><el-button :disabled="page === 1 || loading" @click="move(-1)">上一页</el-button
          ><span>第 {{ page }} 页</span
          ><el-button :disabled="!hasNext || loading" @click="move(1)">下一页</el-button></div
        >
      </template>
    </template>
    <el-dialog
      v-model="editor"
      :title="editorTitle"
      width="min(680px, 95vw)"
      :close-on-click-modal="false"
      @closed="clearForm"
    >
      <el-form
        label-position="top"
        :disabled="mode === 'view' || (mode === 'execute' && !!scheme)"
        @submit.prevent="save"
      >
        <el-form-item v-if="mode !== 'execute'" label="方案名称" required
          ><el-input v-model="form.name" maxlength="100"
        /></el-form-item>
        <el-form-item label="检索内容" required
          ><el-input
            v-model="form.query"
            type="textarea"
            :rows="5"
            maxlength="12000"
            show-word-limit
        /></el-form-item>
        <el-form-item label="检索范围"
          ><el-select v-model="form.sources"
            ><el-option label="长期记忆" value="long_term" /><el-option
              label="工作记忆"
              value="working" /><el-option label="全部记忆" value="both" /></el-select
        ></el-form-item>
        <el-form-item v-if="form.sources !== 'long_term'" label="所属会话" required>
          <el-select
            v-model="form.session_id"
            filterable
            clearable
            placeholder="选择该用户的会话"
            :loading="sessionsLoading"
          >
            <el-option
              v-if="
                form.session_id && !sessions.items?.some((item: any) => item.id === form.session_id)
              "
              :value="form.session_id"
              label="方案中保存的会话"
            />
            <el-option
              v-for="item in sessions.items || []"
              :key="item.id"
              :value="item.id"
              :label="item.title || item.name || `会话 · ${formatTime(item.created_at)}`"
            />
          </el-select>
          <div class="session-pager"
            ><el-button :disabled="sessionPage === 1 || sessionsLoading" @click="moveSessions(-1)"
              >上一页会话</el-button
            ><el-button
              :disabled="
                sessions.total == null || sessionPage * 50 >= sessions.total || sessionsLoading
              "
              @click="moveSessions(1)"
              >下一页会话</el-button
            ></div
          >
          <el-alert v-if="sessionsError" :title="sessionsError" type="error" :closable="false" />
        </el-form-item>
        <el-form-item label="上下文长度上限（词元）"
          ><el-input-number
            v-model="form.token_budget"
            :min="128"
            :max="8000"
            :step="128"
            :precision="0"
        /></el-form-item>
      </el-form>
      <template #footer
        ><el-button @click="editor = false">关闭</el-button
        ><el-button
          v-if="mode !== 'view'"
          type="primary"
          :loading="command.busy.value"
          :disabled="!formValid || command.blocked.value"
          @click="save"
          >{{ mode === 'execute' ? '开始召回' : '保存方案' }}</el-button
        ></template
      >
    </el-dialog>
    <el-drawer v-model="resultOpen" title="召回结果" size="min(800px, 95vw)">
      <div v-loading="resultLoading">
        <el-alert v-if="resultError" :title="resultError" type="warning" :closable="false" />
        <template v-if="resultData">
          <p>{{ resultSummary }}</p>
          <template v-if="pack">
            <h3 v-if="resultItems.length">命中的记忆</h3>
            <article v-for="(item, index) in resultItems" :key="index" class="memory-result"
              ><strong>记忆 {{ index + 1 }}</strong
              ><div class="body">{{ memoryText(item) }}</div></article
            >
            <template v-if="!resultItems.length && pack.rendered_context">
              <h3>召回内容</h3><div class="body">{{ pack.rendered_context }}</div>
            </template>
          </template>
        </template>
        <el-button v-if="historyRow" :loading="resultLoading" @click="viewHistory(historyRow)"
          >刷新结果</el-button
        >
      </div>
    </el-drawer>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, onMounted, reactive, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { getConsole, getIdentity, getOperations } from '@/api/aether'
import MemoryUserPicker from '../MemoryUserPicker.vue'
import MemoryCommandNotice from '../MemoryCommandNotice.vue'
import {
  actionText,
  canManageMemory,
  canReadMemory,
  commandErrorText,
  sourceText
} from '../memoryAdmin.mjs'
import { useMemoryCommand } from '../useMemoryCommand'
import { errorMessage, memoryText, recallItems, tableEmptyText } from '../console.mjs'
import { formatTime, statusText } from '../presentation.mjs'
defineOptions({ name: 'AetherRecalls' })
const identity = ref<any>({}),
  identityError = ref(''),
  selected = ref<any>(null),
  tab = ref('schemes')
const records = ref<any>({}),
  loading = ref(false),
  error = ref(''),
  page = ref(1),
  cursors = ref<any[]>([undefined])
const hasNext = computed(() =>
  tab.value === 'records'
    ? !!records.value.next_cursor
    : records.value.total != null && page.value * 20 < records.value.total
)
const editor = ref(false),
  mode = ref('create'),
  scheme = ref<any>(null)
const form = reactive({
  name: '',
  query: '',
  sources: 'long_term',
  session_id: '',
  token_budget: 1800
})
const sessions = ref<any>({}),
  sessionsLoading = ref(false),
  sessionsError = ref(''),
  sessionPage = ref(1)
const resultOpen = ref(false),
  resultLoading = ref(false),
  resultError = ref(''),
  resultData = ref<any>(null),
  historyRow = ref<any>(null)
let generation = 0,
  sessionGeneration = 0,
  resultGeneration = 0,
  alive = true
const allowed = (action: string) => canManageMemory(identity.value, action)
const editorTitle = computed(() =>
  mode.value === 'execute'
    ? scheme.value
      ? `执行召回：${scheme.value.name}`
      : '临时召回'
    : mode.value === 'create'
      ? '新增召回方案'
      : mode.value === 'view'
        ? '查看召回方案'
        : '修改召回方案'
)
const formValid = computed(
  () =>
    form.query.trim() &&
    (mode.value === 'execute' || form.name.trim()) &&
    (form.sources === 'long_term' || form.session_id) &&
    Number.isInteger(form.token_budget) &&
    form.token_budget >= 128 &&
    form.token_budget <= 8000 &&
    (mode.value !== 'update' || scheme.value?.version != null)
)
const pack = computed(() => {
  const data = resultData.value
  if (
    !data ||
    ['failed', 'pending', 'unknown', 'rejected'].includes(data.status) ||
    (data.result_status && data.result_status !== 'available')
  )
    return null
  for (const candidate of [data.result?.result, data.result, data])
    if (candidate && typeof candidate.rendered_context === 'string') return candidate
  return null
})
const resultItems = computed(() => (pack.value ? recallItems(pack.value) : []))
const resultSummary = computed(() => {
  if (pack.value)
    return pack.value.outcome === 'empty'
      ? '本次未找到符合条件的记忆。'
      : pack.value.outcome === 'degraded'
        ? '召回已结束，部分检索未完成。'
        : `召回已完成，返回 ${resultItems.value.length} 条记忆。`
  if (resultData.value?.result_status === 'invalidated')
    return '这次召回结果已失效，请重新执行召回。'
  return `操作状态：${statusText(resultData.value?.status || resultData.value?.record?.state)}。尚未取得可展示的召回正文。`
})
const command = useMemoryCommand(identity, async (reply, reference) => {
  if (selected.value?.id !== reference.user_id) return
  editor.value = false
  clearForm()
  await load()
  if (reference.action === 'recall_execute' && selected.value?.id === reference.user_id)
    await viewHistory({
      id: reference.command_id,
      command_id: reference.command_id,
      ...reply,
      user_id: reference.user_id
    })
})
function clearForm() {
  Object.assign(form, {
    name: '',
    query: '',
    sources: 'long_term',
    session_id: '',
    token_budget: 1800
  })
  scheme.value = null
}
function selectUser(user: any) {
  selected.value = user
  editor.value = false
  clearForm()
  resultGeneration++
  resultOpen.value = false
  resultData.value = null
  resultError.value = ''
  resultLoading.value = false
  historyRow.value = null
  sessionGeneration++
  sessions.value = {}
  sessionsError.value = ''
  sessionPage.value = 1
  filter()
}
function filter() {
  page.value = 1
  cursors.value = [undefined]
  load()
}
function move(delta: number) {
  if (delta > 0) cursors.value[page.value] = records.value.next_cursor
  page.value += delta
  load()
}
async function load() {
  const request = ++generation
  records.value = {}
  error.value = ''
  loading.value = false
  if (!selected.value) return
  loading.value = true
  try {
    const response = await getConsole('recalls', {
      q: tab.value,
      user_id: selected.value.id,
      limit: 20,
      offset: (page.value - 1) * 20,
      cursor: tab.value === 'records' ? cursors.value[page.value - 1] : undefined
    })
    if (request === generation) records.value = response
  } catch (e) {
    if (request === generation) {
      error.value = errorMessage(e)
      records.value = { status: 'unavailable' }
    }
  } finally {
    if (request === generation) loading.value = false
  }
}
function openEditor(action: string, row?: any) {
  clearForm()
  mode.value = action
  scheme.value = row || null
  if (row)
    Object.assign(form, {
      name: row.name,
      query: row.query,
      sources: row.sources,
      session_id: row.session_id || '',
      token_budget: row.token_budget
    })
  editor.value = true
  sessionPage.value = 1
  loadSessions()
}
function moveSessions(delta: number) {
  sessionPage.value += delta
  loadSessions()
}
async function loadSessions() {
  const request = ++sessionGeneration
  sessions.value = {}
  sessionsError.value = ''
  sessionsLoading.value = true
  try {
    const response = await getConsole('conversations', {
      user_id: selected.value.id,
      limit: 50,
      offset: (sessionPage.value - 1) * 50
    })
    if (request === sessionGeneration) sessions.value = response
  } catch (e) {
    if (request === sessionGeneration) sessionsError.value = errorMessage(e)
  } finally {
    if (request === sessionGeneration) sessionsLoading.value = false
  }
}
async function save() {
  if (!selected.value || !formValid.value || mode.value === 'view') return
  const parameters: any = {
    sources: form.sources,
    session_id: form.sources === 'long_term' ? undefined : form.session_id,
    token_budget: form.token_budget
  }
  const action =
    mode.value === 'execute'
      ? 'recall_execute'
      : mode.value === 'create'
        ? 'recall_scheme_create'
        : 'recall_scheme_update'
  if (mode.value === 'execute') {
    parameters.text = form.query
    if (scheme.value) parameters.scheme_id = scheme.value.id
  } else {
    parameters.name = form.name.trim()
    parameters.query = form.query
  }
  const sent = await command.submit(
    action,
    selected.value.id,
    parameters,
    scheme.value?.id,
    scheme.value?.version
  )
  if (sent && alive) {
    editor.value = false
    clearForm()
  }
}
async function remove(row: any) {
  const user = selected.value?.id
  if (!user || row.version == null) return
  try {
    await ElMessageBox.confirm(`确定删除召回方案“${row.name}”？`, '删除方案', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  if (!alive || selected.value?.id !== user) return
  await command.submit('recall_scheme_delete', user, {}, row.id, row.version)
}
async function viewHistory(row: any) {
  if (!selected.value) return
  const request = ++resultGeneration,
    user = selected.value.id
  historyRow.value = row
  resultOpen.value = true
  resultData.value = null
  resultError.value = ''
  resultLoading.value = true
  try {
    let result = row
    if (!row.recall_id) {
      const response = await getOperations('commands', { command_id: row.command_id || row.id })
      result = response.items?.find(
        (item: any) => (item.command_id || item.id) === (row.command_id || row.id)
      )
      if (!result) throw new Error('Original command unavailable')
    }
    const recallId =
      result.recall_id || result.result?.recall_id || result.result?.record?.recall_id
    if (recallId)
      result = await getConsole(`recalls/${encodeURIComponent(recallId)}`, { user_id: user })
    if (request === resultGeneration) {
      resultData.value = result
      if (['failed', 'rejected'].includes(result.status))
        resultError.value = commandErrorText(result)
    }
  } catch (e) {
    if (request === resultGeneration) resultError.value = errorMessage(e)
  } finally {
    if (request === resultGeneration) resultLoading.value = false
  }
}
onMounted(async () => {
  try {
    const result = await getIdentity()
    if (!alive) return
    identity.value = result
    command.restore()
    if (!canReadMemory(result)) identityError.value = '当前账号无权查看召回内容。'
  } catch (e) {
    if (alive) identityError.value = errorMessage(e)
  }
})
onBeforeUnmount(() => {
  alive = false
  generation++
  sessionGeneration++
  resultGeneration++
  records.value = {}
  sessions.value = {}
  resultData.value = null
  clearForm()
  selected.value = null
})
onDeactivated(() => selectUser(null))
</script>
<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;
}

.toolbar h2 {
  flex: 1;
}

.pager,
.session-pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
}

.pager {
  justify-content: flex-end;
}

.body {
  padding: 16px;
  line-height: 1.8;
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  overflow-wrap: anywhere;
}

.memory-result {
  margin: 18px 0;
}
</style>
