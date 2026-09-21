# 【中文研读】阅读主线：可选生成查询变体 → 查询与检索器组合执行 → 按选定规则融合。P3 参考 Working/长期记忆多来源组合；不据此新增检索模式。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
import asyncio
from enum import Enum
from typing import Dict, List, Optional, Tuple, cast

from llama_index.core.async_utils import run_async_tasks
from llama_index.core.callbacks.base import CallbackManager
from llama_index.core.constants import DEFAULT_SIMILARITY_TOP_K
from llama_index.core.llms.utils import LLMType, resolve_llm
from llama_index.core.prompts import PromptTemplate
from llama_index.core.prompts.mixin import PromptDictType
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import IndexNode, NodeWithScore, QueryBundle
from llama_index.core.settings import Settings

QUERY_GEN_PROMPT = (
    "You are a helpful assistant that generates multiple search queries based on a "
    "single input query. Generate {num_queries} search queries, one on each line, "
    "related to the following input query:\n"
    "Query: {query}\n"
    "Queries:\n"
)


# 【中文研读】类型职责：声明融合策略名称；RRF 使用名次，其他策略处理原分数，不能混淆数学语义。
class FUSION_MODES(str, Enum):
    """Enum for different fusion modes."""

    RECIPROCAL_RANK = "reciprocal_rerank"  # apply reciprocal rank fusion
    RELATIVE_SCORE = "relative_score"  # apply relative score fusion
    DIST_BASED_SCORE = "dist_based_score"  # apply distance-based score fusion
    SIMPLE = "simple"  # simple re-ordering of results based on original scores


