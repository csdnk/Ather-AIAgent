"""Bounded, offline CPU component benchmarks; never a release or capacity verdict.

Uses the project's actual native BGE engine or LLMLingua preprocessor. No HTTP
model calls, automatic downloads, production data writes, or fallback models.
The parent bounds runtime and saves partial evidence if a native worker hangs.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path


class EmbeddingInputTooLong(ValueError):
    def __init__(self,count,limit):
        self.count=count;self.limit=limit


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate_dataset(rows):
    if not isinstance(rows,list) or not rows or len(rows)>100:
        raise ValueError('dataset requires 1..100 rows')
    ids=set()
    for row in rows:
        if (not isinstance(row.get('dataset_id'),str) or not row['dataset_id'] or row['dataset_id'] in ids
            or not isinstance(row.get('text'),str) or not row['text'].strip()
            or len(row['text'].encode())>262144
            or not re.fullmatch('[0-9a-f]{64}',row.get('source_sha256',''))):
            raise ValueError('unique ID, nonempty bounded text and source hash required')
        ids.add(row['dataset_id'])
    return rows


def summarize(rows,planned,elapsed):
    if planned<=0 or elapsed<0 or not math.isfinite(elapsed):raise ValueError('invalid measurement window')
    started=[r for r in rows if r['status'] in ('PASS','FAIL')]
    good=[r for r in started if r['status']=='PASS']
    failed=len(started)-len(good)
    latencies=sorted(r['seconds']*1000 for r in good)
    def quantile(p,minimum):
        return latencies[math.ceil(len(latencies)*p)-1] if len(latencies)>=minimum else None
    return {'status':'FAIL' if failed else ('PASS' if len(good)==planned else 'BLOCKED'),
        'planned':planned,'completed_valid':len(good),'failed':failed,'not_completed':planned-len(started),
        'elapsed_seconds':elapsed,'valid_items_per_second':len(good)/elapsed if elapsed>0 and started else None,
        'valid_tokens_per_second':sum(r.get('input_tokens',0) for r in good)/elapsed if elapsed>0 and started else None,
        'error_rate_started':failed/len(started) if started else None,
        'p50_ms':quantile(.50,1),'p95_ms':quantile(.95,20),'p99_ms':quantile(.99,100),
        'percentile_note':'successful operations only; nearest-rank; P95 needs 20 and P99 100 successful samples; exploratory, not SLA',
        'max_ms':max(latencies) if latencies else None}


def compression_metrics(rows):
    original=stored=tokens=output_tokens=0;unknown_tokens=False;failed=0
    for r in rows:
        original+=r['input_bytes']
        success=r['status']=='PASS'
        if success and (r.get('output_bytes',0)<=0 or r.get('output_tokens',0)<=0):
            raise ValueError('empty output cannot claim compression')
        stored+=r['output_bytes'] if success else r['input_bytes']
        failed+=not success
        if r.get('input_tokens') is None:unknown_tokens=True
        else:
            tokens+=r['input_tokens']
            output_tokens+=r['output_tokens'] if success else r['input_tokens']
    return {'input_bytes':original,'output_bytes_conservative':stored,
        'byte_factor_conservative':original/stored if stored else None,
        'token_factor_conservative':tokens/output_tokens if output_tokens and not unknown_tokens else None,
        'failed_inputs_retained':failed,'semantic_quality_status':'BLOCKED','physical_storage_status':'NOT_RUN',
        'boundary':'LLMLingua model-input view, not committed long-term content; failures retain original size; no physical storage claim'}


def fact_probe(text,facts):
    if not facts:return {'status':'BLOCKED','semantic_acceptance':False,'matched':0,'total':0}
    matched=sum(fact in text for fact in facts)
    return {'status':'PASS' if matched==len(facts) else 'FAIL','matched':matched,'total':len(facts),
        'semantic_acceptance':False,'method':'exact critical phrase retention only; not human factual adjudication'}


def compare(current,baseline):
    if current.get('status','PASS')!='PASS' or baseline.get('status','PASS')!='PASS':
        return {'status':'BLOCKED','reason':'incomplete or failed baseline/current run'}
    if current.get('comparison_key')!=baseline.get('comparison_key'):
        return {'status':'BLOCKED','reason':'model, tokenizer, hardware, runtime or workload differs'}
    a=current['metrics'].get('valid_items_per_second');b=baseline.get('metrics',{}).get('valid_items_per_second')
    if not a or not b:return {'status':'BLOCKED','reason':'missing nonzero throughput'}
    return {'status':'PASS','throughput_change_percent':(a/b-1)*100,'acceptance':'observation only, no frozen regression threshold'}


def finish_samples(events,tasks,finish):
    samples={e['index']:e for e in events if e['type']=='sample'}
    for event in events:
        if event['type']=='operation_start' and event['index'] not in samples:
            index=event['index'];repeat,usage,row=tasks[index]
            samples[index]={'type':'sample','index':index,'dataset_id':row['dataset_id'],
                'repeat':repeat,'usage':usage,'input_bytes':len(row['text'].encode()),
                'input_sha256':hashlib.sha256(row['text'].encode()).hexdigest(),
                'status':'FAIL','error_type':'INTERRUPTED','seconds':max(0,finish-event['monotonic'])}
    return [samples[k] for k in sorted(samples)]


def threshold_gate(metrics,thresholds):
    checks=[]
    for key,field,minimum in [('min_items_per_second','valid_items_per_second',True),('max_p95_ms','p95_ms',False),
            ('min_byte_factor','byte_factor_conservative',True),('min_token_factor','token_factor_conservative',True)]:
        value=thresholds.get(key)
        if value is None:continue
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<=0:
            raise ValueError('positive finite component threshold required')
        actual=metrics.get(field)
        status='BLOCKED' if actual is None or metrics['status']=='BLOCKED' else ('PASS' if (actual>=value if minimum else actual<=value) else 'FAIL')
        checks.append({'metric':field,'threshold':value,'actual':actual,'status':status})
    status='FAIL' if metrics['status']=='FAIL' or any(c['status']=='FAIL' for c in checks) else (
        'PASS' if metrics['status']=='PASS' and checks and all(c['status']=='PASS' for c in checks) else 'BLOCKED')
    return {'status':status,'checks':checks,'scope':'explicit component thresholds only; not contract acceptance'}


def workload(rows,args):
    return [(repeat,usage,row) for repeat in range(args.repeats)
        for usage in (['Query','Passage'] if args.kind=='embedding' else ['Compression']) for row in rows]


def worker(args):
    # Force offline mode before loading model libraries. Local model path required.
    for name in ['HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE']:os.environ[name]='1'
    for name in ['OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS']:os.environ[name]=str(args.threads)
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent.parent/'src'))
    rows=validate_dataset(json.loads(Path(args.dataset).read_text(encoding='utf-8')))
    events=Path(args.events)
    def event(value):
        value['monotonic']=time.perf_counter()
        with events.open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n');f.flush()
    engine=None
    try:
        model=Path(args.model_path)
        if not model.is_dir() or not (model/'tokenizer.json').is_file():
            event({'type':'blocked','reason':'local model directory/tokenizer missing'});return 2
        started=time.perf_counter()
        if args.kind=='embedding':
            import numpy as np
            from aether_agent_memory.recall.embedding.native import NativeEmbeddingBackend,NativeEmbeddingSettings
            engine=NativeEmbeddingBackend(NativeEmbeddingSettings(model_path=model,cache_dir=model,threads=args.threads))
            description=engine.describe()
            identity={k:description[k] for k in ['model_id','model_version','dimension','preprocessing_version','max_input_tokens']}
            def operate(text,usage):
                count=engine.count_tokens(text,usage)
                if count>engine.max_input_tokens:raise EmbeddingInputTooLong(count,engine.max_input_tokens)
                vectors=engine._backend.embed([engine._formatted(text,usage)],[usage.lower()],batch_size=1)
                assert len(vectors)==1
                vector=np.asarray(vectors[0],dtype=np.float32)
                assert vector.shape==(512,) and np.isfinite(vector).all()
                norm=float(np.linalg.norm(vector.astype(np.float64)));assert norm>0 and math.isfinite(norm)
                vector=(vector.astype(np.float64)/norm).astype(np.float32)
                return {'input_tokens':count,'dimensions':512,'vector_sha256':hashlib.sha256(vector.tobytes()).hexdigest()}
        else:
            import torch
            torch.set_num_threads(args.threads)
            from tokenizers import Tokenizer
            from aether_agent_memory.remember.basic.llmlingua import LLMLinguaPreprocessor
            from aether_agent_memory.remember.basic.policy import RememberPolicy
            policy=RememberPolicy(llmlingua_model=str(model),llmlingua_keep_rate=args.keep_rate)
            engine=LLMLinguaPreprocessor(policy)
            counter=Tokenizer.from_file(str(model/'tokenizer.json'));counter.no_truncation();counter.no_padding()
            weights=sorted(p for p in model.rglob('*') if p.is_file() and p.suffix in ('.safetensors','.bin'))
            if not weights:raise FileNotFoundError('local compression weights missing')
            def sha_file(p):
                h=hashlib.sha256()
                with p.open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
                return h.hexdigest()
            identity={'adapter':engine.model_identity['adapter'],'keep_rate':args.keep_rate,
                'weights_sha256':digest([sha_file(p) for p in weights]),
                'tokenizer_sha256':sha_file(model/'tokenizer.json'),
                'model_configuration_sha256':digest([(str(p.relative_to(model)),sha_file(p)) for p in sorted(model.rglob('*.json'))])}
            from importlib.metadata import version
            identity['runtime_versions']={name:version(name) for name in ['torch','transformers','llmlingua','tokenizers']}
            def operate(text,usage):
                output=engine.compress(text);output.validate_source(text)
                return {'input_tokens':len(counter.encode(text).ids),'output_tokens':len(counter.encode(output.text).ids),
                    'output_bytes':len(output.text.encode()),'output_sha256':hashlib.sha256(output.text.encode()).hexdigest(),
                    '_text':output.text}
        event({'type':'initialized','seconds':time.perf_counter()-started,'identity':identity})
        # Warm-up is separate from measurement; for lazy LLMLingua this includes model load.
        warm=time.perf_counter();operate(rows[0]['text'],'Query' if args.kind=='embedding' else 'Compression')
        event({'type':'warmup','seconds':time.perf_counter()-warm})
        event({'type':'measurement_start'})
        for index,(repeat,usage,row) in enumerate(workload(rows,args)):
            event({'type':'operation_start','index':index})
            started=time.perf_counter()
            result={'type':'sample','index':index,'dataset_id':row['dataset_id'],'repeat':repeat,'usage':usage,
                'input_bytes':len(row['text'].encode()),'input_sha256':hashlib.sha256(row['text'].encode()).hexdigest()}
            try:
                output=operate(row['text'],usage)
                result['seconds']=time.perf_counter()-started
                if args.kind=='compression':
                    text=output.pop('_text');facts=row.get('critical_phrases',[]) or ([row['expected_fact']] if row.get('expected_fact') else [])
                    result['fact_probe']=fact_probe(text,facts)
                    # Retention failure is a failed compression, even if the output is small.
                    result['status']='FAIL' if result['fact_probe']['status']=='FAIL' else 'PASS'
                else:result['status']='PASS'
                result.update(output)
            except Exception as exc:
                result.update(status='FAIL',seconds=time.perf_counter()-started,error_type=type(exc).__name__)
                if isinstance(exc,EmbeddingInputTooLong):result.update(input_tokens=exc.count,max_input_tokens=exc.limit)
            event(result)
        event({'type':'measurement_end'});return 0
    except (ImportError,FileNotFoundError) as exc:
        event({'type':'blocked','reason':type(exc).__name__});return 2
    except Exception as exc:
        event({'type':'failed','reason':type(exc).__name__});return 1
    finally:
        if engine is not None and args.kind=='embedding':engine.shutdown()
        try:
            import resource
            usage=resource.getrusage(resource.RUSAGE_SELF)
            event({'type':'resources','cpu_user_seconds':usage.ru_utime,'cpu_system_seconds':usage.ru_stime,
                'peak_rss_bytes':usage.ru_maxrss*(1 if sys.platform=='darwin' else 1024)})
        except ImportError:pass


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind',choices=['embedding','compression'],required=True)
    p.add_argument('--dataset',required=True);p.add_argument('--model-path',required=True)
    p.add_argument('--output',required=True);p.add_argument('--hardware-id',required=True)
    p.add_argument('--candidate',required=True,help='fixed source SHA or deployed image digest')
    p.add_argument('--repeats',type=int,default=3);p.add_argument('--threads',type=int,default=1)
    p.add_argument('--max-seconds',type=int,default=180);p.add_argument('--keep-rate',type=float,default=.8)
    p.add_argument('--baseline');p.add_argument('--events',help=argparse.SUPPRESS)
    p.add_argument('--min-items-per-second',type=float);p.add_argument('--max-p95-ms',type=float)
    p.add_argument('--min-byte-factor',type=float);p.add_argument('--min-token-factor',type=float)
    args=p.parse_args()
    if not 1<=args.repeats<=5 or not 1<=args.threads<=4 or not 1<=args.max_seconds<=600 or not 0<args.keep_rate<=1:
        p.error('repeats 1..5, threads 1..4, max-seconds 1..600, keep-rate (0,1]')
    thresholds={'min_items_per_second':args.min_items_per_second,'max_p95_ms':args.max_p95_ms,
        'min_byte_factor':args.min_byte_factor,'min_token_factor':args.min_token_factor}
    if args.kind=='embedding' and (args.min_byte_factor is not None or args.min_token_factor is not None):p.error('compression thresholds require --kind compression')
    threshold_gate({'status':'BLOCKED'},thresholds)  # Validate before model execution.
    if args.events:return worker(args)
    rows=validate_dataset(json.loads(Path(args.dataset).read_text(encoding='utf-8')))
    run_id=str(uuid.uuid4());out=Path(args.output)/run_id;out.mkdir(parents=True,exist_ok=False)
    events=out/'events.jsonl'
    events.touch()
    command=[sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--events',str(events.resolve())]
    stopped=False;begin=time.perf_counter()
    try:
        with (out/'worker.log').open('w',encoding='utf-8') as f:
            completed=subprocess.run(command,stdout=f,stderr=f,timeout=args.max_seconds)
        exit_code=completed.returncode
    except subprocess.TimeoutExpired:stopped=True;exit_code=124
    finish=time.perf_counter();all_events=[]
    if events.exists():
        for line in events.read_text(encoding='utf-8').splitlines():
            try:all_events.append(json.loads(line))
            except json.JSONDecodeError:stopped=True
    if exit_code!=0 and not all_events:
        all_events.append({'type':'blocked' if stopped else 'failed','reason':'worker_timeout_before_events' if stopped else 'worker_exited_without_events'})
    tasks=workload(rows,args)
    samples=finish_samples(all_events,tasks,finish)
    start=next((e['monotonic'] for e in all_events if e['type']=='measurement_start'),None)
    end=next((e['monotonic'] for e in all_events if e['type']=='measurement_end'),finish)
    metrics=summarize(samples,len(tasks),end-start if start else 0)
    identity=next((e for e in all_events if e['type']=='initialized'),{})
    report={'schema_version':1,'run_id':run_id,'kind':args.kind,'status':metrics['status'],'metrics':metrics,
        'candidate':args.candidate,'hardware_id':args.hardware_id,'platform':platform.platform(),
        'dataset_sha256':digest(rows),'input_unique_hashes':len({hashlib.sha256(r['text'].encode()).hexdigest() for r in rows}),
        'threads':args.threads,'concurrency':1,'batch_size':1,'repeats':args.repeats,'model':identity.get('identity'),
        'initialization_seconds':identity.get('seconds'),
        'warmup_seconds':next((e['seconds'] for e in all_events if e['type']=='warmup'),None),
        'wall_seconds':finish-begin,'worker_exit_code':exit_code,'budget_stop':stopped,'samples':samples,
        'release_gate':'BLOCKED','scope':'offline CPU component; no async ingestion, storage, cache, network or capacity SLA',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'resources':next((e for e in all_events if e['type']=='resources'),None),
        'diagnostics':[e for e in all_events if e['type'] in ('blocked','failed')]}
    if exit_code!=0:
        report['status']='FAIL' if any(e['type']=='failed' for e in all_events) or metrics['failed'] else 'BLOCKED'
    if args.kind=='compression':
        by_index={r['index']:r for r in samples}
        cohort=[by_index.get(i,{'status':'BLOCKED','input_bytes':len(row['text'].encode())}) for i,(_,_,row) in enumerate(tasks)]
        report['compression']=compression_metrics(cohort)
    report['component_gate']=threshold_gate({**metrics,**report.get('compression',{}),'status':report['status']},thresholds)
    if report['component_gate']['status']=='FAIL':report['status']='FAIL'
    elif any(v is not None for v in thresholds.values()) and report['component_gate']['status']=='BLOCKED' and report['status']=='PASS':report['status']='BLOCKED'
    # Compare different code candidates only when measured model/workload/hardware agree.
    report['groups']={}
    for usage in sorted({task[1] for task in tasks}):
        subset=[r for r in samples if r['usage']==usage]
        report['groups'][usage]=summarize(subset,sum(t[1]==usage for t in tasks),sum(r['seconds'] for r in subset))
        report['groups'][usage]['throughput_boundary']='sum of operation time only; excludes between-operation overhead'
    report['comparison_key']=digest({k:report[k] for k in ['kind','hardware_id','platform','dataset_sha256','threads','concurrency','batch_size','repeats','model','script_sha256']})
    if args.baseline:report['comparison']=compare(report,json.loads(Path(args.baseline).read_text(encoding='utf-8')))
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=[f'# {args.kind} component benchmark',f'Status: {report["status"]}; release: BLOCKED',
        f'Run: {run_id}',f'Valid: {metrics["completed_valid"]}/{metrics["planned"]}; failed: {metrics["failed"]}; incomplete: {metrics["not_completed"]}',
        f'Valid throughput: {metrics["valid_items_per_second"]} items/s',
        f'P50/P95/P99: {metrics["p50_ms"]}/{metrics["p95_ms"]}/{metrics["p99_ms"]} ms',report['scope']]
    if args.kind=='compression':lines.append(json.dumps(report['compression'],ensure_ascii=False))
    (out/'agent-report.md').write_text('\n\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'run_id':run_id,'status':report['status'],'report':str(out/'summary.json')}))
    return 0 if report['status']=='PASS' else (1 if report['status']=='FAIL' else 2)


if __name__=='__main__':sys.exit(main())
