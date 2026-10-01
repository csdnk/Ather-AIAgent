"""Fixed, fictional stories; not a browser-editable script language."""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from .models import ScenarioID


@dataclass(frozen=True)
class DialogueTurn:
    action: Literal["remember", "recall"]
    user_text: str
    expected_terms: tuple[str, ...]
    target: Literal["rules", "loan", "noise"]


LIBRARY = (
    DialogueTurn(
        "remember",
        "演示图书馆规则：每次最多借 5 本，借期 30 天，周末 9 点到 17 点开放，计算机类在二楼。",
        (),
        "rules",
    ),
    DialogueTurn("remember", "记住，我借了《机器学习入门》，还想找一本 Python 的书。", (), "loan"),
    DialogueTurn("recall", "图书馆一次最多能借几本，借多久？", ("5 本", "30 天"), "rules"),
    DialogueTurn("recall", "我刚才借的是哪一本书？", ("机器学习入门",), "loan"),
    DialogueTurn("remember", "我今天整理了书桌，把水杯放到左边。", (), "noise"),
    DialogueTurn("recall", "再确认一下，我借的是哪一本书？", ("机器学习入门",), "loan"),
)

RULES = "演示图书馆规则：每次最多借 5 本，借期 30 天，周末 9 点到 17 点开放，计算机类在二楼。"

STORY_TEXTS = {
    "library-full": (
        "请上传并保存这份虚构图书馆规则文档。",
        "记住，我借了《机器学习入门》，还想找一本 Python 的书。",
        "图书馆一次最多能借几本，借多久？",
        "计算机类图书在几楼？",
        "我刚才借的是哪一本书？",
        "把借期相关的记忆原文和来源片段给我看一下。",
        "周末图书馆什么时候开放？",
        "我整理了书桌。再确认一次，我借的书是哪一本？",
        "核对本轮目录和刚才的召回记录。",
    ),
    "weather-weekend": (
        "以下是测试设定，不是实时天气：周六有小雨，20 摄氏度。",
        "我原本想去公园，但下雨更喜欢室内活动。",
        "记住，雨天的备用安排是去图书馆。",
        "周六的测试天气设定是什么？",
        "雨天我选的备用地点在哪里？",
        "把本轮安排保存为可跨会话查询的记忆，并等待真实处理结果。",
        "新会话：我之前为雨天定的备用安排是什么？",
        "新会话：我原来的户外计划是什么？查看这条记忆的调度信息。",
    ),
    "preference-update": (
        "记住，我喜欢无糖咖啡，阅读时希望安静。",
        "我喜欢什么饮品，对阅读环境有什么要求？",
        "更正一下：饮品改为无糖红茶，安静阅读的要求不变。",
        "现在我的饮品和阅读环境偏好是什么？",
        "刚才那份旧查询结果还能使用吗？",
        "这个测试安排已结束，请归档；随后我又需要它，请恢复。",
        "查看并设置这条测试记忆的保留规则。",
        "对这条记忆重新处理、重建索引，并报告真实任务状态。",
    ),
    "learning-review": (
        "第一条学习记录：我在图书馆学了 Python 列表，安静环境更专注。",
        "第二条学习记录：我练了 Python 字典，先看示例再练习比较有效。",
        "第三条学习记录：我复习列表和字典，做小练习有助于查漏补缺。",
        "我记录过哪些学习内容？",
        "把这些学习记录保存为可跨会话查询的记忆。",
        "开启本轮学习记录的反思设置，再读取核对。",
        "对这些真实的长期学习记录提交一次提炼，并查看任务。",
        "处理完成后，展示实际生成的记忆和证据；没有模型就如实说明。",
    ),
    "forget-sources": (
        "记住本次虚构测试预约编号 DEMO-BOOK-017。",
        "我的测试预约编号是多少？",
        "这条预约已经取消，请忘记它。",
        "现在还能查到这个编号吗？刚才的查询结果还有效吗？",
        "保存第二份独立测试资料，然后删除它的整个来源。",
        "保存第三份独立测试资料，然后撤销它的来源。",
        "分别核对三份资料是否还会被召回，并展示实际清理状态。",
    ),
}

class ScenarioSummary(TypedDict):
    id: ScenarioID
    title: str
    description: str
    total_steps: NotRequired[int]


SCENARIOS: list[ScenarioSummary] = [
    {
        "id": "library-full",
        "title": "图书馆里的记忆",
        "description": "规则文档 · 借阅追问 · 原文溯源 · 干扰验证",
    },
    {
        "id": "weather-weekend",
        "title": "雨天的周末计划",
        "description": "虚构天气 · 长期化 · 不带旧聊天的新会话召回",
    },
    {
        "id": "preference-update",
        "title": "偏好可以被更正",
        "description": "版本更正 · 旧结果失效 · 归档恢复 · 保留规则",
    },
    {
        "id": "learning-review",
        "title": "让学习留下线索",
        "description": "三条学习记录 · 反思设置 · 真实提炼或条件不足",
    },
    {
        "id": "forget-sources",
        "title": "记住，也能够忘记",
        "description": "单条删除 · 独立来源删除与撤销 · 可见性复查",
    },
]
for _scenario in SCENARIOS:
    _scenario["total_steps"] = len(STORY_TEXTS[_scenario["id"]])
