<template>
  <ContentWrap>
    <div class="heading"
      ><div
        ><h1>用户与业务</h1><p>先找到用户，再沿会话、记忆和召回查看实际内容与处理过程。</p></div
      ></div
    >
    <el-tabs v-model="topTab">
      <el-tab-pane label="按用户查看" name="users">
        <el-form class="search" @submit.prevent="searchUsers">
          <el-select
            v-model="tenant"
            clearable
            filterable
            placeholder="全部授权企业"
            style="min-width: 190px"
            @change="searchUsers"
            ><el-option
              v-for="item in tenantChoices"
              :key="item.id"
              :label="item.name"
              :value="item.id"
          /></el-select>
          <el-input
            v-model="query"
            clearable
            placeholder="搜索企业、姓名或登录名（服务端检索）"
            aria-label="搜索用户"
            @keyup.enter="searchUsers"
          />
          <el-button type="primary" :loading="usersLoading" @click="searchUsers"
            >查找用户</el-button
          >
        </el-form>
        <p v-if="users.tenant_choices_truncated" class="note"
          >企业选项仅显示前 1000 项；可用搜索框按企业名称继续检索用户。</p
        >
        <el-alert v-if="usersError" :title="usersError" type="error" :closable="false" />
        <div class="workspace">
          <aside>
            <div class="note">{{
              users.total == null ? '总人数尚未返回' : `匹配 ${users.total} 位用户`
            }}</div>
            <el-empty
              v-if="!usersLoading && !users.items?.length"
              :description="observation(users)"
              :image-size="72"
            />
            <button
              v-for="user in users.items || []"
              :key="user.id"
              class="user"
              :class="{ selected: selected?.id === user.id }"
              @click="selectUser(user)"
            >
              <strong>{{ user.display_name || user.username }}</strong
              ><span>{{ user.tenant_name || '企业信息未返回' }}</span
              ><small
                >{{ user.username }} ·
                {{
                  user.enabled == null ? '账号状态未确认' : user.enabled ? '账号启用' : '账号停用'
                }}</small
              >
            </button>
            <div class="pager"
              ><el-button :disabled="userPage === 1 || usersLoading" @click="changeUserPage(-1)"
                >上一页</el-button
              ><el-button
                :disabled="users.total == null || userPage * 20 >= users.total || usersLoading"
                @click="changeUserPage(1)"
                >下一页</el-button
              ></div
            >
          </aside>
          <main>
            <el-empty v-if="!selected" description="点击左侧用户姓名，查看其会话和记忆" />
            <template v-else>
              <h2
                >{{ selected.display_name || selected.username }}
                <small>{{ selected.tenant_name }}</small></h2
              >
              <p class="note">正文仅按当前账号的授权范围读取，每次查看均记录访问历史。</p>
              <el-tabs v-model="userTab" @tab-change="loadUserData">
                <el-tab-pane label="会话与召回" name="conversations" />
                <el-tab-pane label="记忆内容与生命周期" name="memories" />
              </el-tabs>
              <div class="filters">
                <template v-if="userTab === 'memories'"
                  ><el-select
                    v-model="kindFilter"
                    clearable
                    placeholder="全部记忆类型"
                    @change="filterRecords"
                    ><el-option
                      v-for="kind in ['working', 'episodic', 'semantic']"
                      :key="kind"
                      :label="memoryKind(kind)"
                      :value="kind" /></el-select
                  ><el-select
                    v-model="memoryState"
                    clearable
                    placeholder="全部生命周期"
                    @change="filterRecords"
                    ><el-option
                      v-for="state in ['active', 'archived', 'superseded', 'expired', 'deleted']"
                      :key="state"
                      :label="statusText(state)"
                      :value="state" /></el-select
                ></template>
                <template v-else
                  ><el-select
                    v-model="conversationState"
                    clearable
                    placeholder="全部会话状态"
                    @change="filterRecords"
                    ><el-option
                      v-for="state in ['pending', 'complete', 'failed']"
                      :key="state"
                      :label="statusText(state)"
                      :value="state" /></el-select
                  ><el-date-picker
                    v-model="dateRange"
                    type="datetimerange"
                    start-placeholder="会话更新时间起"
                    end-placeholder="会话更新时间止"
                    @change="filterRecords"
                /></template>
              </div>
              <p class="note"
                >条件在服务端检索。会话状态以最新一次请求为准，时间按会话更新时间筛选。</p
              >
              <el-alert v-if="listError" :title="listError" type="error" :closable="false" />
              <div class="list-head"
                ><span class="note"
                  >{{ observation(records) }} · {{ formatTime(records.observed_at) }}</span
                ><el-button :loading="listLoading" @click="loadUserData">刷新</el-button></div
              >
              <el-table
                v-loading="listLoading"
                :data="records.items || []"
                :empty-text="observation(records)"
                @row-click="openRecord"
              >
                <el-table-column
                  :label="userTab === 'memories' ? '真实内容摘要' : '会话主题'"
                  min-width="220"
                  ><template #default="{ row }"
                    ><el-button
                      class="text-link"
                      link
                      type="primary"
                      @click.stop="openRecord(row)"
                      >{{
                        row.summary ||
                        row.title ||
                        (userTab === 'memories' ? '查看记忆内容' : '未命名会话')
                      }}</el-button
                    ><div v-if="userTab === 'memories'" class="note">{{
                      memoryKind(row.type || row.kind || row.memory_type)
                    }}</div></template
                  ></el-table-column
                >
                <el-table-column label="当前状态" min-width="120"
                  ><template #default="{ row }">{{
                    statusText(row.status || row.state || row.last_status)
                  }}</template></el-table-column
                >
                <el-table-column v-if="userTab === 'memories'" label="可检索" width="100"
                  ><template #default="{ row }">{{
                    projectionText(row)
                  }}</template></el-table-column
                >
                <el-table-column label="创建时间" width="170"
                  ><template #default="{ row }">{{
                    formatTime(row.created_at)
                  }}</template></el-table-column
                >
              </el-table>
              <div class="pager"
                ><el-button :disabled="recordPage === 1 || listLoading" @click="previousRecords"
                  >上一页</el-button
                ><span
                  >第 {{ recordPage }} 页 · 本页 {{ records.items?.length || 0 }} 条<span
                    v-if="records.total != null"
                  >
                    / 共 {{ records.total }} 条</span
                  ></span
                ><el-button :disabled="!hasNext || listLoading" @click="nextRecords"
                  >下一页</el-button
                ></div
              >
            </template>
          </main>
        </div>
      </el-tab-pane>
      <el-tab-pane label="全局业务请求" name="requests" lazy
        ><ResourcePanel
          resource="requests"
          title="全局业务请求"
          description="查看请求回执与保存进度。正文请从用户列表进入；此表搜索仅筛选本页。"
      /></el-tab-pane>
    </el-tabs>
    <el-drawer
      v-model="detailOpen"
      :title="detailTitle"
      size="min(820px, 92vw)"
      destroy-on-close
      @closed="clearDetail"
    >
      <el-button v-if="detailTrail.length" class="mb-4" @click="backDetail"
        >返回上一条关联记录</el-button
      >
      <el-skeleton v-if="detailLoading" :rows="5" animated />
      <el-alert v-else-if="detailError" :title="detailError" type="error" :closable="false" />
      <template v-else-if="detailType === 'conversations'">
        <div class="pager"
          ><el-button :disabled="turnPage === 1" @click="changeTurnPage(-1)">上一页对话</el-button
          ><span
            >第 {{ turnPage }} 页 · 已返回 {{ detail.turns?.length || 0 }} 条<span
              v-if="detail.turns_total != null"
            >
              / 共 {{ detail.turns_total }} 条</span
            ></span
          ><el-button :disabled="!detail.turns_truncated" @click="changeTurnPage(1)"
            >下一页对话</el-button
          ></div
        >
        <p class="note">{{ formatTime(detail.observed_at) }} · {{ observation(detail) }}</p>
        <el-empty v-if="!detail.turns?.length" description="此会话尚未返回对话记录" />
        <article v-for="turn in detail.turns || []" :key="turn.id" class="turn">
          <div class="list-head"
            ><strong>{{ formatTime(turn.created_at) }}</strong
            ><el-tag>{{ statusText(turn.status) }}</el-tag></div
          >
          <h4>用户提问</h4><div class="body">{{ turn.input || '未保存提问内容' }}</div>
          <h4>实际回复</h4><div class="body">{{ turn.output || '尚无已保存回复' }}</div>
          <p
            >记忆保存：{{ statusText(turn.memory_status) }} · 当前阶段：{{
              readable(turn.phase)
            }}</p
          >
          <p class="note"
            >开始处理 {{ formatTime(turn.started_at) }} · 首次响应
            {{ formatTime(turn.first_token_at) }}</p
          >
          <el-space wrap
            ><el-button
              v-for="(id, index) in turn.memory_ids || []"
              :key="id"
              @click="openLinked('memories', id)"
              >查看产出记忆 {{ Number(index) + 1 }}</el-button
            ><el-button v-if="turn.recall_id" @click="openLinked('recalls', turn.recall_id)"
              >查看本次召回</el-button
            ><el-button v-if="turn.job_id" @click="openLinked('tasks', turn.job_id)"
              >查看处理任务</el-button
            ></el-space
          >
          <p v-if="!turn.memory_ids?.length && !turn.recall_id" class="note"
            >该历史记录未保存可点击的记忆或召回关联信息。</p
          >
        </article>
      </template>
      <template v-else-if="detailType === 'memories'">
        <el-descriptions :column="2" border
          ><el-descriptions-item label="记忆类型">{{
            memoryKind(detail.memory?.kind)
          }}</el-descriptions-item
          ><el-descriptions-item label="生命周期">{{
            statusText(detail.memory?.status)
          }}</el-descriptions-item
          ><el-descriptions-item label="检索索引">{{
            projectionText(detail.memory)
          }}</el-descriptions-item
          ><el-descriptions-item label="到期时间">{{
            detail.memory?.expires_at ? formatTime(detail.memory.expires_at) : '未设置或未返回'
          }}</el-descriptions-item></el-descriptions
        >
        <h3>记忆全文</h3><div class="body content">{{ memoryText(detail) }}</div>
        <p class="note"
          >保存成功与可检索分别以处理回执为准。版本
          {{ detail.memory?.ref?.version ?? detail.memory?.revision ?? '未返回' }}。</p
        >
        <h3>来源内容</h3>
        <template v-if="sectionItems(detail.sources).length"
          ><article v-for="(source, i) in sectionItems(detail.sources)" :key="i" class="turn"
            ><strong>{{ source.title || source.name || `来源 ${Number(i) + 1}` }}</strong
            ><div class="body">{{ memoryText(source) }}</div
            ><p class="note">{{
              source.source?.locator || source.location || source.uri || '来源位置未返回'
            }}</p></article
          ></template
        >
        <p v-else class="note">{{ observation(detail.sources) }}；该历史记录可能未保存来源关联。</p>
        <p v-if="detail.sources_truncated" class="note">本次只返回前 8 个来源，不代表全部来源。</p>
        <el-space wrap
          ><el-button
            v-for="(id, i) in detail.processing?.derived_memory_ids || []"
            :key="id"
            @click="openLinked('memories', id)"
            >查看提炼产物 {{ Number(i) + 1 }}</el-button
          ></el-space
        >
        <EvidencePanel title="处理与检索索引" :data="detail.processing" />
        <EvidencePanel title="生命周期与存储维护" :data="detail.placement" />
      </template>
      <template v-else-if="detailType === 'recalls'">
        <el-alert :title="recallEvidence(detail)" type="info" :closable="false" />
        <EvidencePanel title="召回执行阶段" :data="detail.record" />
        <p v-if="detail.result?.rendered_context != null" class="note"
          >服务已返回组装后的上下文。该回执不证明内容已发送给模型或被模型采用。</p
        >
        <p v-if="detail.result?.degradation_reasons?.length" class="note"
          >降级原因：{{
            detail.result.degradation_reasons
              .map((reason: string) => readable(reason, '部分检索证据不可用，错误码见技术详情'))
              .join('；')
          }}</p
        >
        <article v-for="(item, i) in recallRows" :key="i" class="turn"
          ><h3>检索返回 {{ Number(i) + 1 }}</h3
          ><div class="body">{{ memoryText(item) }}</div
          ><el-button
            v-if="memoryId(item)"
            link
            type="primary"
            @click="openLinked('memories', memoryId(item))"
            >查看记忆和来源</el-button
          ></article
        >
        <EvidencePanel title="召回范围、降级与上下文证据" :data="detail.result || detail" />
      </template>
      <template v-else
        ><p>{{ taskOutcome(detail) }}</p
        ><EvidencePanel title="业务任务" :data="detail.task || detail" /><EvidencePanel
          title="执行进度"
          :data="detail.progress" /><EvidencePanel title="工作流状态" :data="detail.workflow"
      /></template>
      <el-collapse v-if="!detailLoading && !detailError"
        ><el-collapse-item title="技术详情（编号与原始回执）" name="raw">
          <pre>{{ JSON.stringify(detail, null, 2) }}</pre>
        </el-collapse-item></el-collapse
      >
    </el-drawer>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getConsole } from '@/api/aether'
