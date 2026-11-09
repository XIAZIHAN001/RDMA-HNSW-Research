"""Analyze local query logs and validate access traces against an RHNSW snapshot."""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np

from prepare_data import check_outputs, sha256_file
from simulate_reads import replay, METRICS as MODEL_METRICS


def recall_at_k(rows, truth):
    if truth.ndim != 2 or truth.shape[0] == 0 or truth.shape[1] == 0:
        raise ValueError('ground truth must be a nonempty top-k matrix')
    ids = [int(row['query_id']) for row in rows]
    if len(ids) != len(truth) or sorted(ids) != list(range(len(truth))):
        raise ValueError('query IDs must cover ground truth exactly once')
    total = 0.0
    for row in rows:
        predicted = {int(value) for value in row['result_ids'].split(';') if value}
        expected = set(map(int, truth[int(row['query_id'])]))
        total += len(predicted & expected) / truth.shape[1]
    return total / len(rows)


def validate_queries(queries, catalog, truth):
    for row in queries:
        if row['policy'] not in {'fixed', 'header_actual', 'cached_length', 'prefix'}:
            raise ValueError('unsupported query policy')
        if row['status'] != 'ok':
            raise ValueError('query status must be ok')
        if int(row['snapshot_id']) != catalog['generation']:
            raise ValueError('query snapshot_id disagrees with the index')
        if int(row['k']) != truth.shape[1]:
            raise ValueError('query k disagrees with ground truth')
        if int(row['ef_search']) < int(row['k']):
            raise ValueError('query ef_search must be at least k')
        for field in ['neighbor_reads', 'neighbor_requested_bytes',
                      'neighbor_useful_bytes', 'neighbor_wasted_bytes'] + [name for name in
                      ['neighbor_dependent_reads', 'neighbor_header_reads', 'neighbor_accesses',
                       'capacity_clamps', 'cache_hits', 'cache_misses', 'cache_evictions', 'fallbacks',
                       'cold_miss_fallbacks', 'hint_underestimates'] if name in row]:
            if int(row[field]) < 0:
                raise ValueError('negative query metric: ' + field)
        if int(row.get('capacity_clamps', 0)) != 0:
            raise ValueError('nonzero capacity_clamps indicates a corrupt index')
        if int(row['neighbor_requested_bytes']) != (
                int(row['neighbor_useful_bytes']) + int(row['neighbor_wasted_bytes'])):
            raise ValueError('query byte accounting mismatch')
    for field in ['policy', 'ef_search', 'k', 'transport', 'timing_mode'] + [name for name in
                  ['cache_capacity', 'prefix_granularity'] if name in queries[0]]:
        if len({row[field] for row in queries}) != 1:
            raise ValueError('mixed query experiment field: ' + field)
    if queries[0]['timing_mode'] not in {'query_only', 'trace_instrumented'}:
        raise ValueError('unknown timing_mode')
    if queries[0]['transport'] != 'mmap':
        raise ValueError('transport must be mmap for this local analyzer')


def validate_trace_counts(trace, queries):
    metrics = {'read_ops': 'neighbor_reads', 'useful_bytes': 'neighbor_useful_bytes',
               'wasted_bytes': 'neighbor_wasted_bytes', 'requested_bytes': 'neighbor_requested_bytes'}
    metrics.update({field: query_field for field, query_field in
                    [('dependent_reads', 'neighbor_dependent_reads'), ('header_reads', 'neighbor_header_reads'),
                     ('capacity_clamps', 'capacity_clamps')] + [(name, name) for name in
                     ['cache_hits', 'cache_misses', 'cache_evictions', 'fallbacks', 'cold_miss_fallbacks', 'hint_underestimates']] if query_field in queries[0]})
    policy = queries[0]['policy']
    expected_ids = {int(row['query_id']) for row in queries}
    groups = {query_id: Counter() for query_id in expected_ids}
    for row in trace:
        query_id = int(row['query_id'])
        if query_id not in expected_ids:
            raise ValueError('trace contains unknown query_id')
        groups[query_id]['access_count'] += 1
        for field in metrics:
            value = int(row[field])
            if value < 0:
                raise ValueError('negative trace metric: ' + field)
            groups[query_id][field] += value
        if int(row['requested_bytes']) != int(row['useful_bytes']) + int(row['wasted_bytes']):
            raise ValueError('trace byte accounting mismatch')
    for query in queries:
        for field, query_field in metrics.items():
            if groups[int(query['query_id'])][field] != int(query[query_field]):
                raise ValueError('trace and query metric disagree: ' + field)
        if 'neighbor_accesses' in query:
            count = groups[int(query['query_id'])]['access_count']
            if count != int(query['neighbor_accesses']):
                raise ValueError('trace and query neighbor_accesses disagree')

    # Aggregate consistency above is checked first for older logs. Then verify
    # the declared baseline instead of merely accepting self-consistent totals.
    predicted = replay(trace, policy, 0, 1, int(queries[0].get('cache_capacity', 10000)),
                       prefix_granularity=int(queries[0].get('prefix_granularity', 8)))
    for row, model in zip(trace, predicted):
        if 'policy' in row and row['policy'] != policy:
            raise ValueError('trace policy disagrees with query policy')
        for field in MODEL_METRICS:
            if field != 'estimated_latency_us' and field in row and int(row[field]) != model[field]:
                raise ValueError('policy-specific trace accounting mismatch: ' + field)
        if int(row.get('capacity_clamps', 0)) != 0:
            raise ValueError('nonzero capacity_clamps in trace')


