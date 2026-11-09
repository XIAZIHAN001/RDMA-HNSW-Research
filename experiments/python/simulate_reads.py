"""Replay ordered adjacency accesses using explicitly assumed READ latency/bandwidth."""
import argparse
from collections import Counter, OrderedDict
import csv
import json
import math
from pathlib import Path
import sys

from prepare_data import check_outputs, sha256_file

METRICS = ['requested_bytes', 'useful_bytes', 'wasted_bytes', 'read_ops',
           'dependent_reads', 'header_reads', 'estimated_latency_us', 'cache_hits', 'cache_misses',
           'cache_evictions', 'fallbacks', 'cold_miss_fallbacks', 'hint_underestimates', 'prefix_reads']


def model_access(degree, capacity, policy, latency_us, bandwidth_gbps, prediction=0, hit=False):
    useful = 4 + degree * 4
    if policy == 'fixed':
        requested = 4 + capacity * 4
        operations, dependent, headers = 1, 0, 0
    elif policy == 'header_actual':
        requested = useful
        operations, dependent, headers = 1 + int(degree > 0), int(degree > 0), 1
    elif policy in {'cached_length', 'prefix'}:
        requested = 4 + max(degree, prediction) * 4
        dependent = int(degree > prediction)
        operations, headers = 1 + dependent, int(prediction == 0)
    else:
        raise ValueError('policy is not implemented: ' + policy)
    # Decimal Gbit/s -> bytes per microsecond. No search/vector cost is modeled.
    bytes_per_us = bandwidth_gbps * 125
    estimated = operations * latency_us + requested / bytes_per_us
    if not math.isfinite(estimated):
        raise ValueError('estimated latency must be finite')
    return dict(requested_bytes=requested, useful_bytes=useful,
                wasted_bytes=requested - useful, read_ops=operations,
                dependent_reads=dependent, header_reads=headers, estimated_latency_us=estimated,
                cache_hits=int(policy in {'cached_length', 'prefix'} and hit),
                cache_misses=int(policy in {'cached_length', 'prefix'} and not hit), cache_evictions=0,
                fallbacks=dependent if policy in {'cached_length', 'prefix'} else 0,
                cold_miss_fallbacks=int(policy in {'cached_length', 'prefix'} and dependent and not hit),
                hint_underestimates=int(policy in {'cached_length', 'prefix'} and dependent and hit), prefix_reads=int(policy == 'prefix'))


def replay(rows, policy, latency_us, bandwidth_gbps, cache_capacity=10000, cache_mode='warm', prefix_granularity=8):
    if not math.isfinite(latency_us) or not math.isfinite(bandwidth_gbps):
        raise ValueError('model parameters must be finite')
    if latency_us < 0 or bandwidth_gbps <= 0:
        raise ValueError('latency must be nonnegative and bandwidth must be positive')
    if cache_capacity < 0 or cache_mode not in {'warm', 'cold'}:
        raise ValueError('invalid cache parameters')
    if prefix_granularity <= 0:
        raise ValueError('prefix granularity must be positive')
    caches, previous_snapshots, next_sequences = {}, {}, {}
    outputs = []
    for row in rows:
        thread_id = int(row.get('thread_id', 0))
        if thread_id < 0:
            raise ValueError('negative thread_id')
        seq = next_sequences.get(thread_id, 0)
        if int(row['access_seq']) != seq:
            raise ValueError('access_seq must retain the complete original order per reader')
        next_sequences[thread_id] = seq + 1
        cache = caches.setdefault(thread_id, OrderedDict())
        for field in ['query_id', 'node_id', 'graph_level', 'snapshot_id']:
            if int(row[field]) < 0:
                raise ValueError('negative trace field: ' + field)
        degree, capacity = int(row['degree']), int(row['capacity'])
        if not 0 <= degree <= capacity <= 0xffffffff:
            raise ValueError('degree/capacity is outside the valid record range')
        snapshot = int(row['snapshot_id'])
        previous_snapshot = previous_snapshots.get(thread_id)
        if cache_mode == 'cold' and previous_snapshot is not None and snapshot != previous_snapshot:
            cache.clear()
        previous_snapshots[thread_id] = snapshot
        key = int(row['node_id']), int(row['graph_level'])
        hit = key in cache
        prediction = (min(cache[key] * prefix_granularity if hit else prefix_granularity, capacity)
                      if policy == 'prefix' else min(cache[key], capacity) if hit else 0)
        if hit:
            cache.move_to_end(key)
        metrics = model_access(degree, capacity, policy, latency_us, bandwidth_gbps, prediction, hit)
        if policy in {'cached_length', 'prefix'} and cache_capacity:
            cache[key] = ((degree + prefix_granularity - 1) // prefix_granularity
                          if policy == 'prefix' else degree)
            cache.move_to_end(key)
            while len(cache) > cache_capacity:
                cache.popitem(last=False)
                metrics['cache_evictions'] += 1
        outputs.append({**{field: row[field] for field in
                           ['query_id', 'access_seq', 'node_id', 'graph_level',
                            'degree', 'capacity', 'snapshot_id', 'phase']},
                        'thread_id': thread_id, 'policy': policy, **metrics})
    return outputs