# 【中文研读】类型职责：把多个检索器装成一个检索器；对外仍返回带分节点，可作为外层查询引擎的一个组件。
class QueryFusionRetriever(BaseRetriever):
    # 【中文研读】方法职责：保存检索器、融合规则及查询生成模型
    # 【中文研读】输入参数：retrievers（参与融合的多个检索器）；llm（回答或查询生成模型）；query_gen_prompt（查询生成提示模板）；mode（处理模式，含义由当前类型定义）；similarity_top_k（最多返回的候选数量）；num_queries（包含原查询在内的查询数量配置）；use_async（是否选择异步执行路径）；verbose（是否输出调试信息）；callback_manager（运行事件与回调管理器）；objects（可供递归展开的索引节点）；object_map（索引 ID 到可检索对象的映射）；retriever_weights（各检索器的权重）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        retrievers: List[BaseRetriever],
        llm: Optional[LLMType] = None,
        query_gen_prompt: Optional[str] = None,
        mode: FUSION_MODES = FUSION_MODES.SIMPLE,
        similarity_top_k: int = DEFAULT_SIMILARITY_TOP_K,
        num_queries: int = 4,
        use_async: bool = True,
        verbose: bool = False,
        callback_manager: Optional[CallbackManager] = None,
        objects: Optional[List[IndexNode]] = None,
        object_map: Optional[dict] = None,
        retriever_weights: Optional[List[float]] = None,
    ) -> None:
        # 【中文研读】处理流程：默认 num_queries=4，权重缺省均分，否则按总和归一化；即便只做检索，也要留意全局 LLM 的依赖解析。
        self.num_queries = num_queries
        self.query_gen_prompt = query_gen_prompt or QUERY_GEN_PROMPT
        self.similarity_top_k = similarity_top_k
        self.mode = mode
        self.use_async = use_async

        self._retrievers = retrievers
        # 【中文研读】没有配置权重时各检索器均分；自定义权重分支仅做归一化，调用方仍需约束非空列表和合法总权重。
        if retriever_weights is None:
            self._retriever_weights = [1.0 / len(retrievers)] * len(retrievers)
        else:
            # Sum of retriever_weights must be 1
            total_weight = sum(retriever_weights)
            self._retriever_weights = [w / total_weight for w in retriever_weights]
        self._llm = (
            resolve_llm(llm, callback_manager=callback_manager) if llm else Settings.llm
        )
        super().__init__(
            callback_manager=callback_manager,
            object_map=object_map,
            objects=objects,
            verbose=verbose,
        )

    # 【中文研读】方法职责：将查询生成字符串包装为 PromptTemplate，供统一提示接口读取。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：PromptDictType。结果的业务含义与失败分支见下面处理流程。
    def _get_prompts(self) -> PromptDictType:
        """Get prompts."""
        # 【中文研读】处理流程：将查询生成字符串包装为 PromptTemplate，供统一提示接口读取。
        return {"query_gen_prompt": PromptTemplate(self.query_gen_prompt)}

    # 【中文研读】方法职责：仅当提供 query_gen_prompt 时替换查询生成模板；不修改检索器或融合方式。
    # 【中文研读】输入参数：prompts。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def _update_prompts(self, prompts: PromptDictType) -> None:
        """Update prompts."""
        # 【中文研读】处理流程：仅当提供 query_gen_prompt 时替换查询生成模板；不修改检索器或融合方式。
        if "query_gen_prompt" in prompts:
            self.query_gen_prompt = cast(
                PromptTemplate, prompts["query_gen_prompt"]
            ).template

    # 【中文研读】方法职责：异步生成额外查询
    # 【中文研读】输入参数：original_query（原始查询文本）。
    # 【中文研读】返回约定：List[QueryBundle]。结果的业务含义与失败分支见下面处理流程。
    async def _aget_queries(self, original_query: str) -> List[QueryBundle]:
        # 【中文研读】处理流程：请求 num_queries-1 个变体，按行清洗空行并截断，多出的回答不继续扩展查询。
        prompt_str = self.query_gen_prompt.format(
            num_queries=self.num_queries - 1,
            query=original_query,
        )
        # 【中文研读】额外模型调用只服务查询改写，不是最终答案生成，也不是必需业务步骤。
        response = await self._llm.acomplete(prompt_str)
        queries = response.text.strip("`").split("\n")
        queries = [q.strip() for q in queries if q.strip()]
        if self._verbose:
            queries_str = "\n".join(queries)
            print(f"Generated queries:\n{queries_str}")
        return [QueryBundle(q) for q in queries[: self.num_queries - 1]]

    # 【中文研读】方法职责：同步生成额外查询
    # 【中文研读】输入参数：original_query（原始查询文本）。
    # 【中文研读】返回约定：List[QueryBundle]。结果的业务含义与失败分支见下面处理流程。
    def _get_queries(self, original_query: str) -> List[QueryBundle]:
        # 【中文研读】处理流程：调用模型后去除首尾反引号，分行去空白并限制数量；这是额外模型调用，需评估开销。
        prompt_str = self.query_gen_prompt.format(
            num_queries=self.num_queries - 1,
            query=original_query,
        )
        # 【中文研读】同步模型调用边界；生成更多查询会放大后续检索组合数量。
        response = self._llm.complete(prompt_str)

        # Strip code block and assume LLM properly put each query on a newline
        queries = response.text.strip("`").split("\n")
        queries = [q.strip() for q in queries if q.strip()]
        if self._verbose:
            queries_str = "\n".join(queries)
            print(f"Generated queries:\n{queries_str}")

        # The LLM often returns more queries than we asked for, so trim the list.
        return [QueryBundle(q) for q in queries[: self.num_queries - 1]]

    # 【中文研读】方法职责：以排名倒数融合多路候选
    # 【中文研读】输入参数：results（多路或多次检索结果）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _reciprocal_rerank_fusion(
        self, results: Dict[Tuple[str, int], List[NodeWithScore]]
    ) -> List[NodeWithScore]:
        """
        Apply reciprocal rank fusion.

        The original paper uses k=60 for best results:
        https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
        """
        # 【中文研读】处理流程：各路按分数降序，按节点 hash 累加 1/(rank+60)，再按融合分数排序并写回节点分数。
        # 【中文研读】平滑常数减少头部名次波动的影响；当前代码 enumerate 从 0 开始，第一名贡献 1/60，P3 契约需单独固定起点。
        k = 60.0  # `k` is a parameter used to control the impact of outlier rankings.
        fused_scores = {}
        hash_to_node = {}

        # compute reciprocal rank scores
        for nodes_with_scores in results.values():
            for rank, node_with_score in enumerate(
                sorted(nodes_with_scores, key=lambda x: x.score or 0.0, reverse=True)
            ):
                # 【中文研读】这里的归并身份是节点 hash；P3 需按 memory_id + version 判断同一业务候选。
                hash = node_with_score.node.hash
                hash_to_node[hash] = node_with_score
                if hash not in fused_scores:
                    fused_scores[hash] = 0.0
                # 【中文研读】同一节点在多路排名中累加贡献；不直接相加各模型的原始相似度。
                fused_scores[hash] += 1.0 / (rank + k)

        # sort results
        reranked_results = dict(
            sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        )

        # adjust node scores
        reranked_nodes: List[NodeWithScore] = []
        for hash, score in reranked_results.items():
            reranked_nodes.append(hash_to_node[hash])
            # 【中文研读】原 NodeWithScore 对象的 score 被改写；复用同一对象引用时要留意原始分数不会自动保留。
            reranked_nodes[-1].score = score

        return reranked_nodes

    # 【中文研读】方法职责：先规范各路分数尺度再加权累加
    # 【中文研读】输入参数：results（多路或多次检索结果）；dist_based（是否按均值和标准差确定分数缩放区间）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _relative_score_fusion(
        self,
        results: Dict[Tuple[str, int], List[NodeWithScore]],
        dist_based: Optional[bool] = False,
    ) -> List[NodeWithScore]:
        """Apply relative score fusion."""
        # MinMax scale scores of each result set (highest value becomes 1, lowest becomes 0)
        # then scale by the weight of the retriever
        # 【中文研读】处理流程：计算边界 → 归一化 → 乘检索器权重并除查询数量 → 按 hash 合并分数 → 排序。
        min_max_scores = {}
        for query_tuple, nodes_with_scores in results.items():
            # 【中文研读】空候选路由给出零边界并跳过；这里没有记录这一路是正常空还是外部故障。
            if not nodes_with_scores:
                min_max_scores[query_tuple] = (0.0, 0.0)
                continue
            scores = [
                node_with_score.score or 0.0 for node_with_score in nodes_with_scores
            ]
            # 【中文研读】选择均值±3倍标准差作为缩放区间；这是分数处理，不是概率校准。
            if dist_based:
                # Set min and max based on mean and std dev
                mean_score = sum(scores) / len(scores)
                std_dev = (
                    sum((x - mean_score) ** 2 for x in scores) / len(scores)
                ) ** 0.5
                min_score = mean_score - 3 * std_dev
                max_score = mean_score + 3 * std_dev
            else:
                min_score = min(scores)
                max_score = max(scores)
            min_max_scores[query_tuple] = (min_score, max_score)

        for query_tuple, nodes_with_scores in results.items():
            for node_with_score in nodes_with_scores:
                min_score, max_score = min_max_scores[query_tuple]
                # Scale the score to be between 0 and 1
                # 【中文研读】所有分数相同会导致分母为零，改用统一 0 或 1；该分支不创造更细的候选区分。
                if max_score == min_score:
                    node_with_score.score = 1.0 if max_score > 0 else 0.0
                else:
                    node_with_score.score = (node_with_score.score - min_score) / (
                        max_score - min_score
                    )
                # Scale by the weight of the retriever
                retriever_idx = query_tuple[1]
                existing_score = node_with_score.score or 0.0
                node_with_score.score = (
                    existing_score * self._retriever_weights[retriever_idx]
                )
                # Divide by the number of queries
                # 【中文研读】按配置的查询数量摊分贡献，不是根据实际模型返回了多少查询重新计算。
                node_with_score.score /= self.num_queries

        # Use a dict to de-duplicate nodes
        all_nodes: Dict[str, NodeWithScore] = {}

        # Sum scores for each node
        for nodes_with_scores in results.values():
            for node_with_score in nodes_with_scores:
                # 【中文研读】这里的归并身份是节点 hash；P3 需按 memory_id + version 判断同一业务候选。
                hash = node_with_score.node.hash
                if hash in all_nodes:
                    cur_score = all_nodes[hash].score or 0.0
                    all_nodes[hash].score = cur_score + (node_with_score.score or 0.0)
                else:
                    all_nodes[hash] = node_with_score

        return sorted(all_nodes.values(), key=lambda x: x.score or 0.0, reverse=True)

    # 【中文研读】方法职责：同 hash 保留最大原分数后排序
    # 【中文研读】输入参数：results（多路或多次检索结果）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _simple_fusion(
        self, results: Dict[Tuple[str, int], List[NodeWithScore]]
    ) -> List[NodeWithScore]:
        """Apply simple fusion."""
        # Use a dict to de-duplicate nodes
        # 【中文研读】处理流程：逐路遍历，重复节点比较分数，首次节点登记；原分数跨模型未必可比。
        all_nodes: Dict[str, NodeWithScore] = {}
        for nodes_with_scores in results.values():
            for node_with_score in nodes_with_scores:
                # 【中文研读】这里的归并身份是节点 hash；P3 需按 memory_id + version 判断同一业务候选。
                hash = node_with_score.node.hash
                if hash in all_nodes:
                    max_score = max(
                        node_with_score.score or 0.0, all_nodes[hash].score or 0.0
                    )
                    all_nodes[hash].score = max_score
                else:
                    all_nodes[hash] = node_with_score

        return sorted(all_nodes.values(), key=lambda x: x.score or 0.0, reverse=True)

    # 【中文研读】方法职责：从同步入口执行成组异步检索
    # 【中文研读】输入参数：queries（原查询与可选变体的列表）。
    # 【中文研读】返回约定：Dict[Tuple[str, int], List[NodeWithScore]]。结果的业务含义与失败分支见下面处理流程。
    def _run_nested_async_queries(
        self, queries: List[QueryBundle]
    ) -> Dict[Tuple[str, int], List[NodeWithScore]]:
        # 【中文研读】处理流程：构造所有查询×检索器组合，记录对应键，交给异步辅助运行器，再恢复结果映射。
        tasks, task_queries = [], []
        for query in queries:
            for i, retriever in enumerate(self._retrievers):
                tasks.append(retriever.aretrieve(query))
                task_queries.append((query.query_str, i))

        task_results = run_async_tasks(tasks)

        results = {}
        for query_tuple, query_result in zip(task_queries, task_results):
            results[query_tuple] = query_result

        return results

    # 【中文研读】方法职责：异步等待所有查询组合
    # 【中文研读】输入参数：queries（原查询与可选变体的列表）。
    # 【中文研读】返回约定：Dict[Tuple[str, int], List[NodeWithScore]]。结果的业务含义与失败分支见下面处理流程。
    async def _run_async_queries(
        self, queries: List[QueryBundle]
    ) -> Dict[Tuple[str, int], List[NodeWithScore]]:
        # 【中文研读】处理流程：先收集协程，gather 并发执行，再按原组合顺序恢复字典；某一路异常并不会自动变成带覆盖状态的降级结果。
        tasks, task_queries = [], []
        for query in queries:
            for i, retriever in enumerate(self._retrievers):
                tasks.append(retriever.aretrieve(query))
                task_queries.append((query.query_str, i))

        # 【中文研读】等待所有并发检索；默认异常传播，不自动给失败来源制造空候选。
        task_results = await asyncio.gather(*tasks)

        results = {}
        for query_tuple, query_result in zip(task_queries, task_results):
            results[query_tuple] = query_result

        return results

    # 【中文研读】方法职责：按查询与检索器双循环逐个同步调用
    # 【中文研读】输入参数：queries（原查询与可选变体的列表）。
    # 【中文研读】返回约定：Dict[Tuple[str, int], List[NodeWithScore]]。结果的业务含义与失败分支见下面处理流程。
    def _run_sync_queries(
        self, queries: List[QueryBundle]
    ) -> Dict[Tuple[str, int], List[NodeWithScore]]:
        # 【中文研读】处理流程：结果键为查询文本与检索器编号，供后续分路融合。
        results = {}
        for query in queries:
            for i, retriever in enumerate(self._retrievers):
                results[(query.query_str, i)] = retriever.retrieve(query)

        return results

    # 【中文研读】方法职责：同步融合入口
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        # 【中文研读】处理流程：保留原问题，可选追加查询变体，按配置选择执行方式，最后选择融合算法并截取 TopK。
        queries: List[QueryBundle] = [query_bundle]
        # 【中文研读】num_queries=1 可跳过查询生成；默认 4 会进入此分支，仍需检查构造器的模型依赖。
        if self.num_queries > 1:
            queries.extend(self._get_queries(query_bundle.query_str))

        if self.use_async:
            results = self._run_nested_async_queries(queries)
        else:
            results = self._run_sync_queries(queries)

        # 【中文研读】融合后再截取候选上限；这是算法结果，不是已核验且符合 token 预算的 ContextPack。
        if self.mode == FUSION_MODES.RECIPROCAL_RANK:
            return self._reciprocal_rerank_fusion(results)[: self.similarity_top_k]
        elif self.mode == FUSION_MODES.RELATIVE_SCORE:
            return self._relative_score_fusion(results)[: self.similarity_top_k]
        elif self.mode == FUSION_MODES.DIST_BASED_SCORE:
            return self._relative_score_fusion(results, dist_based=True)[
                : self.similarity_top_k
            ]
        elif self.mode == FUSION_MODES.SIMPLE:
            return self._simple_fusion(results)[: self.similarity_top_k]
        else:
            raise ValueError(f"Invalid fusion mode: {self.mode}")

    # 【中文研读】方法职责：异步融合入口
    # 【中文研读】输入参数：query_bundle（包装后的查询文本及可选向量）。
    # 【中文研读】返回约定：List[NodeWithScore]。结果的业务含义与失败分支见下面处理流程。
    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        # 【中文研读】处理流程：可选异步生成查询，然后并发执行所有组合，按融合规则输出 TopK。
        queries: List[QueryBundle] = [query_bundle]
        # 【中文研读】num_queries=1 可跳过查询生成；默认 4 会进入此分支，仍需检查构造器的模型依赖。
        if self.num_queries > 1:
            queries.extend(await self._aget_queries(query_bundle.query_str))

        results = await self._run_async_queries(queries)

        # 【中文研读】融合后再截取候选上限；这是算法结果，不是已核验且符合 token 预算的 ContextPack。
        if self.mode == FUSION_MODES.RECIPROCAL_RANK:
            return self._reciprocal_rerank_fusion(results)[: self.similarity_top_k]
        elif self.mode == FUSION_MODES.RELATIVE_SCORE:
            return self._relative_score_fusion(results)[: self.similarity_top_k]
        elif self.mode == FUSION_MODES.DIST_BASED_SCORE:
            return self._relative_score_fusion(results, dist_based=True)[
                : self.similarity_top_k
            ]
        elif self.mode == FUSION_MODES.SIMPLE:
            return self._simple_fusion(results)[: self.similarity_top_k]
        else:
            raise ValueError(f"Invalid fusion mode: {self.mode}")
