"""Validate external QA offsets and independently frozen source-gold anchors."""
import argparse
import hashlib
import json
from pathlib import Path


def validate(dataset_path, gold_path):
    if not Path('/.dockerenv').exists():
        raise RuntimeError('Dataset validation must run in Docker')
    data_bytes = Path(dataset_path).read_bytes()
    dataset = json.loads(data_bytes)
    source_gold = json.loads(Path(gold_path).read_bytes())
    qa_count = 0
    anchor_count = 0
    source_bytes = 0
    sample_ids = set()
    for sample in dataset['samples']:
        if sample['id'] in sample_ids:
            raise ValueError('duplicate sample ID')
        sample_ids.add(sample['id'])
        sources = {s['id']: s['text'] for s in sample['sources']}
        assert len(sources) == len(sample['sources'])
        source_bytes += sum(len(t.encode('utf8')) for t in sources.values())
        for qa in sample['gold_qa']:
            qa_count += 1
            text = sources[qa['source_id']]
            for answer in qa['answers']:
                start = answer['answer_start']
                if text[start:start + len(answer['text'])] != answer['text']:
                    raise ValueError('external human QA answer offset does not match source')
        annotated = source_gold['samples'].get(sample['id'], {})
        for item in annotated.get('facts', []) + annotated.get('conditions', []):
            if item['source_quote'] not in sources[item['source_id']]:
                raise ValueError('frozen source-gold quote absent from original: ' + item['id'])
            anchor_count += 1
    if set(source_gold['samples']) - sample_ids:
        raise ValueError('gold references an unknown sample')
    return {'samples': len(sample_ids), 'human_qa_pairs': qa_count,
            'source_gold_anchors': anchor_count, 'raw_utf8_bytes': source_bytes,
            'dataset_sha256': hashlib.sha256(data_bytes).hexdigest(),
            'source_gold_sha256': hashlib.sha256(Path(gold_path).read_bytes()).hexdigest(),
            'quality_benchmark': False, 'validation': 'source_offsets_and_anchors_passed', 'docker': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--gold', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    report = validate(args.dataset, args.gold)
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report), flush=True)
