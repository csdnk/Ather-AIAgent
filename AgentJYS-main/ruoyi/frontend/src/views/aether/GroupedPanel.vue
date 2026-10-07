<template>
  <ContentWrap>
    <h1>{{ config.title }}</h1>
    <el-tabs v-model="tab">
      <el-tab-pane
        v-for="item in visibleTabs"
        :key="item.resource"
        :label="item.title"
        :name="item.resource"
        lazy
      >
        <TaskCasesBoard v-if="item.resource === 'task_cases'" />
        <ResourcePanel
          v-else
          :resource="item.resource"
          :title="item.title"
          :description="item.description"
        />
      </el-tab-pane>
    </el-tabs>
  </ContentWrap>
</template>
<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { getIdentity } from '@/api/aether'
import { useRoute } from 'vue-router'
import ResourcePanel from './ResourcePanel.vue'
import TaskCasesBoard from './TaskCasesBoard.vue'
const props = defineProps<{ group: string }>()
const groups: Record<string, any> = {
  faults: {
    title: '故障处理',
    description: '从告警定位影响，记录处置过程并跟踪恢复。',
    tabs: [
      { resource: 'task_cases', title: '任务处置' },
      {
        resource: 'incidents',
        title: '告警与处置',
        description: '认领告警、记录处置说明；恢复状态以实际采样为准。'
      },
      {
        resource: 'support',
        title: '工单跟进',
        description: '跟踪问题负责人、业务影响和处理结果。'
      },
      {
        resource: 'rules',
        title: '告警规则',
        description: '维护当前已接入指标的阈值；规则未覆盖的业务仍需单独检查。'
      }
    ]
  },
  resources: {
    title: '用量与资源',
    description: '区分实测用量、配额限制与人工资源登记。当前没有完整物理存储容量采集。',
    tabs: [
      {
        resource: 'usage',
        title: '请求与模型用量',
        description: '最近 24 小时请求及已返回的模型用量。未计量的数据不视为零。'
      },
      {
        resource: 'quotas',
        title: '企业配额',
        description: '选择企业管理请求、并发和已观测模型用量上限。'
      },
      {
        resource: 'resources',
        title: '资源登记',
        description: '人工登记的服务、负责人和依赖关系；登记状态不是实时健康监控。'
      }
    ]
  },
  recovery: {
    title: '配置与恢复',
    description: '核对实际配置版本、发布登记与可恢复性。恢复完成仍需业务验收。',
    tabs: [
      {
        resource: 'configuration',
        title: '配置与发布',
        description: '分别查看平台配置登记和记忆服务实际激活版本。'
      },
      {
        resource: 'backups',
        title: '平台备份与恢复',
        description: '仅覆盖平台 PostgreSQL。P3 原文、向量及其他存储的备份与恢复未在此页验证。'
      }
    ]
  }
}
const config = computed(() => groups[props.group])
const platform = ref(false)
const visibleTabs = computed(() =>
  config.value.tabs.filter(
    (item: any) =>
      platform.value ||
      !['task_cases', 'resources', 'quotas', 'rules', 'configuration', 'backups'].includes(
        item.resource
      )
  )
)
const route = useRoute()
const tab = ref(
  config.value.tabs.some((item: any) => item.resource === route.query.tab)
    ? String(route.query.tab)
    : config.value.tabs[0].resource
)
onMounted(async () => {
  try {
    const identity = await getIdentity()
    platform.value = identity.role_codes?.includes('aether_platform_admin') || false
  } catch {
    platform.value = false
  }
  if (!visibleTabs.value.some((item: any) => item.resource === tab.value))
    tab.value = visibleTabs.value[0]?.resource || ''
})
</script>
<style scoped>
h1 {
  font-size: 26px;
  margin: 0;
}
.note {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
</style>