def write_rows(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--policy', choices=['fixed', 'header_actual', 'cached_length', 'prefix'], required=True)
    parser.add_argument('--latency-us', type=float, required=True,
                        help='Assumed per-READ fixed latency in microseconds, not measured RDMA')
    parser.add_argument('--bandwidth-gbps', type=float, required=True,
                        help='Assumed bandwidth in decimal gigabits per second')
    parser.add_argument('--cache-capacity', type=int, default=10000)
    parser.add_argument('--prefix-granularity', type=int, default=8)
    parser.add_argument('--cache-mode', choices=['warm', 'cold'], default='warm')
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        check_outputs([args.trace], [args.outdir / name for name in
                                    ['accesses.csv', 'queries.csv', 'summary.json']])
        with args.trace.open(newline='', encoding='utf-8') as source:
            reader = csv.DictReader(source)
            required = {'query_id', 'access_seq', 'node_id', 'graph_level',
                        'degree', 'capacity', 'snapshot_id', 'phase'}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError('trace is missing required fields')
            trace = list(reader)
        rows = replay(trace, args.policy, args.latency_us, args.bandwidth_gbps, args.cache_capacity, args.cache_mode, args.prefix_granularity)
        total = Counter()
        queries, phases, readers = {}, {}, {}
        for row in rows:
            query_id = int(row['query_id'])
            group = queries.setdefault((row['thread_id'], row['phase'], query_id), Counter())
            phase = phases.setdefault(row['phase'], Counter())
            reader = readers.setdefault(row['thread_id'], Counter())
            group['access_count'] += 1
            for field in METRICS:
                total[field] += row[field]
                group[field] += row[field]
                phase[field] += row[field]
                reader[field] += row[field]
        summary = dict(measurement='trace_replay_model', scope='adjacency_reads_only',
                       policy=args.policy, prefix_granularity=args.prefix_granularity, cache_capacity=args.cache_capacity, cache_mode=args.cache_mode, access_count=len(rows), query_count=len(queries),
                       latency_us=args.latency_us, bandwidth_gbps=args.bandwidth_gbps,
                       bandwidth_unit='decimal Gbit/s', calibration='user_configured_assumption',
                       assumptions='Serial per-reader READ costs L + bytes/B, summed work not parallel wall latency; excludes vectors, search, CPU and overlap',
                       totals={field: total[field] for field in METRICS},
                       phase_totals={name: dict(counts) for name, counts in phases.items()},
                       reader_totals={name: dict(counts) for name, counts in readers.items()},
                       trace=dict(path=str(args.trace.resolve()), sha256=sha256_file(args.trace)),
                       snapshots=sorted({int(row['snapshot_id']) for row in rows}),
                       cache_policies='cached_length and prefix implemented with node+level LRU and historical hints')
        args.outdir.mkdir(parents=True, exist_ok=True)
        write_rows(args.outdir / 'accesses.csv', rows,
                   ['query_id', 'access_seq', 'node_id', 'graph_level', 'degree', 'capacity',
                    'snapshot_id', 'phase', 'thread_id', 'policy', *METRICS])
        write_rows(args.outdir / 'queries.csv',
                   [dict(thread_id=thread_id, phase=phase, query_id=query_id, **dict(counts))
                    for (thread_id, phase, query_id), counts in queries.items()],
                   ['thread_id', 'phase', 'query_id', 'access_count', *METRICS])
        (args.outdir / 'summary.json').write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps(dict(policy=args.policy, prefix_granularity=args.prefix_granularity, cache_capacity=args.cache_capacity, cache_mode=args.cache_mode, access_count=len(rows), totals=summary['totals'])))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as error:
        print('simulate_reads: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