def graph_degrees(path):
    # RHNSW v1 serializes native structs. This reader explicitly supports the
    # little-endian x86_64 ABI used by our Linux build, rather than guessing it.
    with open(path, 'rb') as source:
        data = source.read()
    header_format = struct.Struct('<8sIIIII4xQQQQ')
    if len(data) < header_format.size:
        raise ValueError('truncated RHNSW header')
    magic, version, dim, m, max_level, entrypoint, n, generation, offsets, records = (
        header_format.unpack_from(data))
    if (magic != b'RHNSW001' or version != 1 or dim == 0 or m == 0 or
            max_level > 63 or offsets != 64 or records != 64 + n * 8):
        raise ValueError('unsupported or invalid RHNSW x86_64 v1 layout')
    record_size = 24 + dim * 4 + 4 + m * 8 + max_level * (4 + m * 4)
    if len(data) != records + n * record_size:
        raise ValueError('truncated or unexpected RHNSW records')
    rows = []
    for node_id in range(n):
        offset = struct.unpack_from('<Q', data, offsets + node_id * 8)[0]
        if offset != records + node_id * record_size:
            raise ValueError('unexpected RHNSW node offset')
        disk_id, _, level, deleted = struct.unpack_from('<I4xQIB3x', data, offset)
        if disk_id != node_id or level > max_level or deleted not in (0, 1):
            raise ValueError('invalid RHNSW node header')
        cursor = offset + 24 + dim * 4
        for graph_level in range(max_level + 1):
            capacity = m * (2 if graph_level == 0 else 1)
            degree = struct.unpack_from('<I', data, cursor)[0]
            if degree > capacity:
                raise ValueError('degree exceeds reserved capacity')
            if graph_level <= level and not deleted:
                rows.append(dict(node_id=node_id, graph_level=graph_level,
                                 degree=degree, capacity=capacity))
            cursor += 4 + capacity * 4
    return dict(node_count=n, dim=dim, M=m, max_level=max_level,
                entrypoint=entrypoint, generation=generation,
                abi='little_endian_x86_64_v1'), rows


def check_trace(trace, catalog, graph):
    lookup = {(row['node_id'], row['graph_level']): row for row in graph}
    for seq, row in enumerate(trace):
        if int(row['access_seq']) != seq:
            raise ValueError('access_seq must preserve the complete access order')
        if int(row['snapshot_id']) != catalog['generation']:
            raise ValueError('trace snapshot mismatch')
        expected = lookup.get((int(row['node_id']), int(row['graph_level'])))
        if expected is None:
            raise ValueError('trace references an inactive node or layer')
        for name in ['degree', 'capacity']:
            if int(row[name]) != expected[name]:
                raise ValueError(f'trace {name} disagrees with the index')
    return len(trace)


def read_csv(path):
    with open(path, newline='', encoding='utf-8') as source:
        return list(csv.DictReader(source))


