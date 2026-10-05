export async function readAttachment(file) {
  if (!/\.(txt|md|csv)$/i.test(file.name)) throw new Error('目前支持 TXT、Markdown 和 CSV 文本文件。');
  if (file.size > 100 * 1024) throw new Error('每个文件不能超过 100 KB。');
  let text;
  try { text = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer()); }
  catch { throw new Error('无法读取文件，请使用 UTF-8 编码的文本文件。'); }
  if (/[\u0000-\u0008\u000e-\u001f]/.test(text)) throw new Error('文件包含非文本内容，请选择纯文本文件。');
  if (!text.trim()) throw new Error('文件内容为空。');
  if (text.length > 11000) throw new Error('文件文本过长，请缩减至 11000 字符以内。');
  return {name: file.name, size: file.size, text};
}

export function composeMessage(input, attachments) {
  if (attachments.length > 3) throw new Error('每次最多添加 3 个文件。');
  const message = attachments.length
    ? `${input.trim() || '请阅读并概括附件内容。'}\n\n${attachments.map(file => `【附件：${file.name}】\n${file.text}\n【附件结束】`).join('\n\n')}`
    : input.trim();
  if (message.length > 12000) throw new Error('消息与附件文本合计不能超过 12000 字符，请减少内容后发送。');
  return message;
}
