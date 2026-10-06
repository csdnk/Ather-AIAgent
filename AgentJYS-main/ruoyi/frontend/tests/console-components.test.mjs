import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const root = path.resolve(import.meta.dirname, '../src/views/aether')
for (const file of fs
  .readdirSync(root, { recursive: true })
  .filter((file) => file.endsWith('.vue'))) {
  test(`operator component compiles: ${file}`, () => {
    const filename = path.join(root, file)
    const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
    assert.deepEqual(
      errors.map((error) => error.message),
      []
    )
    const script = compileScript(descriptor, { id: file })
    const template = compileTemplate({
      source: descriptor.template.content,
      filename,
      id: file,
      compilerOptions: { bindingMetadata: script.bindings, expressionPlugins: ['typescript'] }
    })
    assert.deepEqual(template.errors, [])
  })
}