def write_csv(path, rows, fields):
    with open(path, 'w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def distribution(rows):
    result = {}
    for level in sorted({int(row['graph_level']) for row in rows}):
        layer = [row for row in rows if int(row['graph_level']) == level]
        degrees = np.array([int(row['degree']) for row in layer])
        capacities = np.array([int(row['capacity']) for row in layer])
        result[str(level)] = dict(count=len(layer), mean_degree=float(degrees.mean()),
                                 mean_utilization=float((degrees / capacities).mean()),
                                 histogram=dict(sorted(Counter(map(int, degrees)).items())))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--truth', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--trace', type=Path)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        check_outputs([args.results, args.truth, args.index] + ([args.trace] if args.trace else []),
                      [args.outdir / name for name in
                       ['graph_degrees.csv', 'node_heat.csv', 'summary.json']])
        queries = read_csv(args.results)
        truth = np.load(args.truth, allow_pickle=False)
        recall = recall_at_k(queries, truth)
        catalog, graph = graph_degrees(args.index)
        validate_queries(queries, catalog, truth)
        elapsed = np.array([float(row['elapsed_ms']) for row in queries])
        if not np.isfinite(elapsed).all() or np.any(elapsed <= 0):
            raise ValueError('invalid query latency')
        requested = sum(int(row['neighbor_requested_bytes']) for row in queries)
        wasted = sum(int(row['neighbor_wasted_bytes']) for row in queries)
        useful = sum(int(row['neighbor_useful_bytes']) for row in queries)
        if requested != wasted + useful:
            raise ValueError('inconsistent neighbor byte accounting')
        summary = dict(measurement='local_mmap', timing_modes=sorted({row['timing_mode'] for row in queries}),
                       query_count=len(queries), k=truth.shape[1], recall_at_k=recall,
                       mean_query_ms=float(elapsed.mean()), p50_ms=float(np.percentile(elapsed, 50)),
                       p95_ms=float(np.percentile(elapsed, 95)), p99_ms=float(np.percentile(elapsed, 99)),
                       query_loop_qps=float(1000 / elapsed.mean()),
                       neighbor_reads=sum(int(row['neighbor_reads']) for row in queries),
                       neighbor_requested_bytes=requested, neighbor_useful_bytes=useful,
                       neighbor_wasted_bytes=wasted, neighbor_waste_fraction=wasted / requested if requested else 0,
                       catalog=catalog, graph_distribution=distribution(graph),
                       files={name: dict(path=str(path.resolve()), sha256=sha256_file(path))
                              for name, path in [('results', args.results), ('truth', args.truth),
                                                 ('index', args.index)]})
        for field in ['neighbor_dependent_reads', 'neighbor_header_reads', 'neighbor_accesses', 'capacity_clamps',
                      'cache_hits', 'cache_misses', 'cache_evictions', 'fallbacks', 'cold_miss_fallbacks', 'hint_underestimates']:
            if field in queries[0]:
                summary[field] = sum(int(row[field]) for row in queries)
        if args.trace:
            trace = read_csv(args.trace)
            checked = check_trace(trace, catalog, graph)
            validate_trace_counts(trace, queries)
            heat = Counter(int(row['node_id']) for row in trace)
            total = len(trace)
            summary.update(trace_rows_verified=checked, access_distribution=distribution(trace),
                           unique_accessed_nodes=len(heat),
                           repeated_node_access_fraction=1 - len(heat) / total if total else 0,
                           top_1_percent_node_access_fraction=sum(count for _, count in
                               heat.most_common(max(1, math.ceil(catalog['node_count'] * .01)))) / total if total else 0)
            summary['files']['trace'] = dict(path=str(args.trace.resolve()), sha256=sha256_file(args.trace))
        # No output files are created until every supplied log is validated.
        args.outdir.mkdir(parents=True, exist_ok=True)
        write_csv(args.outdir / 'graph_degrees.csv', graph,
                  ['node_id', 'graph_level', 'degree', 'capacity'])
        if args.trace:
            write_csv(args.outdir / 'node_heat.csv',
                      [dict(node_id=node, accesses=count) for node, count in heat.most_common()],
                      ['node_id', 'accesses'])
        (args.outdir / 'summary.json').write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps({key: summary[key] for key in
                          ['query_count', 'recall_at_k', 'mean_query_ms', 'neighbor_waste_fraction']}))
        return 0
    except (OSError, ValueError, KeyError, struct.error) as error:
        print('analyze: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
