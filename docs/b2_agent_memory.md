# B2 · Agent 记忆管理器

> **委托方（甲方）：** 南京捷云盛软件科技有限公司
> **受托方（乙方）：** 南京信息工程大学（教授 C 课题组）
> **所属：** AetherStore → P3 AetherBrain（语义层与智能分层调度）
> **整体周期：** 2026-06-01 → 2027-06-30（13 个月，5 个里程碑）
> **主语言：** Python + Rust

---

## 一、项目定位

B2 是 P3 AetherBrain 的三个子模块之一，负责为 LLM Agent 提供三层记忆（Working / Episodic / Semantic）的统一管理。

### 1.1 P3 子模块关系

| 子模块 | 功能 | B2 与它的交互 |
| --- | --- | --- |
| B1 · Embedding 下推 Sidecar | 在存储侧执行向量化（CPU+SIMD，ONNX Runtime） | B2 调用 B1 将记忆文本向量化后存入 Episodic/Semantic |
| **B2 · Agent 记忆管理器** | Working / Episodic / Semantic 三层记忆 | **当前项目** |
| B3 · 语义智能分层调度器 | 多维感知调度（启发式→Bandit→Deep RL） | B3 根据推理热度等信号，将 B2 的记忆数据在五级介质间调度 |

### 1.2 系统架构中的位置

```
P4 · 应用接入       AI 网关 / SDK / 控制台
                 ↕ IF-06 AI 原语 SDK（Memory 部分）
P3 · 语义层        B1 ← B2 Agent 记忆管理器 → B3 智能分层调度
                 ↕ IF-04              ↕ IF-03 Tiering Hook
P2 · 引擎层        向量引擎 E1 / 对象引擎 E2  ↕ Storage Block API
P1 · HCI 底座      五级介质池（L0 DRAM / L1 NVMe / L2 HDD / L3 对象 / L4 归档）
-----------------------------------------------------------------------
硬件               通用 x86_64 / ARM64 · 200Gb RDMA · 3 节点起步
```

### 1.3 团队与论文

| 属性 | 说明 |
| --- | --- |
| 牵头 | 教授 C 课题组（AI / 强化学习方向） |
| 团队规模 | MVP 5-6 人 → GA 8-9 人（含 3-5 硕 + 2 博） |
| 对标系统 | MemGPT / LangChain Memory |
| 论文方向 | 遗忘曲线压缩、多模态索引（NeurIPS / ACL） |

---

## 二、三层记忆模型

### 2.1 记忆层级

| 层级 | 功能 | 存储位置 | 生命周期 | MVP 性能指标 |
| --- | --- | --- | --- | --- |
| **Working Memory** | Agent 会话上下文的即时键值存取 | DRAM（L0） | 会话粒度，TTL 自动过期 | P99 读写 < 1ms |
| **Episodic Memory** | 对话历史的向量化存储，支持跨会话检索 | L1 NVMe，后台压缩后下沉 L3 对象存储 | 按 Ebbinghaus 遗忘曲线衰减 | 跨会话检索准确率 ≥ 80% |
| **Semantic Memory** | Token 级语义蒸馏后的长期知识压缩存储 | 待定 | 长期持久化 | 压缩比 ≥ 10x，回忆准确率不降 |

### 2.2 端到端记忆处理流程

```
Agent 会话
    │
    ├── 即时读写 → Working Memory (DRAM, P99<1ms)
    │       │
    │       └── 会话结束 → 异步归档
    │
    └── 异步归档
            │
            ├── 拦截 → 异步提纯 → 调用 B1 向量化
            │
            ├── 双库分流
            │   ├── 短期 → Episodic Memory (L1, TTL 7 天)
            │   │     ├── Ebbinghaus 自动衰减
            │   │     └── 后台压缩 → 下沉 L3
            │   └── 长期 → Semantic Memory (Token 级语义蒸馏)
            │
            └── 检索时 → 带时间衰减的联合检索
                    (Working + Episodic + Semantic 加权合并)
```

### 2.3 核心算法

| 算法 | 作用 | 交付里程碑 |
| --- | --- | --- |
| TTL 自动过期 | Working Memory 基于时间窗口自动淘汰 | MVP |
| Ebbinghaus 自动衰减 | Episodic Memory 基于遗忘曲线（先快后慢）自动降权/压缩 | M1 |
| Token 级语义蒸馏 | 将长对话压缩为摘要表示，降低物理存储 | M1 |
| 个性化遗忘曲线 | 按 Agent 聚类，学习差异化衰减策略 | M2 |

