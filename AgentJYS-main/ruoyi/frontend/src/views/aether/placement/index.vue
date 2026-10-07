<template>
  <ContentWrap>
    <el-result
      v-if="denied"
      icon="info"
      title="冷热分层调度仅供平台管理员查看"
      sub-title="企业账号可在用户与业务中查看本企业的记忆。"
    />
    <template v-else>
      <div class="heading">
        <div><h1>冷热分层调度</h1></div>
        <el-button type="primary" :loading="loading" @click="reset">刷新动作记录</el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-alert
        v-if="data.placement_capability === 'unsupported'"
        title="当前存储执行器不支持层级迁移，普通分层评估已停止"
        type="warning"
        :closable="false"
        show-icon
        class="capability"
      />
      <p class="muted">最近更新 {{ formatTime(data.observed_at) }}</p>
      <div class="cards">
        <el-card v-for="card in cards" :key="card.label" shadow="never">
          <span class="muted">{{ card.label }}</span
          ><strong>{{ card.value }}</strong>
        </el-card>
      </div>
      <div class="toolbar">
        <el-select
          v-model="filter"
          placeholder="全部执行结果"
          clearable
          aria-label="按执行结果筛选"
          @change="reset"
        >
          <el-option
            v-for="(label, value) in resultLabels"
            :key="value"
            :label="label"
            :value="value"
          />
        </el-select>
        <span class="muted">{{
          data.matching == null ? '尚未读取记录' : `符合条件 ${data.matching} 条，每页 20 条`
        }}</span>
      </div>
      <el-table
        :data="data.items || []"
        v-loading="loading"
        :empty-text="emptyText"
        row-key="action_id"
      >
        <el-table-column label="发起时间" min-width="170"
          ><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column
        >
        <el-table-column label="所属企业 / 用户" min-width="155"
          ><template #default="{ row }"
            ><div>{{ row.tenant_name || '企业名称未匹配' }}</div
            ><span class="muted">{{ row.user_name || '用户名称未匹配' }}</span></template
          ></el-table-column
        >
        <el-table-column label="为什么触发" min-width="285"
          ><template #default="{ row }"
            ><div>{{ triggerText(row.trigger) }}</div
            ><p class="reason">{{ decisionText(row) }}</p></template
          ></el-table-column
        >
        <el-table-column label="原层级 → 目标层级" min-width="170"
          ><template #default="{ row }"
            >{{ tierText(row.current_tier) }} → {{ tierText(row.target_tier) }}</template
          ></el-table-column
        >
        <el-table-column label="是否成功" min-width="175"
          ><template #default="{ row }"
            ><el-tag :type="resultType(row.result)">{{ resultText(row.result) }}</el-tag
            ><p class="muted">{{
              row.provider_mode === 'real'
                ? '真实存储执行'
                : row.provider_mode === 'simulated'
                  ? '模拟执行'
                  : '执行方式未记录'
            }}</p></template
          ></el-table-column
        >
        <el-table-column label="操作" width="100" fixed="right"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="selected = row">查看记录</el-button></template
          ></el-table-column
        >
      </el-table>
      <div class="pagination"
        ><el-button :disabled="loading || cursors.length === 1" @click="previous">上一页</el-button
        ><span>第 {{ cursors.length }} 页</span
        ><el-button :disabled="loading || !data.next_cursor" @click="next">下一页</el-button></div
      >
      <el-drawer
        :model-value="Boolean(selected)"
        title="分层动作记录"
        size="600px"
        @close="selected = null"
      >
        <template v-if="selected">
          <h2>{{ tierText(selected.current_tier) }} → {{ tierText(selected.target_tier) }}</h2>
          <el-tag :type="resultType(selected.result)">{{ resultText(selected.result) }}</el-tag>
          <h3>为什么发起这次调度</h3><p>{{ triggerText(selected.trigger) }}</p
          ><p>{{ decisionText(selected) }}</p>
          <h3>执行结果</h3>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="发起时间">{{
              formatTime(selected.created_at)
            }}</el-descriptions-item>
            <el-descriptions-item label="最近回执时间">{{
              formatTime(selected.observed_at)
            }}</el-descriptions-item>
            <el-descriptions-item label="发起至结果确认">{{
              durationText(selected.duration_seconds)
            }}</el-descriptions-item>
            <el-descriptions-item label="确认到达层级">{{
              selected.verified_tier ? tierText(selected.verified_tier) : '尚未确认到达目标层'
            }}</el-descriptions-item>
            <el-descriptions-item label="迁移后读取验证">{{
              selected.read_verified
                ? selected.provider_mode === 'real'
                  ? '通过真实读取验证'
                  : '仅通过模拟验证'
                : '尚无完整的读取证明'
            }}</el-descriptions-item>
            <el-descriptions-item label="旧副本清理">{{
              cleanupText(selected.cleanup_state)
            }}</el-descriptions-item>
            <el-descriptions-item label="记忆编号"
              >{{ selected.memory_id || '未记录' }} · 第
              {{ selected.memory_version ?? '未知' }} 版</el-descriptions-item
            >
          </el-descriptions>
          <p v-if="selected.result === 'failed'" class="muted"
            >存储动作已报告失败。可进入任务处理查看原任务的失败步骤和恢复情况。</p
          >
          <p v-if="selected.result === 'unconfirmed'" class="muted"
            >尚无完整证据确认最终结果，不能认定成功，也不能直接认定失败。</p
          >
          <el-button
            class="task-link"
            @click="$router.push({ path: '/aether/tasks', query: { task_id: selected.task_id } })"
            >查看关联任务</el-button
          >
        </template>
      </el-drawer>
    </template>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getConsole, getIdentity } from '@/api/aether'
