import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'python/simulate_reads.py'


class SimulateReadsTest(unittest.TestCase):
    def invoke(self, root, rows, policy, latency='2', bandwidth='4', extra=None):
        with (root / 'trace.csv').open('w', newline='', encoding='utf-8') as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return subprocess.run([sys.executable, str(SCRIPT), '--trace', str(root / 'trace.csv'),
                               '--policy', policy, '--latency-us', latency,
                               '--bandwidth-gbps', bandwidth, '--outdir', str(root / policy), *(extra or [])],
                              capture_output=True, text=True)

    def row(self, seq, degree, capacity=32):
        return dict(query_id=0, access_seq=seq, node_id=1, graph_level=0,
                    degree=degree, capacity=capacity, snapshot_id=1, phase='trace')

    def test_fixed_and_header_actual_hand_counts_and_model_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for policy, expected in [('fixed', (132, 44, 88, 1, 0, 2.264)),
                                     ('header_actual', (44, 44, 0, 2, 1, 4.088))]:
                with self.subTest(policy=policy):
                    result = self.invoke(root, [self.row(0, 10)], policy)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    with (root / policy / 'accesses.csv').open() as source:
                        row = next(csv.DictReader(source))
                    for field, value in zip(['requested_bytes', 'useful_bytes', 'wasted_bytes',
                                             'read_ops', 'dependent_reads'], expected[:5]):
                        self.assertEqual(int(row[field]), value)
                    self.assertAlmostEqual(float(row['estimated_latency_us']), expected[5])
                    summary = json.loads((root / policy / 'summary.json').read_text(encoding='utf-8'))
                    self.assertEqual(summary['measurement'], 'trace_replay_model')
                    self.assertEqual(summary['bandwidth_unit'], 'decimal Gbit/s')

    def test_empty_layer_avoids_second_read_and_repeats_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.invoke(root, [self.row(0, 0), self.row(1, 32), self.row(2, 32)], 'header_actual')
            self.assertEqual(result.returncode, 0, result.stderr)
            with (root / 'header_actual/accesses.csv').open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual([int(r['read_ops']) for r in rows], [1, 2, 2])
            self.assertEqual([int(r['requested_bytes']) for r in rows], [4, 132, 132])
            self.assertEqual(len(rows), 3)

    def test_cached_replay_uses_history_and_preserves_it_across_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            trace=[self.row(i,d) for i,d in enumerate([8,8,10,3,0,3])]
            trace[2]['snapshot_id']=2
            trace[3]['snapshot_id']=2
            trace[4]['snapshot_id']=2;trace[4]['graph_level']=1
            trace[5]['snapshot_id']=2
            result=self.invoke(root,trace,'cached_length',extra=['--cache-capacity','2'])
            self.assertEqual(result.returncode,0,result.stderr)
            with (root/'cached_length/accesses.csv').open() as source:rows=list(csv.DictReader(source))
            self.assertEqual([int(r['requested_bytes']) for r in rows],[36,36,44,44,4,16])
            self.assertEqual([int(r['fallbacks']) for r in rows],[1,0,1,0,0,0])
            self.assertEqual([int(r['cache_hits']) for r in rows],[0,1,1,1,0,1])
            self.assertEqual([int(r['cold_miss_fallbacks']) for r in rows],[1,0,0,0,0,0])
            self.assertEqual([int(r['hint_underestimates']) for r in rows],[0,0,1,0,0,0])
            result=self.invoke(root,trace,'cached_length',extra=['--cache-capacity','0'])
            self.assertEqual(result.returncode,0,result.stderr)
            with (root/'cached_length/accesses.csv').open() as source:rows=list(csv.DictReader(source))
            self.assertTrue(all(int(r['cache_hits'])==0 for r in rows))

    def test_prefix_replay_predicts_classes_before_observing_degree(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            trace=[self.row(i,d) for i,d in enumerate([3,7,8,9,10,3,0,10])]
            result=self.invoke(root,trace,'prefix',extra=['--prefix-granularity','8'])
            self.assertEqual(result.returncode,0,result.stderr)
            with (root/'prefix/accesses.csv').open() as source:rows=list(csv.DictReader(source))
            self.assertEqual([int(r['requested_bytes']) for r in rows],[36,36,36,40,68,68,36,44])
            self.assertEqual([int(r['fallbacks']) for r in rows],[0,0,0,1,0,0,0,1])
            self.assertEqual([int(r['cache_hits']) for r in rows],[0,1,1,1,1,1,1,1])
            self.assertTrue(all(int(r['prefix_reads'])==1 for r in rows))
            for g in [1,2,4,8,16,64]:
                result=self.invoke(root,[self.row(0,3,7)],'prefix',extra=['--prefix-granularity',str(g)])
                self.assertEqual(result.returncode,0,result.stderr)
                with (root/'prefix/accesses.csv').open() as source:row=next(csv.DictReader(source))
                self.assertEqual(int(row['requested_bytes']),4+4*max(3,min(g,7)))
            result=self.invoke(root,[self.row(0,3)],'prefix',extra=['--prefix-granularity','0'])
            self.assertNotEqual(result.returncode,0)

    def test_thread_local_replay_does_not_mix_readers_or_warmup_phases(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            trace=[{**self.row(0,8),'thread_id':0,'phase':'warmup'},
                   {**self.row(0,2),'thread_id':1,'phase':'warmup'},
                   {**self.row(1,10),'thread_id':0,'phase':'measured'},
                   {**self.row(1,2),'thread_id':1,'phase':'measured'}]
            result=self.invoke(root,trace,'cached_length')
            self.assertEqual(result.returncode,0,result.stderr)
            with (root/'cached_length/accesses.csv').open() as source:rows=list(csv.DictReader(source))
            self.assertEqual([int(r['requested_bytes']) for r in rows],[36,12,44,12])
            self.assertEqual([int(r['read_ops']) for r in rows],[2,2,2,1])
            self.assertEqual([int(r['cache_hits']) for r in rows],[0,0,1,1])
            summary=json.loads((root/'cached_length/summary.json').read_text())
            self.assertEqual(summary['phase_totals']['measured']['read_ops'],3)
            self.assertEqual(summary['phase_totals']['warmup']['read_ops'],4)

    def test_corrupt_degree_access_order_and_model_parameters_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [([self.row(0, 33)], '2', '4', 'degree'),
                     ([self.row(1, 10)], '2', '4', 'access_seq'),
                     ([self.row(0, 10)], 'nan', '4', 'finite'),
                     ([self.row(0, 10)], '2', '0', 'positive')]
            for rows, latency, bandwidth, message in cases:
                with self.subTest(message=message):
                    result = self.invoke(root, rows, 'fixed', latency, bandwidth)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)


if __name__ == '__main__':
    unittest.main()
