"""Validate frozen epoch evidence and aggregate independent repeated runs."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np
from prepare_data import check_outputs, sha256_file

COUNTERS = ['neighbor_reads', 'neighbor_requested_bytes', 'neighbor_useful_bytes',
            'neighbor_wasted_bytes', 'neighbor_dependent_reads', 'neighbor_header_reads',
            'neighbor_accesses', 'cache_hits', 'cache_misses', 'cache_evictions',
            'fallbacks', 'cold_miss_fallbacks', 'hint_underestimates', 'capacity_clamps',
            'read_ops', 'requested_bytes']


def integer(value):
    if isinstance(value, int):
        return value
    if not str(value).isdigit():
        raise ValueError('invalid unsigned integer: ' + str(value))
    return int(value)


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('non-finite measurement')
    return result


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(path):
    with path.open(newline='', encoding='utf-8') as source:
        return list(csv.DictReader(source))


def validate_counts(entry, policy):
    c = {key: integer(entry[key]) for key in COUNTERS}
    require(all(v >= 0 for v in c.values()), 'negative counter')
    a, r = c['neighbor_accesses'], c['neighbor_reads']
    require(c['capacity_clamps'] == 0, 'degree exceeds capacity')
    require(c['neighbor_requested_bytes'] == c['neighbor_useful_bytes'] + c['neighbor_wasted_bytes'], 'byte conservation')
    require(c['neighbor_useful_bytes'] >= 4 * a, 'missing degree bytes')
    require(c['read_ops'] >= r and c['requested_bytes'] >= c['neighbor_requested_bytes'], 'total/neighbor scope mismatch')
    require(r >= a and c['neighbor_header_reads'] <= a and c['cache_evictions'] <= a, 'invalid access accounting')
    if policy in ['fixed', 'header_actual']:
        require(all(c[key] == 0 for key in ['cache_hits', 'cache_misses', 'cache_evictions', 'fallbacks', 'cold_miss_fallbacks', 'hint_underestimates']), 'baseline cache counters')
    if policy == 'fixed':
        require(r == a and c['neighbor_dependent_reads'] == 0 and c['neighbor_header_reads'] == 0, 'fixed read accounting')
    elif policy == 'header_actual':
        require(c['neighbor_header_reads'] == a and c['neighbor_dependent_reads'] <= a and r == a + c['neighbor_dependent_reads'] and c['neighbor_wasted_bytes'] == 0, 'header/body read accounting')
    else:
        require(c['cache_hits'] + c['cache_misses'] == a, 'cache lookup accounting')
        require(r == a + c['fallbacks'] and c['neighbor_dependent_reads'] == c['fallbacks'], 'fallback read accounting')
        require(c['fallbacks'] == c['cold_miss_fallbacks'] + c['hint_underestimates'], 'fallback classification')
        require(c['cold_miss_fallbacks'] <= c['cache_misses'] and c['hint_underestimates'] <= c['cache_hits'], 'fallback lookup accounting')
    return c


def validate_run(log, epochs, operations, config, truth=None):
    """Check whole-workload identity, epoch publication and measured scopes."""
    require(config['policy'] in ['fixed', 'header_actual', 'cached_length', 'prefix'], 'unknown policy')
    require(len(log) == len(operations) == config['operation_count'], 'incomplete operations')
    indexed = {integer(entry['seq']): entry for entry in log}
    require(len(indexed) == len(log) and set(indexed) == set(range(len(log))), 'duplicate/missing sequence')
    expected_epochs = defaultdict(list)
    for operation in operations:
        seq = integer(operation['seq'])
        require(seq in indexed, 'unknown workload sequence')
        entry = indexed[seq]
        for key in ['seq', 'vector_ref', 'node_id', 'epoch']:
            require(integer(entry[key]) == integer(operation[key]), 'workload/log mismatch: ' + key)
        require(entry['type'] == operation['type'] and entry['type'] in ['query', 'upsert'], 'workload type mismatch')
        require(entry['status'] == 'ok', 'failed operation')
        for key, value in [('policy', config['policy']), ('cache_mode', 'warm'), ('transport', 'mmap'), ('timing_mode', 'epoch_operation_only')]:
            require(entry[key] == value, 'measurement configuration mismatch: ' + key)
        for key, value in [('cache_capacity', config['cache_capacity']), ('prefix_granularity', config['granularity']),
                           ('threads', config['threads']), ('warmup', config['warmup']), ('ef_search', config['ef_search']), ('k', config['k'])]:
            require(integer(entry[key]) == value, 'measurement configuration mismatch: ' + key)
        require(integer(entry['node_count']) == config['nodes'], 'unexpected node count')
        require(integer(entry['thread_id']) < config['threads'], 'invalid reader id')
        require(number(entry['elapsed_ms']) > 0, 'nonpositive operation time')
        require(0 <= integer(entry['node_id']) < config['nodes'], 'invalid operation target')
        expected_epochs[integer(entry['epoch'])].append(entry)
    require([integer(e['epoch']) for e in epochs] == sorted(expected_epochs), 'missing/reordered epochs')
    require(len({integer(op['seq']) for op in operations}) == len(operations), 'duplicate workload sequence')
    counters = dict.fromkeys(COUNTERS, 0)
    queries, updates, recalls, signature = [], [], [], []
    query_ms = update_ms = reload_ms = verify_ms = warm_ms = 0.0
    rewritten = 0
    generation = 1
    for epoch in epochs:
        entries = sorted(expected_epochs[integer(epoch['epoch'])], key=lambda e: integer(e['seq']))
        qs = [e for e in entries if e['type'] == 'query']
        us = [e for e in entries if e['type'] == 'upsert']
        require(integer(epoch['query_count']) == len(qs) and integer(epoch['update_count']) == len(us), 'epoch counts disagree')
        require(integer(epoch['snapshot_before']) == generation and integer(epoch['snapshot_after']) == generation + len(us), 'epoch publication mismatch')
        for key in ['query_phase_ms', 'update_phase_ms', 'reload_phase_ms', 'verification_ms', 'initial_warmup_ms']:
            require(number(epoch[key]) >= 0, 'negative stage time')
        require(not qs or number(epoch['query_phase_ms']) > 0, 'missing query stage time')
        require(bool(us) == (number(epoch['reload_phase_ms']) > 0), 'reload/update mismatch')
        require(math.isclose(number(epoch['update_phase_ms']), sum(number(e['elapsed_ms']) for e in us), rel_tol=1e-6, abs_tol=1e-5), 'update stage sum mismatch')
        seen_readers = set()
        for ordinal, entry in enumerate(qs):
            require(integer(entry['snapshot_id']) == generation, 'query did not use frozen epoch')
            reader = integer(entry['thread_id'])
            require(reader == ordinal % config['threads'], 'query reader assignment mismatch')
            ids = [integer(i) for i in entry['result_ids'].split(';') if i]
            require(len(ids) == min(config['k'], config['nodes']) and len(set(ids)) == len(ids) and all(i < config['nodes'] for i in ids), 'invalid query results')
            require(integer(entry['logical_index_rewrite_bytes']) == 0, 'query rewrite bytes')
            for key, value in validate_counts(entry, config['policy']).items():
                counters[key] += value
            verified = number(entry['verified_recall'])
            require(verified == -1 or 0 <= verified <= 1, 'invalid verified recall')
            if config['update_ratio'] > 0 and reader not in seen_readers:
                require(verified >= 0, 'missing per-reader epoch truth probe')
            seen_readers.add(reader)
            if truth is not None:
                ref = integer(entry['vector_ref'])
                require(ref < len(truth) and truth.shape[1] >= config['k'], 'truth reference mismatch')
                exact = truth[ref, :config['k']]
                require(len(set(map(int, exact))) == len(exact) and all(0 <= i < config['nodes'] for i in exact), 'invalid exact truth')
                recalls.append(len(set(ids) & set(map(int, exact))) / len(exact))
            elif verified >= 0:
                recalls.append(verified)
            signature.append([integer(entry['seq']), generation, ids])
        for offset, entry in enumerate(us, 1):
            require(integer(entry['snapshot_id']) == generation + offset, 'update publication mismatch')
            require(entry['result_ids'] == '' and number(entry['verified_recall']) == -1, 'update query result')
            require(all(integer(entry[key]) == 0 for key in COUNTERS), 'update query counters')
            require(integer(entry['logical_index_rewrite_bytes']) > 0, 'missing update rewrite bytes')
        epoch_rewrite = sum(integer(e['logical_index_rewrite_bytes']) for e in us)
        require(integer(epoch['logical_index_rewrite_bytes']) == epoch_rewrite, 'rewrite scope mismatch')
        require(epoch is epochs[0] or number(epoch['initial_warmup_ms']) == 0, 'warmup counted more than once')
        generation += len(us)
        queries.extend(qs); updates.extend(us)
        query_ms += number(epoch['query_phase_ms']); update_ms += number(epoch['update_phase_ms'])
        reload_ms += number(epoch['reload_phase_ms']); verify_ms += number(epoch['verification_ms'])
        warm_ms += number(epoch['initial_warmup_ms']); rewritten += epoch_rewrite
    require(queries, 'no measured queries')
    require(len(updates) == round(config['operation_count'] * config['update_ratio']), 'actual update ratio mismatch')
    latencies = np.array([number(e['elapsed_ms']) for e in queries])
    accounted_ms = query_ms + update_ms + reload_ms
    a = counters['neighbor_accesses']
    lookups = counters['cache_hits'] + counters['cache_misses']
    return dict(query_count=len(queries), update_count=len(updates), actual_update_ratio=len(updates)/len(log),
        unique_query_vector_count=len({integer(e['vector_ref']) for e in queries}),
        mean_query_ms=float(latencies.mean()), p50_query_ms=float(np.percentile(latencies, 50)),
        p95_query_ms=float(np.percentile(latencies, 95)), p99_query_ms=float(np.percentile(latencies, 99)),
        query_phase_qps=len(queries)/query_ms*1000, accounted_mixed_ops_per_second=len(log)/accounted_ms*1000,
        mean_update_ms=update_ms/len(updates) if updates else 0, mean_reload_epoch_ms=reload_ms/sum(bool(integer(e['update_count'])) for e in epochs) if updates else 0,
        stage_ms=dict(query=query_ms, update=update_ms, reload=reload_ms, verification_excluded=verify_ms, warmup_excluded=warm_ms),
        update_reload_time_fraction=(update_ms+reload_ms)/accounted_ms, logical_index_rewrite_bytes=rewritten,
        counters=counters, neighbor_bytes_per_query=counters['neighbor_requested_bytes']/len(queries),
        neighbor_reads_per_query=counters['neighbor_reads']/len(queries), total_bytes_per_query=counters['requested_bytes']/len(queries),
        neighbor_byte_fraction=counters['neighbor_requested_bytes']/counters['requested_bytes'] if counters['requested_bytes'] else 0,
        neighbor_waste_fraction=counters['neighbor_wasted_bytes']/counters['neighbor_requested_bytes'] if a else 0,
        cache_hit_rate=counters['cache_hits']/lookups if lookups else 0,
        single_read_completion_rate=1-counters['neighbor_dependent_reads']/a if a else 1,
        stale_hint_repair_per_access=counters['hint_underestimates']/a if a else 0,
        modeled_neighbor_work_us_per_query=(counters['neighbor_reads']*3+counters['neighbor_requested_bytes']*.00008)/len(queries),
        recall_checked_query_count=len(recalls), mean_checked_recall=float(np.mean(recalls)) if recalls else None,
        minimum_checked_recall=min(recalls) if recalls else None,
        quality_sha256=hashlib.sha256(json.dumps(sorted(signature), separators=(',', ':')).encode()).hexdigest())


def analyze(source, allow_partial=False):
    metadata = json.loads((source/'metadata.json').read_text(encoding='utf-8'))
    status = json.loads((source/'status.json').read_text(encoding='utf-8'))
    matrix = metadata['matrix']
    matrix_path = Path(metadata['matrix_file'])
    require(str(matrix_path.resolve()) in metadata['input_hashes'], 'matrix source is not frozen')
    require(json.loads(matrix_path.read_text(encoding='utf-8')) == matrix, 'metadata matrix disagrees with frozen input')
    configs = {c['config_id']: c for c in matrix['configs']}
    require(len(configs) == len(matrix['configs']), 'duplicate matrix ids')
    expected = {f'{cid}_r{r}' for cid in configs for r in range(matrix['repeats'])}
    require(set(status['runs']) <= expected, 'unknown run id')
    if not allow_partial:
        require(set(status['runs']) == expected, 'matrix incomplete')
    for path, digest in metadata['input_hashes'].items():
        require(sha256_file(Path(path)) == digest, 'frozen input changed: ' + path)
    results, per_config, equivalence = {}, defaultdict(list), {}
    for name, record in sorted(status['runs'].items()):
        require(record['status'] == 'complete' and record['returncode'] == 0, 'failed run: ' + name)
        cid = name.split('_r')[0]; c = configs[cid]
        require(record['config'] == c and integer(record['repeat']) == integer(name.split('_r')[1]), 'run identity mismatch')
        workload = source/f"workload_{c['dataset']}_{c['nodes']}_{c['update_ratio']}_{c['distribution']}_{c['operation_count']}.csv"
        for path, digest in [(source/f'{name}.csv', record['result_sha256']), (source/f'{name}_epochs.csv', record['epochs_sha256']), (workload, record['workload_sha256'])]:
            require(sha256_file(path) == digest, 'evidence checksum mismatch: ' + str(path))
        truth = None
        if c['update_ratio'] == 0:
            repo = Path(__file__).resolve().parents[2]
            truth = np.load(repo/f"data/main_{c['dataset']}{c['nodes']}/ground_truth.npy", allow_pickle=False)
        summary = validate_run(rows(source/f'{name}.csv'), rows(source/f'{name}_epochs.csv'), rows(workload),
                               {**c, 'ef_search': matrix['ef_search'], 'k': matrix['k']}, truth)
        summary.update(config=c, repeat=record['repeat'], process_wall_seconds=record['process_wall_seconds'])
        results[name] = summary; per_config[cid].append(summary)
        key = (c['dataset'], c['nodes'], record['workload_sha256'])
        signature = summary['quality_sha256']
        require(key not in equivalence or equivalence[key] == signature, 'policies/threads/repeats returned different results')
        equivalence[key] = signature
    aggregates = {}
    metrics = ['mean_query_ms', 'p50_query_ms', 'p95_query_ms', 'p99_query_ms', 'query_phase_qps',
               'accounted_mixed_ops_per_second', 'mean_update_ms', 'mean_reload_epoch_ms', 'update_reload_time_fraction',
               'neighbor_bytes_per_query', 'neighbor_reads_per_query', 'total_bytes_per_query', 'neighbor_byte_fraction',
               'neighbor_waste_fraction', 'cache_hit_rate', 'single_read_completion_rate', 'stale_hint_repair_per_access',
               'modeled_neighbor_work_us_per_query', 'mean_checked_recall']
    for cid, summaries in sorted(per_config.items()):
        require(all(s['counters'] == summaries[0]['counters'] for s in summaries), 'repeated deterministic counters differ')
        aggregates[cid] = dict(config=configs[cid], repeats=len(summaries), counters=summaries[0]['counters'],
            query_count_per_run=summaries[0]['query_count'], update_count_per_run=summaries[0]['update_count'],
            recall_checked_query_count=sum(s['recall_checked_query_count'] for s in summaries),
            metrics={key: dict(mean=statistics.mean(s[key] for s in summaries), sample_sd=statistics.stdev(s[key] for s in summaries) if len(summaries)>1 else None) for key in metrics})
    return dict(measurement='local_mmap_epoch', complete=len(results)==len(expected), complete_runs=len(results), expected_runs=len(expected),
                metadata=metadata, runs=results, configs=aggregates, quality_comparison_groups=len(equivalence),
                all_comparison_results_identical=True,
                assumptions=dict(model_latency_us=3, model_bandwidth_gbps=100, model_scope='neighbor READ work only; hypothetical serialized work, not parallel wall latency or calibrated RDMA',
                    recall_scope='all static query instances checked against exact truth; dynamic first query per active reader per epoch checked in C++ against current snapshot',
                    tail_scope='mean and sample SD of three per-run percentiles; exploratory tails, 1000-query vector pool reused',
                    throughput_scope=metadata['throughput_scope'], hardware_rdma_measured=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-partial', action='store_true')
    args=parser.parse_args()
    metadata=json.loads((args.source/'metadata.json').read_text(encoding='utf-8'))
    inputs=list(args.source.glob('*')) + list((Path(__file__).resolve().parents[2]/'data').glob('main_*/*'))
    inputs.extend(Path(path) for path in metadata['input_hashes'])
    inputs.append(Path(metadata['matrix_file']))
    check_outputs(inputs, [args.output])
    evidence=analyze(args.source, args.allow_partial)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(f"validated_runs={evidence['complete_runs']}/{evidence['expected_runs']} comparison_groups={evidence['quality_comparison_groups']}")


if __name__ == '__main__':
    main()
