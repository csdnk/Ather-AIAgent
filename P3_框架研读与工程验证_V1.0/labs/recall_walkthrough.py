"""Real LlamaIndex interfaces with synthetic candidates and MockEmbedding.

No remote model, Milvus, production authorization, or P3 acceptance is exercised.
Run from the learning-package root: python -m labs.recall_walkthrough
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from importlib.metadata import version
import inspect
import json
from pathlib import Path
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode


class NoGenerationLLM(MockLLM):
    def predict(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError('This exercise must not generate query variants or answers')

    async def apredict(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError('This exercise must not generate query variants or answers')


class FixedRetriever(BaseRetriever):
    """A deliberately synthetic backend, to expose the framework call boundary."""

    def __init__(self, label: str, candidates: list[NodeWithScore], events: list[dict]):
        super().__init__()
        self.label = label
        self.candidates = candidates
        self.events = events

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        self.events.append({'event': 'retrieve', 'source': self.label,
                            'query': query_bundle.query_str, 'input_type': type(query_bundle).__name__})
        return deepcopy(self.candidates)


class ExampleVersionFilter(BaseNodePostprocessor):
    """Teaching-only local snapshot check; NOT a transactional final_guard."""

    current_versions: dict[str, int]

    def _postprocess_nodes(self, nodes: list[NodeWithScore],
                           query_bundle: QueryBundle | None = None) -> list[NodeWithScore]:
        return [row for row in nodes
                if self.current_versions.get(row.node.metadata['memory_id'])
                == row.node.metadata['version']]


def candidate(memory_id: str, ver: int, content: str, score: float) -> NodeWithScore:
    return NodeWithScore(node=TextNode(
        id_=f'{memory_id}_v{ver}', text=content,
        metadata={'memory_id': memory_id, 'version': ver,
                  'tenant_id': 'demo_tenant', 'user_id': 'demo_user', 'agent_id': 'demo_agent'}), score=score)


def describe(nodes: list[NodeWithScore]) -> list[dict]:
    return [{'memory_id': n.node.metadata['memory_id'], 'version': n.node.metadata['version'],
             'node_id': n.node.node_id, 'node_hash': n.node.hash, 'score': n.score,
             'content': n.node.get_content()} for n in nodes]


def run_demo() -> dict:
    events: list[dict] = []
    working_row = candidate('m_trip', 1, '我下周要去杭州出差。', 0.95)
    old = candidate('m_coffee', 1, '我喜欢加糖咖啡。', 0.99)
    current = candidate('m_coffee', 2, '我现在喜欢无糖咖啡。', 0.85)
    working = FixedRetriever('working_fixture', [working_row], events)
    long_term = FixedRetriever('long_term_fixture',
                               [old, current, candidate('m_trip', 1, working_row.node.text, 0.80)], events)

    # STEP 1: BaseRetriever.retrieve converts str to QueryBundle, then dispatches _retrieve.
    simple = working.retrieve('我出差要去哪里？')
    assert events[-1]['input_type'] == 'QueryBundle'

    # STEP 2: Real VectorIndexRetriever + local SimpleVectorStore, fake vectors.
    # MockEmbedding gives no meaningful semantic ranking. Only study call structure.
    index = VectorStoreIndex([deepcopy(current.node), deepcopy(working_row.node)],
                             embed_model=MockEmbedding(embed_dim=8))
    vector_retriever = index.as_retriever(similarity_top_k=2)
    vector_rows = vector_retriever.retrieve('我喜欢什么咖啡？')
    assert len(vector_rows) == 2

    # STEP 3: Fusion owns its child retrievers. num_queries=1 prevents query generation.
    fusion = QueryFusionRetriever([working, long_term], llm=NoGenerationLLM(),
                                  num_queries=1, mode=FUSION_MODES.RECIPROCAL_RANK,
                                  similarity_top_k=10, use_async=False)
    fused = fusion.retrieve('我的出差和咖啡偏好是什么？')
    assert len(fused) == 3  # m_trip's identical hash merges, old and new coffee remain.
    assert {n.node.metadata['version'] for n in fused
            if n.node.metadata['memory_id'] == 'm_coffee'} == {1, 2}
    assert fused[0].node.metadata['memory_id'] == 'm_trip'

    # STEP 4: Postprocessing is explicit here. Merely declaring a filter does not run it.
    guard = ExampleVersionFilter(current_versions={'m_trip': 1, 'm_coffee': 2})
    filtered = guard.postprocess_nodes(fused, query_str='我的出差和咖啡偏好是什么？')
    assert len(filtered) == 2
    assert all(n.node.metadata['version'] == guard.current_versions[n.node.metadata['memory_id']]
               for n in filtered)
    # An equal-content different-ID node can hash identically: identity != content hash.
    same_content = deepcopy(working_row.node)
    same_content.id_ = 'a_different_node_id'
    assert same_content.hash == working_row.node.hash

    symbols = {
        'BaseRetriever.retrieve': BaseRetriever.retrieve,
        'VectorIndexRetriever._retrieve': type(vector_retriever)._retrieve,
        'QueryFusionRetriever._retrieve': QueryFusionRetriever._retrieve,
        'BaseNodePostprocessor.postprocess_nodes': BaseNodePostprocessor.postprocess_nodes,
    }
    locations = {}
    for name, func in symbols.items():
        fn = inspect.unwrap(func)
        locations[name] = {'path': inspect.getsourcefile(fn), 'line': inspect.getsourcelines(fn)[1]}
    return {'generated_at': datetime.now(timezone.utc).isoformat(),
            'kind': 'framework_walkthrough_only', 'llama_index_core_version': version('llama-index-core'),
            'real_framework': True, 'embedding': 'MockEmbedding', 'vector_store': 'SimpleVectorStore',
            'remote_model_calls': 0, 'milvus_tested': False, 'production_final_guard_tested': False,
            'simple_result': describe(simple), 'vector_path_result': describe(vector_rows),
            'fusion_before_filter': describe(fused), 'after_explicit_filter': describe(filtered),
            'events': events, 'debug_locations': locations, 'checks_passed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run_demo()
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized+'\n', encoding='utf-8')
    print(serialized)


if __name__ == '__main__':
    main()
