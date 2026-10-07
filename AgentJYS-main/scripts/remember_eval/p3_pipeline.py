"""P3 integration hook for the paired benchmark: real stores and real inference.

Uses the existing Azure resource factory for isolated real backend namespaces.
Unlike contract tests, a native embedding configuration is mandatory. No fake
LLM, embeddings, compressor, extraction, support or equivalence verdict is used.
"""
from __future__ import annotations

import importlib
import asyncio
import json
import os
from pathlib import Path
import sys
import subprocess
import traceback
from hashlib import sha256
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / 'src'), str(REPO / 'tests')]

from azure_test_runtime import OwnedResources, _resources, create_runtime
from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.remember.basic.comparison import ModelEquivalenceVerifier
from aether_agent_memory.remember.basic.compression import ModelCompression
from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.models import MemoryRef, RememberRequest, SourceInput, TextInput
from aether_agent_memory.remember.model_provider import CompressionVerifier, SupportVerifier
from aether_agent_memory.runtime.contracts.models import Permission, Principal, RecordRef, Scope, ScopeSelector
from aether_agent_memory.runtime.foundation.common import now
from aether_agent_memory.operate.basic.continuous import ContinuousOperate


class P3PipelineFailure(RuntimeError):
    def __init__(self, code, tasks=()):
        super().__init__(code)
        self.failure_code = code
        self.failed_tasks = [
            {'task_id': r.get('task_id'), 'kind': r.get('kind'), 'state': r.get('state'),
             'reason_code': r.get('reason_code'), 'effect_status': r.get('effect_status')}
            for r in tasks
        ]


def construct_runtime(root, policy, model, provider):
    return create_runtime(
        root / 'p3.db', root / 'cache', remember_policy=policy,
        embedding_config=os.environ['AETHER_EVAL_EMBEDDING_CONFIG'],
        extraction=OfficialLangMemConsolidation.from_model(model, model.model_name),
        compressor=ModelCompression(provider), compression_quality=CompressionVerifier(provider),
        support_verifier=SupportVerifier(provider), equivalence_verifier=ModelEquivalenceVerifier(provider),
        recall_settings=RecallSettings(rerank_policy='disabled'),
        operate_factory=ContinuousOperate,
    )


def construct_execution(app, root, run_id):
    from component_configuration import ComponentConfiguration
    from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.celery.config import CeleryConfiguration
    from aether_agent_memory.runtime.celery.service import ExecutionService
    config = ComponentConfiguration(
        data_dir=root, identity_file=root / 'identities-unused.yaml',
        embedding_profile='native', embedding_config=os.environ['AETHER_EVAL_EMBEDDING_CONFIG'],
        temporal=TemporalConfiguration(deployment_id=run_id, endpoint='127.0.0.1:7233'),
        celery=CeleryConfiguration(queue_prefix=os.environ['P3_CELERY_QUEUE']),
    )
    return ExecutionService(app, config, lambda: None, CacheMaintenance(app))


