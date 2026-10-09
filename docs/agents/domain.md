# Domain Documentation: Single-Context

此仓库使用**单上下文**领域文档布局。

## 术语表

**位置**: `GLOSSARY.md`（仓库根目录）

术语表定义项目中使用的关键概念、缩写和领域术语。

**格式示例**:
```markdown
# Glossary

## Term Name
Brief definition of the term and its usage in this project.

## Another Term
...
```

## 架构决策记录（ADRs）

**位置**: `docs/adr/`

ADRs 记录重要的架构决策及其上下文和后果。

**命名约定**: `NNNN-title-in-kebab-case.md`
- `NNNN`: 四位数字序号（0001, 0002, ...）
- 使用 kebab-case 标题

**模板**:
```markdown
# NNNN. Decision Title

Date: YYYY-MM-DD
Status: Proposed | Accepted | Deprecated | Superseded

## Context
What is the issue we're trying to address?

## Decision
What is the change we're making?

## Consequences
What becomes easier or harder as a result?
```

## 使用规则

Agent 技能在需要领域知识时会：
1. 首先查阅 `GLOSSARY.md` 以理解术语
2. 搜索 `docs/adr/` 以了解相关的架构决策
3. 在生成代码或文档时遵循既定的约定

## 维护

- 当引入新的领域概念时更新 `GLOSSARY.md`
- 当做出重要架构决策时创建新的 ADR
- ADRs 是不可变的；如需修改决策，创建新的 ADR 并在其中引用旧的
