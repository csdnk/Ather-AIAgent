import { computed, onBeforeUnmount, ref } from 'vue'
import { getOperations, sendCommand } from '@/api/aether'
import {
  canManageMemory,
  commandErrorText,
  commandTerminal,
  pendingReference,
  abandonEnvelope,
  recoveredPending
} from './memoryAdmin.mjs'

export function useMemoryCommand(
  identity: any,
  onSuccess: (reply: any, reference: any) => Promise<void>
) {
  const pending = ref<any>(null),
    busy = ref(false),
    message = ref('')
  let alive = true
  const storageKey = () => `aether-memory-command:${identity.value.user_id}`
  const blocked = computed(
    () => busy.value || (pending.value && !commandTerminal(pending.value.status))
  )
  function persist() {
    if (!identity.value.user_id) return
    try {
      if (pending.value && !commandTerminal(pending.value.status))
        sessionStorage.setItem(storageKey(), JSON.stringify(pendingReference(pending.value)))
      else sessionStorage.removeItem(storageKey())
    } catch {
      /* In-memory duplicate protection remains active if storage is unavailable. */
    }
  }
  function restore() {
    if (!identity.value.user_id) return
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey()) || 'null')
      if (saved?.command_id && saved?.user_id && saved?.action)
        pending.value = { ...pendingReference(saved), status: 'unknown' }
    } catch {
      message.value = '未能读取上次操作记录，请在操作记录中核实后再提交。'
    }
  }
  async function accept(response: any, reference: any) {
    if (!alive) return
    pending.value = recoveredPending(reference, response)
    persist()
    if (!pending.value) {
      message.value = '未受理请求已撤销，可以重新提交。'
      return
    }
    message.value =
      pending.value.status === 'failed' || pending.value.status === 'rejected'
        ? commandErrorText(pending.value)
        : ''
    if (pending.value.status === 'succeeded') {
      try {
        await onSuccess(response, reference)
      } catch {
        if (alive) message.value = '操作已成功，但页面刷新失败，请重新读取当前数据。'
      }
    }
  }
  async function submit(
    action: string,
    userId: string,
    parameters: any,
    targetId?: string,
    version?: number
  ) {
    // A cached sibling page may have submitted a command since this page mounted.
    if (!blocked.value) restore()
    if (blocked.value || !userId || !canManageMemory(identity.value, action)) return false
    const command = {
      command_id: crypto.randomUUID(),
      resource: 'memories',
      action,
      target_id: targetId,
      expected_version: version,
      parameters: { ...parameters, user_id: userId }
    }
    const reference = pendingReference(command)
    pending.value = { ...reference, status: 'submitting' }
    persist()
    busy.value = true
    message.value = ''
    try {
      await accept(await sendCommand(command), reference)
    } catch (error: any) {
      if (!alive) return true
      // Transport failure does not establish whether the server executed the operation.
      const code = error?.code || error?.response?.status
      pending.value = {
        ...reference,
        status: [400, 401, 403, 409, 422].includes(Number(code)) ? 'rejected' : 'unknown',
        code
      }
      message.value = commandErrorText(error)
      persist()
    } finally {
      if (alive) busy.value = false
    }
    return true
  }
  async function query() {
    if (!pending.value || busy.value) return
    const reference = pendingReference(pending.value)
    busy.value = true
    message.value = ''
    try {
      const response = await getOperations('commands', { command_id: reference.command_id })
      const row = response.items?.find(
        (item: any) => (item.command_id || item.id) === reference.command_id
      )
      if (row) await accept(row, reference)
      else if (alive) message.value = '原操作尚未返回结果，请稍后继续查询。'
    } catch (error: any) {
      if (alive) {
        if (Number(error?.code || error?.response?.status) === 404) {
          pending.value = { ...reference, status: 'unknown', unaccepted: true }
          message.value = '服务端尚未受理原请求。可以撤销这次未受理请求，再重新提交。'
        } else message.value = commandErrorText(error)
      }
    } finally {
      if (alive) busy.value = false
    }
  }
  async function abandon() {
    const envelope = abandonEnvelope(pending.value)
    if (!envelope || busy.value) return
    const reference = pendingReference(pending.value)
    busy.value = true
    message.value = ''
    try {
      await accept(await sendCommand(envelope), reference)
    } catch {
      if (alive) {
        pending.value = { ...reference, status: 'unknown' }
        message.value = '尚未确认撤销结果，请继续查询原操作，暂不能重新提交。'
        persist()
      }
    } finally {
      if (alive) busy.value = false
    }
  }
  onBeforeUnmount(() => {
    alive = false
    pending.value = null
  })
  return { pending, busy, blocked, message, restore, submit, query, abandon }
}