import { readTaskDiagnostics } from '../taskAccess.mjs'
import { errorMessage } from '../console.mjs'
import { formatTime } from '../presentation.mjs'
import {
  tierText,
  triggerText,
  decisionText,
  resultText,
  resultType,
  resultLabels,
  durationText,
  cleanupText
} from '../placement.mjs'

defineOptions({ name: 'AetherPlacement' })
const data = ref<any>({}),
  loading = ref(false),
  denied = ref(false),
  error = ref('')
const selected = ref<any>(null),
  filter = ref(''),
  cursors = ref<(string | undefined)[]>([undefined])
let sequence = 0
const cards = computed(() => {
  const counts = data.value.by_result
  const count = (...keys: string[]) =>
    counts ? keys.reduce((n, key) => n + (counts[key] || 0), 0) : '—'
  return [
    { label: '累计分层动作', value: data.value.total ?? '—', note: '不包含仅做判断的策略评估' },
    { label: '真实执行成功', value: count('succeeded'), note: '已验证到达目标层并可读' },
    { label: '执行失败', value: count('failed'), note: '动作记录已明确报告失败' },
    {
      label: '待完成或待确认',
      value: count('pending', 'running', 'unconfirmed'),
      note: '等待提交、执行中或证据不足'
    }
  ]
})
const emptyText = computed(() =>
  loading.value
    ? '正在读取分层动作…'
    : error.value
      ? '读取失败，请重试'
      : !data.value.observed_at
        ? '尚未取得动作记录'
        : filter.value
          ? '没有符合筛选条件的动作'
          : '当前部署尚无已记录的冷热迁移动作'
)
async function load() {
  const request = ++sequence
  loading.value = true
  error.value = ''
  selected.value = null
  data.value = {}
  try {
    const result = await readTaskDiagnostics(getIdentity, getConsole, {
      kind: 'placement',
      limit: 20,
      cursor: cursors.value.at(-1),
      status: filter.value || undefined
    })
    if (request === sequence) {
      data.value = result
      denied.value = false
    }
  } catch (e: any) {
    if (request === sequence) {
      denied.value = Number(e?.code || e?.status) === 403
      error.value = errorMessage(e)
    }
  } finally {
    if (request === sequence) loading.value = false
  }
}
function reset() {
  cursors.value = [undefined]
  load()
}
function next() {
  if (data.value.next_cursor) {
    cursors.value.push(data.value.next_cursor)
    load()
  }
}
function previous() {
  if (cursors.value.length > 1) {
    cursors.value.pop()
    load()
  }
}
onMounted(load)
onBeforeUnmount(() => {
  sequence++
})
</script>
<style scoped>
.heading,
.toolbar,
.pagination {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.heading {
  justify-content: space-between;
}
h1 {
  font-size: 26px;
  margin: 0;
}
.heading p,
.muted,
.reason {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
.muted,
.reason {
  font-size: 13px;
}
.reason {
  margin: 6px 0;
}
.cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 20px 0;
}
.cards strong {
  display: block;
  font-size: 30px;
  margin: 14px 0;
}
.cards p {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.toolbar,
.pagination {
  margin: 20px 0;
}
.toolbar .el-select {
  width: 230px;
}
.pagination {
  justify-content: flex-end;
}
.task-link {
  margin-top: 20px;
}
.capability {
  margin-top: 16px;
}
@media (max-width: 900px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