---

## 三、与兄弟模块的交互

### 3.1 B2 → B1（Embedding 下推 Sidecar）

B2 自身不实现 Embedding，所有向量化能力由 B1 提供：

| B2 操作 | B1 动作 | 数据流向 |
| --- | --- | --- |
| Episodic Memory 写入 | 将对话文本向量化 | 文本 → B1（ONNX Runtime + SIMD）→ 向量 → 向量引擎 E1 |
| Episodic Memory 检索 | 将查询向量化 | 查询文本 → B1 → 向量 → E1 ANN 检索 |
| Semantic Memory 构建 | Token 级向量化 | 长文本 → B1 → 压缩向量 → E1 |

### 3.2 B2 → B3（智能分层调度器）

B3 通过 IF-03 Tiering Hook 控制 B2 记忆数据的介质迁移：

| B2 记忆类型 | B3 调度策略 |
| --- | --- |
| Working Memory | 固定置顶 L0（DRAM），B3 不参与调度 |
| Episodic Memory | 根据「推理热度 + 语义亲近度」决定保留 L1 还是下沉 L3 |
| Semantic Memory | 根据 Task-Aware 成本决策存储层级 |

B3 通过 IF-04 Segment 内省接口读取 B2 的记忆数据状态（list / stats / freeze）。

### 3.3 B2 → P4（网关 / SDK）

P4 通过 IF-06 AI 原语 SDK（Memory 部分）将 B2 的能力暴露给外部：

| P4 模块 | B2 能力对接 |
| --- | --- |
| G2 Python SDK | 封装 Memory 原语，供 LangChain Retriever / LlamaIndex VectorStore 调用 |
| G2 Go/TS SDK | M1 起提供 |
| G4 OTel 全链路 | 每条记忆操作生成 Trace/Span，Jager 可视化，贯穿网关→引擎→介质 |
| OpenAI Function-Calling | Agent 通过工具调用方式访问记忆管理接口 |

---

## 四、里程碑与交付物

| 里程碑 | 时间窗 | B2 交付物 |
| --- | --- | --- |
| **M0** 接口冲刺 | 2026-06 | IF-06 AI 原语 SDK（Memory 部分）接口冻结 v0.1；trace 收集与脱敏方案 |
| **M-MVP** 首版可用 | 2026-09-30 | Working Memory（TTL+键值，DRAM）；Episodic Memory v1（向量化落地）；端到端记忆服务可运行 |
| **M1** 迭代一 | 2026-12-30 | Ebbinghaus 自动衰减 v1；Episodic → L3 后台压缩；Token 级语义蒸馏 |
| **M2** 迭代二 | 2027-03-30 | 个性化遗忘曲线（按 Agent 聚类） |
| **GA** 成品上市 | 2027-06-30 | 长期记忆压缩比 ≥ 10x，回忆准确率不降；开源 trace 数据集（业界首个 LLM Agent 存储 trace） |

同一时期 P3 兄弟模块的交付物（B2 运行依赖）：

| 里程碑 | B1 交付物 | B3 交付物 |
| --- | --- | --- |
| M-MVP | CPU+SIMD Sidecar，BGE-small ≥ 4k QPS | Phase 1 启发式调度器（30s 决策周期） |
| M1 | ONNX+OpenVINO，BGE-large | Phase 2 Contextual Bandit（A/B 10%） |
| M2 | 可选 GPU 加速（CLIP/ImageBind） | Phase 3 Deep RL+GNN 预取（A/B 50%） |
| GA | 多模态下推 | RL 全量上线 + 一键回退 |

---

## 五、验收标准

### 5.1 MVP 验收（2026-09-30）

| 验收项 | 量化标准 | 测试条件 | 测试方式 |
| --- | --- | --- | --- |
| Working Memory 读写 | P99 < 1ms，TTL 自动过期 | DRAM / Redis | 性能基线（AetherBench） |
| Episodic Memory v1 | 对话历史向量化后可跨会话检索，准确率 ≥ 80% | RAG 调用数据集 | 场景 + 准确率 |
| 端到端记忆服务 | 对话→拦截→异步提纯→向量化→双库分流→带时间衰减联合检索，全链路可运行 | MVP 全环境 | 集成测试 |

