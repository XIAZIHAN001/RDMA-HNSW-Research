import importlib.util
import csv
from pathlib import Path
import struct
import sys
import subprocess
import tempfile
import unittest

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'python/analyze.py'
sys.path.insert(0, str(SCRIPT.parent))


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('lab_analyze', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_recall_counts_unique_ids_and_matches_query_ids(self):
        truth = np.array([[0, 1], [2, 3]])
        rows = [{'query_id': '1', 'result_ids': '2;2'},
                {'query_id': '0', 'result_ids': '1;4'}]
        self.assertEqual(self.module.recall_at_k(rows, truth), 0.5)
        with self.assertRaisesRegex(ValueError, 'query IDs'):
            self.module.recall_at_k([rows[0], rows[0]], truth)
        with self.assertRaisesRegex(ValueError, 'query IDs'):
            self.module.recall_at_k(rows[:1], truth)

    def test_graph_statistics_exclude_reserved_inactive_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'index.bin'
            # x86_64 v1 ABI: 64-byte index header, 24-byte node header.
            header = struct.pack('<8sIIIII4xQQQQ', b'RHNSW001', 1, 1, 2, 1, 0,
                                 1, 7, 64, 72)
            record = struct.pack('<I4xQIB3x', 0, 1, 0, 0) + struct.pack('<f', 0)
            record += struct.pack('<IIIII', 1, 0, 0, 0, 0)  # base: cap=4
            record += struct.pack('<III', 0, 0, 0)          # reserved upper layer
            path.write_bytes(header + struct.pack('<Q', 72) + record)
            catalog, rows = self.module.graph_degrees(path)
            self.assertEqual(catalog['generation'], 7)
            self.assertEqual(rows, [{'node_id': 0, 'graph_level': 0,
                                    'degree': 1, 'capacity': 4}])
            trace = [{'access_seq': '0', 'node_id': '0', 'graph_level': '0',
                      'degree': '1', 'capacity': '4', 'snapshot_id': '7'}]
            self.assertEqual(self.module.check_trace(trace, catalog, rows), 1)
            trace[0]['degree'] = '2'
            with self.assertRaisesRegex(ValueError, 'degree'):
                self.module.check_trace(trace, catalog, rows)

    def test_truncated_index_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.bin'
            path.write_bytes(b'RHNSW001')
            with self.assertRaisesRegex(ValueError, 'truncated'):
                self.module.graph_degrees(path)

    def fixture(self, root):
        index = root / 'index.bin'
        header = struct.pack('<8sIIIII4xQQQQ', b'RHNSW001', 1, 1, 2, 0, 0,
                             1, 7, 64, 72)
        record = struct.pack('<I4xQIB3x', 0, 1, 0, 0) + struct.pack('<f', 0)
        record += struct.pack('<IIIII', 1, 0, 0, 0, 0)
        index.write_bytes(header + struct.pack('<Q', 72) + record)
        np.save(root / 'truth.npy', np.array([[0]], dtype=np.uint32))
        query = dict(query_id=0, result_ids='0', elapsed_ms=1,
                     neighbor_reads=1, neighbor_requested_bytes=20,
                     neighbor_useful_bytes=8, neighbor_wasted_bytes=12,
                     timing_mode='query_only', snapshot_id=7, status='ok', policy='fixed',
                     ef_search=64, k=1, transport='mmap')
        trace = dict(query_id=0, access_seq=0, node_id=0, graph_level=0, degree=1,
                     capacity=4, snapshot_id=7, phase='trace', requested_bytes=20,
                     useful_bytes=8, wasted_bytes=12, read_ops=1)
        return query, trace

    def write_rows(self, path, rows):
        with path.open('w', newline='', encoding='utf-8') as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def run_analysis(self, root, results=None, trace=None):
        command = [sys.executable, str(SCRIPT), '--index', str(root / 'index.bin'),
                   '--truth', str(root / 'truth.npy'), '--results', str(results or root / 'queries.csv'),
                   '--outdir', str(root / 'out')]
        if trace:
            command += ['--trace', str(trace)]
        return subprocess.run(command, capture_output=True, text=True)

    def test_analyzer_cannot_overwrite_source_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query, _ = self.fixture(root)
            (root / 'out').mkdir()
            source = root / 'out/graph_degrees.csv'
            self.write_rows(source, [query])
            original = source.read_bytes()
            result = self.run_analysis(root, results=source)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('output path collision', result.stderr)
            self.assertEqual(source.read_bytes(), original)

    def test_query_snapshot_status_k_and_parameters_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query, _ = self.fixture(root)
            for field, value in [('snapshot_id', 99), ('status', 'failed'), ('k', 10)]:
                with self.subTest(field=field):
                    row = {**query, field: value}
                    self.write_rows(root / 'queries.csv', [row])
                    result = self.run_analysis(root)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(field, result.stderr)
            np.save(root / 'truth.npy', np.array([[0], [0]], dtype=np.uint32))
            self.write_rows(root / 'queries.csv', [query, {**query, 'query_id': 1, 'ef_search': 128}])
            result = self.run_analysis(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('ef_search', result.stderr)

    def test_trace_extra_missing_and_duplicate_accesses_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query, trace = self.fixture(root)
            self.write_rows(root / 'queries.csv', [query])
            variants = [([trace, {**trace, 'access_seq': 1, 'query_id': 999}], 'query_id', {}),
                        ([{**trace, 'useful_bytes': 4, 'wasted_bytes': 16}], 'useful', {}),
                        ([trace, {**trace, 'access_seq': 1}], 'read_ops',
                         dict(neighbor_requested_bytes=40, neighbor_useful_bytes=16, neighbor_wasted_bytes=24)),
                        ([{**trace, 'useful_bytes': 7}], 'accounting', {}),
                        ([{**trace, 'useful_bytes': -1, 'wasted_bytes': 21}], 'negative', {})]
            for rows, message, overrides in variants:
                with self.subTest(message=message):
                    self.write_rows(root / 'queries.csv', [{**query, **overrides}])
                    self.write_rows(root / 'trace.csv', rows)
                    result = self.run_analysis(root, trace=root / 'trace.csv')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
            # A header-only trace misses the measured access entirely.
            self.write_rows(root / 'queries.csv', [{**query, 'neighbor_requested_bytes': 0,
                                                     'neighbor_useful_bytes': 0, 'neighbor_wasted_bytes': 0}])
            (root / 'trace.csv').write_text(','.join(trace) + '\n', encoding='utf-8')
            result = self.run_analysis(root, trace=root / 'trace.csv')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('read_ops', result.stderr)

    def test_new_strategy_metrics_are_validated_without_breaking_old_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query, trace = self.fixture(root)
            query.update(neighbor_dependent_reads=0, neighbor_header_reads=0,
                         neighbor_accesses=1, capacity_clamps=0)
            trace.update(dependent_reads=0, header_reads=0, capacity_clamps=0)
            for field in ['dependent_reads', 'header_reads', 'capacity_clamps']:
                with self.subTest(field=field):
                    self.write_rows(root / 'queries.csv', [query])
                    self.write_rows(root / 'trace.csv', [{**trace, field: 1}])
                    result = self.run_analysis(root, trace=root / 'trace.csv')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(field, result.stderr)
            self.write_rows(root / 'queries.csv', [{**query, 'capacity_clamps': 1}])
            result = self.run_analysis(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('capacity_clamps', result.stderr)

    def test_policy_and_per_access_counts_must_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query, trace = self.fixture(root)
            for changes, trace_changes in [
                ({'policy': 'unknown'}, {}),
                ({'policy': 'header_actual'}, {'policy': 'fixed'}),
                ({'policy': 'header_actual'}, {'policy': 'header_actual'}),
                ({}, {'policy': 'header_actual'}),
            ]:
                with self.subTest(changes=changes, trace_changes=trace_changes):
                    self.write_rows(root / 'queries.csv', [{**query, **changes}])
                    self.write_rows(root / 'trace.csv', [{**trace, **trace_changes}])
                    result = self.run_analysis(root, trace=root / 'trace.csv')
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('policy', result.stderr)
            # Older fixed traces omit policy/header/dependency columns and remain valid.
            self.write_rows(root / 'queries.csv', [query])
            self.write_rows(root / 'trace.csv', [trace])
            self.assertEqual(self.run_analysis(root, trace=root / 'trace.csv').returncode, 0)

    def test_cached_policy_replay_counts_and_capacity_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            query,trace=self.fixture(root)
            query.update(policy='cached_length',neighbor_reads=2,neighbor_requested_bytes=8,
                         neighbor_wasted_bytes=0,neighbor_header_reads=1,neighbor_dependent_reads=1,
                         neighbor_accesses=1,cache_hits=0,cache_misses=1,cache_evictions=0,fallbacks=1,
                         cold_miss_fallbacks=1,hint_underestimates=0,cache_capacity=0,prefix_granularity=8)
            trace.update(policy='cached_length',read_ops=2,requested_bytes=8,wasted_bytes=0,header_reads=1,
                         dependent_reads=1,cache_hits=0,cache_misses=1,cache_evictions=0,fallbacks=1,
                         cold_miss_fallbacks=1,hint_underestimates=0,prefix_reads=0)
            self.write_rows(root/'queries.csv',[query]);self.write_rows(root/'trace.csv',[trace])
            result=self.run_analysis(root,trace=root/'trace.csv')
            self.assertEqual(result.returncode,0,result.stderr)
            for field in ['cache_hits','cache_misses','fallbacks','cold_miss_fallbacks','hint_underestimates']:
                self.write_rows(root/'trace.csv',[{**trace,field:1-int(trace[field])}])
                result=self.run_analysis(root,trace=root/'trace.csv')
                self.assertNotEqual(result.returncode,0)
                self.assertIn(field,result.stderr)

    def test_prefix_policy_class_counters_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);query,trace=self.fixture(root)
            query.update(policy='prefix',neighbor_reads=1,neighbor_requested_bytes=8,
                         neighbor_wasted_bytes=0,cache_capacity=10000,prefix_granularity=1)
            trace.update(policy='prefix',requested_bytes=8,wasted_bytes=0,cache_hits=0,
                         cache_misses=1,cache_evictions=0,fallbacks=0,cold_miss_fallbacks=0,
                         hint_underestimates=0,dependent_reads=0,header_reads=0,prefix_reads=1)
            self.write_rows(root/'queries.csv',[query]);self.write_rows(root/'trace.csv',[trace])
            result=self.run_analysis(root,trace=root/'trace.csv')
            self.assertEqual(result.returncode,0,result.stderr)
            self.write_rows(root/'trace.csv',[{**trace,'prefix_reads':0}])
            result=self.run_analysis(root,trace=root/'trace.csv')
            self.assertNotEqual(result.returncode,0);self.assertIn('prefix_reads',result.stderr)

    def test_baseline_plotter_preserves_source_evidence(self):
        plotter = SCRIPT.parent / 'plot_baseline_comparison.py'
        evidence = SCRIPT.parents[1] / 'reports/baseline2/evidence.json'
        original = evidence.read_bytes()
        for name in ['baseline_tradeoff.png', 'baseline_tradeoff.svg',
                     'local_and_model_scopes.png', 'local_and_model_scopes.svg']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / name
                source.write_bytes(original)
                result = subprocess.run([sys.executable, str(plotter), '--evidence', str(source),
                                         '--outdir', str(root)], capture_output=True, text=True, encoding='utf-8')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('output path collision', result.stderr)
                self.assertEqual(source.read_bytes(), original)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'evidence.json'
            source.write_bytes(original)
            (root / 'baseline_tradeoff.png').hardlink_to(source)
            result = subprocess.run([sys.executable, str(plotter), '--evidence', str(source),
                                     '--outdir', str(root)], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('output path collision', result.stderr)
            self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
