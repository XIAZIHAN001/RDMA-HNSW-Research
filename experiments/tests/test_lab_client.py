import csv
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

REPO = Path(__file__).resolve().parents[2]
CLIENT = Path(os.environ.get("LAB_CLIENT", REPO / "experiments/build/lab_client"))
BUILDER = Path(os.environ.get("HNSW_BUILD", REPO / "build/hnsw_build"))


def vectors(path, rows):
    with path.open("wb") as output:
        for row in rows:
            output.write(np.array([len(row)], dtype="<i4").tobytes())
            output.write(np.array(row, dtype="<f4").tobytes())


def run_client(args):
    try:
        return subprocess.run([str(CLIENT), *args], capture_output=True, text=True)
    except OSError as error:
        return subprocess.CompletedProcess([], 127, "", str(error))


class LabClientTest(unittest.TestCase):
    def test_query_results_and_trace_counts_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vectors(root / "base.fvecs", [[0, 0], [1, 0], [2, 0], [3, 0]])
            vectors(root / "query.fvecs", [[0, 0], [3, 0]])
            built = subprocess.run([str(BUILDER), "--input", str(root / "base.fvecs"),
                                    "--dim", "2", "--M", "2", "--output",
                                    str(root / "index.bin")], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = run_client(["--index", str(root / "index.bin"),
                                 "--queries", str(root / "query.fvecs"),
                                 "--results", str(root / "queries.csv"),
                                 "--trace", str(root / "trace.csv"),
                                 "--ef-search", "16", "--k", "2"])
            self.assertEqual(result.returncode, 0, result.stderr)
            with (root / "queries.csv").open() as source:
                queries = list(csv.DictReader(source))
            self.assertEqual([row["result_ids"] for row in queries], ["0;1", "3;2"])
            self.assertEqual([row["timing_mode"] for row in queries],
                             ["trace_instrumented", "trace_instrumented"])
            for row in queries:
                self.assertEqual(row['snapshot_id'], '1')
                self.assertEqual(row['status'], 'ok')
                self.assertEqual(row['policy'], 'fixed')
                self.assertEqual(row['ef_search'], '16')
                self.assertEqual(row['k'], '2')
            with (root / "trace.csv").open() as source:
                trace = list(csv.DictReader(source))
            self.assertTrue(trace)
            self.assertEqual({row["query_id"] for row in trace}, {"0", "1"})
            for query in queries:
                rows = [row for row in trace if row["query_id"] == query["query_id"]]
                self.assertEqual(sum(int(row["requested_bytes"]) for row in rows),
                                 int(query["neighbor_requested_bytes"]))
            for row in trace:
                self.assertLessEqual(int(row["degree"]), int(row["capacity"]))
                self.assertEqual(int(row["requested_bytes"]),
                                 int(row["useful_bytes"]) + int(row["wasted_bytes"]))

    def test_missing_queries_does_not_create_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_client(["--index", str(root / "missing.bin"),
                                 "--results", str(root / "queries.csv")])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--queries is required", result.stderr)
            self.assertFalse((root / "queries.csv").exists())

    def test_header_actual_matches_fixed_results_and_request_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vectors(root / 'base.fvecs', [[0, 0], [1, 0], [2, 0], [3, 0]])
            vectors(root / 'query.fvecs', [[0, 0], [3, 0]])
            built = subprocess.run([str(BUILDER), '--input', str(root / 'base.fvecs'),
                                    '--dim', '2', '--M', '2', '--output', str(root / 'index.bin')],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            results = {}
            traces = {}
            for policy in ['fixed', 'header_actual']:
                result = run_client(['--index', str(root / 'index.bin'), '--queries', str(root / 'query.fvecs'),
                                     '--results', str(root / f'{policy}.csv'), '--trace', str(root / f'{policy}_trace.csv'),
                                     '--read-policy', policy, '--ef-search', '16', '--k', '2'])
                self.assertEqual(result.returncode, 0, result.stderr)
                with (root / f'{policy}.csv').open() as source:
                    results[policy] = list(csv.DictReader(source))
                with (root / f'{policy}_trace.csv').open() as source:
                    traces[policy] = list(csv.DictReader(source))
            self.assertEqual([r['result_ids'] for r in results['fixed']],
                             [r['result_ids'] for r in results['header_actual']])
            self.assertEqual(len(traces['fixed']), len(traces['header_actual']))
            for fixed, header in zip(traces['fixed'], traces['header_actual']):
                for field in ['node_id', 'graph_level', 'degree', 'capacity', 'access_seq']:
                    self.assertEqual(fixed[field], header[field])
                degree = int(header['degree'])
                self.assertEqual(int(header['requested_bytes']), 4 + degree * 4)
                self.assertEqual(int(header['read_ops']), 1 + (degree > 0))
                self.assertEqual(int(header['dependent_reads']), int(degree > 0))
                self.assertEqual(int(header['wasted_bytes']), 0)
            self.assertTrue(all(row['policy'] == 'header_actual' for row in results['header_actual']))

    def test_cached_length_cli_preserves_results_with_cache_disabled_and_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vectors(root/'base.fvecs', [[0,0],[1,0],[2,0],[3,0]])
            vectors(root/'query.fvecs', [[.125,0],[3.125,0],[.125,0]])
            subprocess.run([str(BUILDER),'--input',str(root/'base.fvecs'),'--dim','2','--M','2',
                            '--output',str(root/'index.bin')],check=True,capture_output=True)
            for capacity in [0,1000,10000,100000]:
                result=run_client(['--index',str(root/'index.bin'),'--queries',str(root/'query.fvecs'),
                                   '--results',str(root/'out.csv'),'--trace',str(root/'trace.csv'),
                                   '--read-policy','cached_length','--cache-capacity',str(capacity),'--k','2','--ef-search','16'])
                self.assertEqual(result.returncode,0,result.stderr)
                with (root/'out.csv').open() as source:rows=list(csv.DictReader(source))
                self.assertEqual([row['result_ids'] for row in rows],['0;1','3;2','0;1'])
                self.assertTrue(all(row['cache_capacity']==str(capacity) for row in rows))
                hits=sum(int(row['cache_hits']) for row in rows)
                self.assertEqual(hits==0,capacity==0)

    def test_prefix_cli_returns_complete_results_for_five_granularities(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vectors(root/'base.fvecs',[[i,0] for i in range(8)])
            vectors(root/'query.fvecs',[[.125,0],[6.125,0],[.125,0]])
            subprocess.run([str(BUILDER),'--input',str(root/'base.fvecs'),'--dim','2','--M','2',
                            '--output',str(root/'index.bin')],check=True,capture_output=True)
            for g in [1,2,4,8,16]:
                result=run_client(['--index',str(root/'index.bin'),'--queries',str(root/'query.fvecs'),
                                   '--results',str(root/'out.csv'),'--read-policy','prefix',
                                   '--prefix-granularity',str(g),'--k','2','--ef-search','16'])
                self.assertEqual(result.returncode,0,result.stderr)
                with (root/'out.csv').open() as source:rows=list(csv.DictReader(source))
                self.assertEqual([row['result_ids'] for row in rows],['0;1','6;7','0;1'])
                self.assertTrue(all(row['prefix_granularity']==str(g) for row in rows))

    def test_outputs_cannot_overwrite_inputs_or_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vectors(root / "base.fvecs", [[0, 0], [1, 0]])
            vectors(root / "query.fvecs", [[0, 0]])
            built = subprocess.run([str(BUILDER), "--input", str(root / "base.fvecs"),
                                    "--dim", "2", "--M", "2", "--output",
                                    str(root / "index.bin")], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            originals = {name: (root / name).read_bytes()
                         for name in ["query.fvecs", "index.bin"]}
            for results, trace in [("query.fvecs", "trace.csv"),
                                   ("results.csv", "index.bin"),
                                   ("same.csv", "same.csv")]:
                with self.subTest(results=results, trace=trace):
                    for name, content in originals.items():
                        (root / name).write_bytes(content)
                    result = run_client(["--index", str(root / "index.bin"),
                                         "--queries", str(root / "query.fvecs"),
                                         "--results", str(root / results),
                                         "--trace", str(root / trace), "--k", "1"])
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("output path collision", result.stderr)
                    for name, content in originals.items():
                        self.assertEqual((root / name).read_bytes(), content)

    def test_incomplete_header_and_nonfinite_queries_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            built = subprocess.run([str(BUILDER), '--generate', '10', '--dim', '2',
                                    '--M', '2', '--output', str(root / 'index.bin')],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for row, suffix, message in [([0, 0], b'x', 'truncated fvecs'),
                                         ([float('nan'), 0], b'', 'non-finite')]:
                with self.subTest(message=message):
                    vectors(root / 'query.fvecs', [row])
                    with (root / 'query.fvecs').open('ab') as output:
                        output.write(suffix)
                    result = run_client(['--index', str(root / 'index.bin'),
                                         '--queries', str(root / 'query.fvecs'),
                                         '--results', str(root / 'result.csv'), '--k', '1'])
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