### 5.2 迭代验收

| 验收项 | 里程碑 | 量化标准 | 测试方式 |
| --- | --- | --- | --- |
| 长期记忆压缩 | M1 | Token 级语义蒸馏，物理压缩 ≥ 5x | 长文本对话数据集 |
| Bandit 调度下的记忆命中率 | M1 | 配合 B3 Bandit，命中率较启发式 +10%（硬指标） | 在线 A/B 对比 |
| 个性化遗忘曲线 | M2 | 按 Agent 聚类，衰减精度可调 | 多 Agent 场景对比 |
| 长期记忆压缩比 | GA | ≥ 10x，回忆准确率不降 | 性能基线 |
| 底座对接 | GA | 长时记忆对接 AetherEngine E1；完成 Milvus→E1 对比迁移 | 集成 + 对比 |

---

## 六、MVP 验收场景（B2 参与部分）

### 场景 1 · RAG 知识库

- LangChain Retriever 接入，向量库存 1 千万条文档块
- 写入吞吐 ≥ 1k docs/s，端到端检索 P99 < 30ms
- **LLM 调用结束后自动写一条 Episodic Memory，TTL 7 天**

### 场景 2 · Agent 短期记忆

- Agent 框架（如 LangGraph）把会话上下文写入 **Working Memory**，P99 < 1ms
- **会话结束后异步归档为 Episodic Memory**

---

## 七、关键接口

| 编号 | 接口 | Owner | B2 角色 | 说明 |
| --- | --- | --- | --- | --- |
| IF-06 | AI 原语 SDK（Memory 部分） | P3 | B2 实现，P4 消费 | Working/Episodic/Semantic 读写原语的对外接口 |
| IF-03 | Tiering Hook（promote/demote/pin/hint） | P1 | B3 消费（间接触发 B2 数据迁移） | B3 通过此接口管控介质分层 |
| IF-04 | Segment 内省（list/stats/freeze） | P2 | B3 消费（读取 B2 存储段状态） | B3 通过此接口获取 B2 数据在存储引擎中的段信息 |
| IF-07 | 全链路 OTel 标准 | P4 | B2 遵循 | 记忆操作的 Trace/Span 规范，Jager 可视化 |
| IF-08 | 多租命名空间 + RBAC | P4 | B2 遵循 | 记忆数据按 Agent/租户隔离 |

---

## 八、测试资料框架

编号规则：TC-P3-{里程碑}-{序号} / TR-P3-{里程碑}。B2 相关验收项至少关联 1 条用例与 1 份报告。

### 测试用例登记表（模板）

| 字段 | 说明 |
| --- | --- |
| 用例编号 | TC-P3-{里程碑}-{序号} |
| 关联验收项 | 对应 B2 验收表中的验收项 |
| 测试类型 | 单元 / 集成 / 性能基线 / 场景 |
| 前置条件 | 环境、数据集、配置 |
| 测试步骤 | — |
| 预期结果 | 对应量化验收口径 |
| 实测结果 | 测试落地阶段填写 |
| 结论 | 通过 / 失败 |

### 测试报告字段

| 字段 | 说明 |
| --- | --- |
| 报告编号 / 名称 | TR-P3-{里程碑} |
| 对应阶段 / 里程碑 | — |
| 测试范围 / 关联验收项 | — |
| 测试环境（硬件 / 数据集 / 并发） | 引用 B2 验收测试条件 |
| 执行人 / 评审人 / 日期 | — |
| 用例总数 / 通过 / 失败 | — |
| 关键指标实测值 | 对应量化验收口径 |
| 缺陷统计（致命 / 严重 / 一般） | — |
| 验收结论 | 通过 / 不通过 |

---

## 九、待确认事项

| 编号 | 事项 | 当前共识 |
| --- | --- | --- |
| A3 | B2 长时记忆底座：对接自研 AetherEngine（E1）还是用 Milvus 过渡 | 按共识对接 E1；MVP 如需临时用 Redis/Milvus，须经抽象接口隔离，并在迭代阶段完成对比迁移 |
| A4 | 硬件基线、数据集（含真实学科脱敏数据）、并发口径 | 待填入验收表「测试条件」列并冻结 |
| A5 | 应用验证与学校背书（水文/卫星云图/地质等真实系统） | 作为各阶段最终验收附加项纳入 |