import ResourcePanel from '../ResourcePanel.vue'
import EvidencePanel from '../EvidencePanel.vue'
import {
  errorMessage,
  memoryKind,
  memoryText,
  memoryId,
  projectionText,
  observation,
  recallEvidence,
  recallItems,
  sectionItems,
  targetQuery,
  taskOutcome
} from '../console.mjs'
import { formatTime, readable, statusText } from '../presentation.mjs'
defineOptions({ name: 'AetherBusiness' })
const route = useRoute()
const topTab = ref(route.query.tab === 'requests' ? 'requests' : 'users'),
  query = ref(''),
  users = ref<any>({}),
  usersError = ref(''),
  usersLoading = ref(false),
  userPage = ref(1)
const tenant = ref(''),
  tenantChoices = ref<any[]>([])
const selected = ref<any>(null),
  userTab = ref('conversations'),
  records = ref<any>({}),
  listLoading = ref(false),
  listError = ref(''),
  recordPage = ref(1),
  cursors = ref<any[]>([undefined])
const kindFilter = ref(''),
  memoryState = ref(''),
  conversationState = ref(''),
  dateRange = ref<[Date, Date] | null>(null)
function filterRecords() {
  recordPage.value = 1
  cursors.value = [undefined]
  detailOpen.value = false
  clearDetail()
  loadUserData()
}
const detailOpen = ref(false),
  detailLoading = ref(false),
  detailError = ref(''),
  detail = ref<any>({}),
  detailType = ref(''),
  detailTitle = ref('业务详情')
