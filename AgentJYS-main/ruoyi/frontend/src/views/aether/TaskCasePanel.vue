<template>
  <section v-loading="loading" class="case-panel">
    <div class="case-heading"
      ><h3>问题处置</h3><el-button @click="load">刷新处置记录</el-button></div
    >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="pending"
      title="上次提交尚未确认，请查询原操作结果。"
      type="warning"
      :closable="false"
    >
      <el-button @click="recover">查询上次提交</el-button>
      <el-button v-if="notReceived" @click="resend">继续提交原操作</el-button>
    </el-alert>
    <template v-if="record">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="处理阶段">{{ caseState(record.state) }}</el-descriptions-item>
        <el-descriptions-item label="负责人">{{ record.owner_name }}</el-descriptions-item>
        <el-descriptions-item label="处理结果" :span="2">{{
          resolutionName(record.resolution?.kind)
        }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="record.diagnosis" class="section">
        <h4>最近检查 · {{ formatTime(record.diagnosis.at) }}</h4>
        <el-table :data="record.diagnosis.checks"
          ><el-table-column prop="name" label="检查项" width="150" /><el-table-column
            label="检查结果"
            ><template #default="{ row }">{{ readable(row.result) }}</template></el-table-column
          ></el-table
        >
      </div>
      <el-alert
        v-if="record.control"
        :title="`${caseAction(record.control.action)}：${statusText(record.control.status)}`"
        type="info"
        :closable="false"
        class="section"
      />
      <el-alert
        v-if="record.verification"
        :title="`${record.verification.passed ? '核验通过' : '核验未通过'}：${record.verification.reason}`"
        :type="record.verification.passed ? 'success' : 'warning'"
        :closable="false"
        class="section"
      />
      <p
        v-if="
          record.state === 'awaiting_verification' && record.resolution?.kind === 'no_longer_needed'
        "
        >人工结案需另一名有处置权限的管理员复核原结果和结案依据。</p
      >
    </template>
    <p v-else-if="!loading && !error">此任务尚未建立处置工单。</p>
    <el-space wrap class="section">
      <el-button
        v-for="action in actions"
        :key="action"
        :disabled="!!pending || submitting"
        :type="
          ['open', 'claim', 'resolve', 'verify', 'close'].includes(action) ? 'primary' : 'default'
        "
        @click="choose(action)"
        >{{ caseAction(action) }}</el-button
      >
    </el-space>
    <template v-if="record?.history?.length">
      <h4>处理记录</h4>
      <el-timeline
        ><el-timeline-item
          v-for="(event, index) in [...record.history].reverse()"
          :key="index"
          :timestamp="formatTime(event.at)"
        >
          <strong>{{ caseAction(event.action) }} · {{ event.actor_name }}</strong>
          <p v-if="event.note">{{ event.note }}</p
          ><p v-if="event.result">{{ readable(event.result) }}</p>
        </el-timeline-item></el-timeline
      >
    </template>
    <el-dialog v-model="dialog" :title="caseAction(selectedAction)" width="620px" append-to-body>
      <el-form label-position="top">
        <el-form-item v-if="selectedAction === 'resolve'" label="处理结果">
          <el-select v-model="resolution" @change="loadCandidates"
            ><el-option
              v-for="kind in ['original_success', 'replacement', 'no_longer_needed']"
              :key="kind"
              :value="kind"
              :label="resolutionName(kind)"
          /></el-select>
        </el-form-item>
        <el-form-item
          v-if="selectedAction === 'resolve' && resolution === 'replacement'"
          label="选择同一业务对象的后续任务"
        >
          <el-select
            v-model="replacement"
            :loading="candidateLoading"
            filterable
            placeholder="选择已成功的后续任务"
            style="width: 100%"
            ><el-option
              v-for="candidate in candidates"
              :key="candidate.task_id"
              :value="candidate.task_id"
              :label="`${taskKindLabel(candidate.kind)} · ${formatTime(candidate.created_at)}`"
          /></el-select>
          <span v-if="!candidateLoading && !candidates.length"
            >本次查询没有匹配的成功任务，不能据此提交补办成功。</span
          >
          <el-button v-if="candidateCursor" link @click="loadCandidates(true)"
            >继续查找更早的任务</el-button
          >
        </el-form-item>
        <el-form-item label="处理说明"
          ><el-input
            v-model="note"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
            placeholder="说明已做了什么、检查结果和处理依据，至少 8 个字"
        /></el-form-item>
        <el-checkbox
          v-if="selectedAction === 'verify' && record?.resolution?.kind === 'no_longer_needed'"
          v-model="confirmed"
          >我已独立核对原业务结果，确认无需继续办理；这不会把原任务标记为成功。</el-checkbox
        >
        <el-alert
          v-if="['cancel', 'reconcile'].includes(selectedAction)"
          :title="
            selectedAction === 'cancel'
              ? '将向仍在运行的原任务申请取消。已经发生的业务变更不会撤销。'
              : '将核对原任务已提交的执行结果，不创建新的业务请求。'
          "
          type="warning"
          :closable="false"
        />
      </el-form>
      <template #footer
        ><el-button @click="dialog = false">返回</el-button
        ><el-button
          type="primary"
          :loading="submitting"
          :disabled="
            note.trim().length < 8 ||
            (selectedAction === 'resolve' && resolution === 'replacement' && !replacement) ||
            (selectedAction === 'verify' &&
              record?.resolution?.kind === 'no_longer_needed' &&
              !confirmed)
          "
          @click="submitForm"
          >确认提交</el-button
        ></template
      >
    </el-dialog>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getConsole, getIdentity, getOperations, sendCommand } from '@/api/aether'
