import test from 'node:test';
import assert from 'node:assert/strict';
import {readAttachment, composeMessage} from '../src/attachments.mjs';

const file = (name, content) => ({name, size: new TextEncoder().encode(content).length, arrayBuffer: async () => new TextEncoder().encode(content).buffer});
test('reads UTF-8 text and includes actual contents with the user question', async () => {
  const attachment = await readAttachment(file('计划.md', '周五交付原型'));
  const message = composeMessage('什么时候交付？', [attachment]);
  assert.match(message, /什么时候交付/);
  assert.match(message, /计划.md/);
  assert.match(message, /周五交付原型/);
});
test('rejects unsupported, empty, oversized and binary files', async () => {
  await assert.rejects(readAttachment(file('a.pdf', 'PDF')), /TXT/);
  await assert.rejects(readAttachment(file('a.txt', '  ')), /为空/);
  await assert.rejects(readAttachment({...file('a.txt', 'ok'), size: 102401}), /100 KB/);
  await assert.rejects(readAttachment(file('a.txt', 'a\u0000b')), /文本/);
});
test('checks aggregate message budget and allows a file-only question', async () => {
  const attachment = await readAttachment(file('a.csv', 'name,count\nbook,7'));
  assert.match(composeMessage('', [attachment]), /请阅读/);
  assert.throws(() => composeMessage('x'.repeat(12000), [attachment]), /12000/);
  assert.throws(() => composeMessage('ok', Array(4).fill(attachment)), /3/);
});
