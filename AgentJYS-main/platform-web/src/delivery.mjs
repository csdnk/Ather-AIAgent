export function submission(previous, conversationId, content, turnId, uuid, attachments = []) {
  const refs = attachments.map(({upload_id, name}) => ({upload_id, name}));
  if (previous) {
    if (previous.conversationId !== conversationId || previous.content !== content || JSON.stringify(previous.attachments || []) !== JSON.stringify(refs)) {
      throw new Error('上一条消息的发送状态尚未确认，请先刷新对话或重试原消息。');
    }
    return previous;
  }
  return {conversationId, content, turnId: turnId || uuid(), attachments: refs};
}

export function pollDelay(error, failures) {
  if ([401, 403].includes(error.status) || failures > 5) return null;
  return Math.min(1000 * 2 ** (failures - 1), 8000);
}

export function nextUpdate(messages) {
  if(messages.some(m=>m.status==='pending'&&m.phase==='generating'))return 250;
  if(messages.some(m=>m.status==='pending'))return 750;
  if(messages.some(m=>['save_queued','saving'].includes(m.memory_status)))return 2000;
  return null;
}

export function replyPhase(message) {
  return message.phase==='generating'?(message.content?'正在生成…':'正在等待模型响应…'):'正在检索记忆…';
}