const turnPage = ref(1),
  detailId = ref('')
const detailTrail = ref<any[]>([])
let userRequest = 0,
  listRequest = 0,
  detailRequest = 0
const hasNext = computed(() =>
  userTab.value === 'memories'
    ? !!records.value.next_cursor
    : records.value.total != null && recordPage.value * 20 < records.value.total
)
const recallRows = computed(() => recallItems(detail.value))
async function loadUsers() {
  const request = ++userRequest
  usersLoading.value = true
  usersError.value = ''
  users.value = {}
  try {
    const response = await getConsole('users', {
      q: query.value.trim(),
      tenant_id: tenant.value || undefined,
      limit: 20,
      offset: (userPage.value - 1) * 20
    })
    if (request === userRequest) {
      users.value = response
      tenantChoices.value = response.tenant_choices || []
    }
  } catch (e) {
    if (request === userRequest) {
      usersError.value = errorMessage(e)
      users.value = { status: 'unavailable' }
    }
  } finally {
    if (request === userRequest) usersLoading.value = false
  }
}
function changeUserPage(delta: number) {
  userPage.value += delta
  loadUsers()
}
function changeTurnPage(delta: number) {
  turnPage.value += delta
  loadDetail()
}
function searchUsers() {
  listRequest++
  selected.value = null
  records.value = {}
  detailOpen.value = false
  clearDetail()
  userPage.value = 1
  loadUsers()
}
function selectUser(user: any) {
  selected.value = user
  detailOpen.value = false
  clearDetail()
  recordPage.value = 1
  cursors.value = [undefined]
  loadUserData()
}
async function loadUserData(tab?: any) {
  if (!selected.value) return
  if (typeof tab === 'string') {
    recordPage.value = 1
    cursors.value = [undefined]
  }
  const request = ++listRequest
  records.value = {}
  listLoading.value = true
  listError.value = ''
  try {
    const paging =
      userTab.value === 'memories'
        ? { limit: 20, cursor: cursors.value[recordPage.value - 1] }
        : { limit: 20, offset: (recordPage.value - 1) * 20 }
    const filters =
      userTab.value === 'memories'
        ? { kind: kindFilter.value || undefined, status: memoryState.value || undefined }
        : {
            status: conversationState.value || undefined,
            from: dateRange.value?.[0]?.toISOString(),
            to: dateRange.value?.[1]?.toISOString()
          }
    const response = await getConsole(
      userTab.value,
      targetQuery(selected.value.id, { ...paging, ...filters })
    )
    if (request === listRequest) records.value = response
  } catch (e) {
    if (request === listRequest) {
      listError.value = errorMessage(e)
      records.value = { status: 'unavailable' }
    }
  } finally {
    if (request === listRequest) listLoading.value = false
  }
}
function nextRecords() {
  cursors.value[recordPage.value] = records.value.next_cursor
  recordPage.value++
  loadUserData()
}
function previousRecords() {
  recordPage.value--
  loadUserData()
}
function clearDetail() {
  detailRequest++
  detail.value = {}
  detailError.value = ''
  detailLoading.value = false
}
function openRecord(row: any) {
  detailTrail.value = []
  detailOpen.value = false
  openLinked(userTab.value, memoryId(row))
}
async function openLinked(type: string, id: string) {
  if (detailOpen.value && detailId.value)
    detailTrail.value.push({
      type: detailType.value,
      id: detailId.value,
      page: turnPage.value,
      title: detailTitle.value
    })
  turnPage.value = 1
  detailId.value = id
  clearDetail()
  detailType.value = type
  detailTitle.value = (
    {
      conversations: '会话内容与处理过程',
      memories: '记忆内容、来源与生命周期',
      recalls: '本次召回证据',
      tasks: '关联任务与执行进度'
    } as any
  )[type]
  detailOpen.value = true
  await loadDetail()
}
function backDetail() {
  const previous = detailTrail.value.pop()
  if (!previous) return
  detailType.value = previous.type
  detailId.value = previous.id
  turnPage.value = previous.page
  detailTitle.value = previous.title
  loadDetail()
}
async function loadDetail() {
  const request = ++detailRequest
  detail.value = {}
  detailError.value = ''
  detailLoading.value = true
  try {
    const response = await getConsole(
      `${detailType.value}/${encodeURIComponent(detailId.value)}`,
      targetQuery(
        selected.value.id,
        detailType.value === 'conversations' ? { limit: 20, offset: (turnPage.value - 1) * 20 } : {}
      )
    )
    if (request === detailRequest) detail.value = response
  } catch (e) {
    if (request === detailRequest) detailError.value = errorMessage(e)
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}
onMounted(loadUsers)
onBeforeUnmount(() => {
  userRequest++
  listRequest++
  clearDetail()
  users.value = {}
  records.value = {}
  selected.value = null
})
</script>
<style scoped>
h1 {
  margin: 0;
  font-size: 26px;
}
h2 {
  font-size: 21px;
}
h2 small {
  font-size: 13px;
  font-weight: 400;
}
h4 {
  margin-bottom: 8px;
}
.heading p,
.note,
.user span,
.user small {
  color: var(--el-text-color-secondary);
  line-height: 1.65;
}
.search {
  display: flex;
  gap: 12px;
  max-width: 850px;
  margin: 12px 0 20px;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.filters .el-select {
  width: 180px;
}
.workspace {
  display: grid;
  grid-template-columns: 245px minmax(0, 1fr);
  gap: 24px;
}
aside {
  border-right: 1px solid var(--el-border-color-lighter);
  padding-right: 20px;
}
.user {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  width: 100%;
  padding: 14px;
  margin: 10px 0;
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-primary);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  cursor: pointer;
}
.user:hover,
.user.selected {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.pager,
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 16px 0;
  flex-wrap: wrap;
}
.text-link {
  white-space: normal;
  text-align: left;
  height: auto;
  line-height: 1.6;
}
.body,
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
}
.content {
  background: var(--el-fill-color-light);
  padding: 20px;
  border-radius: 8px;
}
.turn {
  padding: 12px 0 24px;
  border-bottom: 1px solid var(--el-border-color);
}
@media (max-width: 800px) {
  .workspace {
    grid-template-columns: 1fr;
  }
  aside {
    border: 0;
  }
}
</style>
