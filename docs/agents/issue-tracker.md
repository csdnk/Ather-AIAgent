# Issue Tracker: Linear

此项目使用 Linear 管理 issues，通过 `linear` CLI（schpet/linear-cli v2.6.0）操作。

## 配置信息

- **Workspace**: yu-xiao
- **Team**: AET (Aetherstore Engine)
  - Team UUID: `bba40361-11e2-44c4-b691-3bd821f387e9`
- **Default Project**: Recall Testcase
  - Project UUID: `ceacbbbf-aca6-45ee-bb7a-f2d0654b82dd`
  - Slug: `recall-testcase-5e8a26d0a99d`

## 创建 Issue

```bash
linear issue create \
  --project ceacbbbf-aca6-45ee-bb7a-f2d0654b82dd \
  --title "Issue title" \
  --description "Issue description"
```

**重要提示**: 始终显式传递 `--project` 参数，因为 Linear CLI 没有默认项目设置。

## 查询 Issues

```bash
# 查询 Recall Testcase 项目的所有 issues
linear issue list --project ceacbbbf-aca6-45ee-bb7a-f2d0654b82dd

# 查询特定状态的 issues
linear issue list --project ceacbbbf-aca6-45ee-bb7a-f2d0654b82dd --status "In Progress"
```

## 工作流状态

Linear 的默认工作流状态：
- Backlog
- Todo
- In Progress
- In Review
- Done
- Canceled

## 标签（Labels）

如果使用标签，请确保在 AET team 中已创建以下标签：
- Bug/Feature（类型标签）
- Triage 相关标签
- wayfinder:* 系列标签

**注意**: 这些标签可能需要在 AET team 中重新创建，因为它们可能只存在于之前的 YU team 中。

## Agent 技能集成

像 `to-tickets`、`triage` 等技能会使用 `linear` CLI 来：
- 创建新的 issues
- 更新 issue 状态
- 添加标签和描述
- 查询和过滤 issues

所有操作都会自动使用仓库根目录的 `.linear.toml` 配置。