def worker_factory():
    try:
        return _worker_factory()
    except Exception as exc:
        manifest_path = Path(os.environ['AETHER_EVAL_RUN_MANIFEST'])
        safe = {'error_type': type(exc).__name__, 'frames': [
            {'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
            for f in traceback.extract_tb(exc.__traceback__)[-10:]]}
        manifest_path.with_name('worker-bootstrap-error.json').write_text(json.dumps(safe, indent=2), encoding='utf8')
        raise


def _worker_factory():
    """Zero-argument bootstrap called only inside the real Celery child process."""
    import httpx
    from paired_benchmark import Meter
    from aether_agent_memory.runtime.flows.config import LanguageModel
    from aether_agent_memory.remember.langmem_model import create_langmem_chat_model
    from aether_agent_memory.remember.model_provider import ModelProvider
    manifest = json.loads(Path(os.environ['AETHER_EVAL_RUN_MANIFEST']).read_text(encoding='utf8'))
    resources = OwnedResources(manifest['resources'])
    _resources.set(resources)
    config = LanguageModel(
        endpoint=os.environ['AETHER_EVAL_TEMP_ENDPOINT'], model=manifest['model'],
        api_key_env='AETHER_EVAL_TEMP_KEY', max_output_tokens=4096, timeout_seconds=60,
    )
    client = httpx.AsyncClient(transport=Meter(int(os.environ['AETHER_EVAL_MAX_CALLS'])), timeout=60, follow_redirects=False)
    model = create_langmem_chat_model(config, client=client)
    provider = ModelProvider(config, client=client)
    root = Path(manifest['root'])
    app = construct_runtime(root, RememberPolicy.model_validate(manifest['policy']), model, provider)
    service = construct_execution(app, root, manifest['run_id'])
    engine = service.celery
    original_close = engine.close

    def close():
        original_close()
        app.close()

    engine.close = close
    # Keep the reconstructed runtime and clients alive for the worker lifecycle.
    engine._eval_owned = (resources, app, service, model, provider, client)
    return engine


async def celery_drain(app, execution, timeout_seconds=900):
    """Separate Celery process, real Redis broker, SQL dispatch and actual stages."""
    worker = subprocess.Popen(
        [sys.executable, '-m', 'celery', '-A', 'aether_agent_memory.runtime.celery.app:app',
         'worker', '--pool=prefork', '--concurrency=1', '--loglevel=WARNING', '--without-gossip', '--without-mingle'],
        env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                if worker.poll() is not None:
                    raise P3PipelineFailure('CELERY_WORKER_EXITED')
                await execution.dispatcher.flush()
                with app.foundation.uow.transaction() as tx:
                    pending = tx.active_task_rows() or tx.pending_delivery_rows()
                    task_rows = [r['record'] for _, r in tx.rows('tasks')]
                failed = [r for r in task_rows if r['state'] in {'failed', 'attention_required', 'cancelled'}]
                if failed:
                    raise P3PipelineFailure('PRODUCTION_TASK_FAILED', failed)
                if not pending:
                    return
                await asyncio.sleep(0.2)
    finally:
        worker.terminate()
        try:
            await asyncio.to_thread(worker.wait, 15)
        except subprocess.TimeoutExpired:
            worker.kill()
            await asyncio.to_thread(worker.wait, 5)


async def run(*, sample_id, sources, precompression_enabled, model, provider, stage):
    if not Path('/.dockerenv').exists():
        raise RuntimeError('P3 evaluation must run in Docker')
    embedding_config = os.environ.get('AETHER_EVAL_EMBEDDING_CONFIG')
    if not embedding_config:
        raise RuntimeError('real native embedding requires AETHER_EVAL_EMBEDDING_CONFIG')
    if 'precompression_enabled' not in RememberPolicy.model_fields:
        raise RuntimeError('production RememberPolicy.precompression_enabled is not implemented')
    # Identical policy in both arms except this one declared treatment flag.
    # Threshold=1 intentionally forces compression eligibility in the paired study.
    policy = RememberPolicy(
        precompression_enabled=precompression_enabled,
        compression_min_bytes=1,
        consolidation_messages=max(2, len(sources) + 1),
        consolidation_bytes=1_000_000,
        consolidation_overlap_messages=0,
        max_candidates=32,
        max_model_calls=256,
    )
    resources = OwnedResources()
    token = _resources.set(resources)
    run_id = uuid4().hex
    root = Path(os.environ.get('AETHER_EVAL_STATE_DIR', '/eval/p3_state')) / run_id
    root.mkdir(parents=True, exist_ok=True)
    app = None
    execution = None
    try:
        stage.set(f'{sample_id}/{"precompress" if precompression_enabled else "raw"}/production_create_runtime')
        app = construct_runtime(root, policy, model, provider)
        if app.embedding_profile != 'native':
            raise RuntimeError('controlled or injected embeddings are forbidden in this real evaluation')
        scope = Scope(tenant_id='eval_' + run_id, application_id='paired', user_id='research', agent_id='memory')
        principal = Principal(principal_id='eval_user', home_scope=scope, permissions=tuple(Permission), auth_epoch=1)
        app.foundation.identity.provision([(sha256(run_id.encode()).hexdigest(), principal)])
        os.environ['P3_CELERY_QUEUE'] = 'eval_' + run_id
        os.environ['AETHER_CELERY_RUNTIME_FACTORY'] = 'p3_pipeline:worker_factory'
        os.environ['AETHER_EVAL_STAGE_PREFIX'] = f'{sample_id}/{"precompress" if precompression_enabled else "raw"}/production_worker'
        manifest_path = root / 'worker-manifest.json'
        manifest_path.write_text(json.dumps({'run_id': run_id, 'root': str(root), 'resources': resources.manifest(), 'policy': policy.model_dump(mode='json'), 'model': model.model_name}), encoding='utf8')
        os.environ['AETHER_EVAL_RUN_MANIFEST'] = str(manifest_path)
        execution = construct_execution(app, root, run_id)

        def context():
            return app.foundation.identity.context(run_id, timeout_seconds=300)

        with app.foundation.uow.transaction() as tx:
            initial_count = len(list(tx.rows('remember_current')))
        if initial_count:
            raise RuntimeError('evaluation namespace unexpectedly contains prior memories')
        receipts = []
        stage.set(f'{sample_id}/{"precompress" if precompression_enabled else "raw"}/production_admission')
        for index, source in enumerate(sources):
            request = RememberRequest(
                source=SourceInput(kind='text', external_id=f'{sample_id}_{source["id"]}', external_version='1', occurred_at='2026-10-07T00:00:00.000Z'),
                content=TextInput(kind='text', text=source['text']),
                selection=ScopeSelector(session_id='paired_source'),
                trigger='remember' if index == len(sources) - 1 else 'observe',
            )
            receipts.append(await app.remember.save(context(), request))
        stage.set(f'{sample_id}/{"precompress" if precompression_enabled else "raw"}/production_background')
        driver_name = os.environ.get('AETHER_EVAL_DRIVER')
        if driver_name:
            module, symbol = driver_name.split(':', 1)
            driver = getattr(importlib.import_module(module), symbol)
            await driver(app, timeout_seconds=900)
            execution_mode = driver_name
        else:
            await celery_drain(app, execution, timeout_seconds=900)
            execution_mode = 'real_celery_redis_broker_separate_prefork_worker'
        # Resolve only real authority pointers. Hydrated API loads check body hash
        # and current authorization outside metadata transactions when necessary.
        with app.foundation.uow.transaction() as tx:
            pending = [r for _, r in tx.rows('remember_pending')]
            if len(pending) != len(sources) or any(x['state'] != 'processed' for x in pending):
                raise P3PipelineFailure('SOURCE_PROCESSING_NOT_COMMITTED')
            refs = []
            for _, pointer in tx.rows('remember_current'):
                raw = tx.get(RecordRef.model_validate(pointer))
                if raw and raw['kind'] in {'semantic', 'episodic'} and raw['status'] == 'active':
                    refs.append(MemoryRef.model_validate(raw['ref']))
            task_rows = [row['record'] for _, row in tx.rows('tasks')]
            extraction_tasks = [r for r in task_rows if r['kind'] == 'remember.extract']
            if not extraction_tasks or any(r['state'] != 'succeeded' for r in extraction_tasks):
                raise P3PipelineFailure('EXTRACTION_NOT_COMMITTED', extraction_tasks)
            artifacts = [r for _, r in tx.rows('remember_artifacts')]
            attempts = [r for _, r in tx.rows('remember_compression_attempts')]
            rejections = [{k: r.get(k) for k in ('task_id', 'candidate_hash', 'sources', 'evidence', 'status', 'reason')}
                          for _, r in tx.rows('remember_support_verifications') if r['status'] == 'rejected']
            for task in extraction_tasks:
                if task.get('result_ref'):
                    result = tx.get(RecordRef.model_validate(task['result_ref'])) or {}
                    rejections.extend(result.get('candidate_rejections', []))
        batch = app.remember.load(context(), tuple(refs))
        if len(batch.items) != len(refs):
            raise RuntimeError('committed long-term memory readback failed authorization or hydration')
        memories = [
            {'text': item.content, 'kind': item.kind.value, 'memory_id': item.ref.memory_id,
             'version': item.ref.version, 'content_hash': item.content_hash,
             'sources': [s.model_dump(mode='json') for s in item.sources]}
            for item in batch.items
        ]
        return {
            'commit_readback': True,
            'initial_long_term_memory_count': initial_count,
            'memories': memories,
            'commit_evidence': {
                'execution_mode': execution_mode,
                'run_id': run_id,
                'finished_at': now(),
                'source_count': len(sources),
                'extraction_task_ids': [r['task_id'] for r in extraction_tasks],
                'source_processing_states': [r['state'] for r in pending],
                'real_backend_namespaces': resources.manifest(),
                'embedding_profile': app.embedding_profile,
                'max_candidates': policy.max_candidates,
                'compression_min_bytes_override': policy.compression_min_bytes,
            },
            'route_observed': 'precompression_attempted_actual_view_unverified' if attempts else 'raw',
            'compression': attempts,
            'candidate_rejections': rejections,
            'published_artifact_count': sum(bool(a.get('published')) for a in artifacts),
            'intermediate_compression_ratio': None,
        }
    finally:
        if execution is not None:
            execution.celery.close()
        if app is not None:
            app.close()
        # Keep owned PG/object namespace evidence for review; no destructive
        # automatic resource cleanup. Root run manifest identifies exact owners.
        _resources.reset(token)