import { caseActions, caseAction, caseError, caseState, resolutionName } from './taskCases.mjs'
import { createCommand } from './operations.mjs'
import { formatTime, readable, statusText } from './presentation.mjs'
import { taskKindLabel } from './dashboard.mjs'
const props = defineProps<{ taskId: string; data?: any }>()
const emit = defineEmits(['changed'])
const identity = ref<any>({}),
  record = ref<any>(null),
  loading = ref(false),
  submitting = ref(false),
  error = ref(''),
  pending = ref<any>(null),
  notReceived = ref(false)
const dialog = ref(false),
  selectedAction = ref(''),
  note = ref(''),
  resolution = ref('original_success'),
  replacement = ref(''),
  confirmed = ref(false)
const candidates = ref<any[]>([]),
  candidateLoading = ref(false),
  candidateCursor = ref<string | undefined>()
const key = computed(() => `aether-case:${identity.value.user_id}:${props.taskId}`)
const task = computed(() => record.value?.latest || props.data || {})
const actions = computed(() => caseActions(record.value, identity.value, task.value))
let seq = 0
async function load() {
  const request = ++seq
  loading.value = true
  error.value = ''
  try {
    const who = await getIdentity()
    const result = await getOperations('support', { q: 'task_cases', task_id: props.taskId })
    if (request !== seq) return
    identity.value = { ...who, case_actor_id: result.actor_id }
    record.value = result.items?.[0] || null
    try {
      pending.value = JSON.parse(localStorage.getItem(key.value) || 'null')
    } catch {
      pending.value = null
    }
  } catch (e) {
    if (request === seq) {
      record.value = null
      error.value = caseError(e)
    }
  } finally {
    if (request === seq) loading.value = false
  }
}
async function deliver(command: any) {
  submitting.value = true
  error.value = ''
  pending.value = command
  localStorage.setItem(key.value, JSON.stringify(command))
  try {
    const result = await sendCommand(command)
    if (result.status !== 'complete') throw new Error('未确认')
    localStorage.removeItem(key.value)
    pending.value = null
    dialog.value = false
    await load()
    emit('changed')
  } catch (e: any) {
    if (
      [400, 401, 403, 404, 409, 422].includes(Number(e?.code || e?.status || e?.response?.status))
    ) {
      localStorage.removeItem(key.value)
      pending.value = null
      await load()
    }
    error.value = caseError(e)
  } finally {
    submitting.value = false
  }
}
async function recover() {
  if (!pending.value) return
  try {
    const result = await getOperations('commands', { command_id: pending.value.command_id })
    const status = result.items?.[0]?.status
    if (['complete', 'failed', 'rejected'].includes(status)) {
      localStorage.removeItem(key.value)
      pending.value = null
      notReceived.value = false
      await load()
      emit('changed')
    } else error.value = '原操作仍在处理中，请继续查询。'
  } catch (e: any) {
    if (Number(e?.code || e?.status) === 404) {
      notReceived.value = true
      error.value = '服务端尚未登记这次提交，可继续提交同一操作编号。'
    } else error.value = caseError(e)
  }
}
async function resend() {
  if (pending.value) await deliver(pending.value)
}
function choose(action: string) {
  selectedAction.value = action
  note.value = ''
  confirmed.value = false
  replacement.value = ''
  resolution.value = 'original_success'
  if (['open', 'claim', 'check', 'poll', 'close'].includes(action))
    return deliver(
      createCommand('support', 'case_' + action, props.taskId, record.value?.version, {})
    )
  dialog.value = true
}
function submitForm() {
  const action = selectedAction.value
  const parameters: any = { note: note.value }
  if (action === 'resolve')
    Object.assign(parameters, {
      kind: resolution.value,
      replacement_task_id: replacement.value || undefined
    })
  if (action === 'verify') parameters.confirmed = confirmed.value
  if (['cancel', 'reconcile'].includes(action)) parameters.action = action
  return deliver(
    createCommand(
      'support',
      'case_' + (['cancel', 'reconcile'].includes(action) ? 'control' : action),
      props.taskId,
      record.value?.version,
      parameters
    )
  )
}
async function loadCandidates(more: any = false) {
  if (resolution.value !== 'replacement') return
  const append = more === true
  if (!append) {
    candidates.value = []
    candidateCursor.value = undefined
  }
  candidateLoading.value = true
  try {
    const result = await getConsole('diagnostics', {
      state: 'succeeded',
      limit: 100,
      cursor: append ? candidateCursor.value : undefined
    })
    const original = record.value?.latest || record.value?.original || task.value.task || task.value
    const subject = original.subject || {}
    candidates.value.push(
      ...(result.tasks?.items || []).filter(
        (row: any) =>
          row.task_id !== props.taskId &&
          row.kind === original.kind &&
          row.subject?.object_id === subject.object_id &&
          (row.subject?.version ?? null) === (subject.version ?? null) &&
          row.subject?.object_type === subject.object_type &&
          row.subject?.owner === subject.owner &&
          Date.parse(row.workflow?.start_time || row.created_at) >=
            Date.parse(original.workflow?.close_time) &&
          ['tenant_id', 'user_id', 'application_id', 'agent_id', 'session_id', 'task_id'].every(
            (k) => (row.subject?.scope?.[k] ?? null) === (subject.scope?.[k] ?? null)
          )
      )
    )
    candidateCursor.value = result.tasks?.next_cursor
  } catch (e) {
    error.value = caseError(e)
  } finally {
    candidateLoading.value = false
  }
}
watch(() => props.taskId, load, { immediate: true })
</script>
<style scoped>
.case-panel {
  margin: 24px 0;
  padding: 20px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}
.case-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.section {
  margin: 16px 0;
}
h3 {
  margin-top: 0;
}
p {
  line-height: 1.6;
}
</style>
