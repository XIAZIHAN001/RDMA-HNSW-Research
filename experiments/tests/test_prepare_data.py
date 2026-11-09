import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "python" / "prepare_data.py"


def write_fixture(path, rows):
    with open(path, "wb") as output:
        for row in rows:
            output.write(np.array([len(row)], dtype="<i4").tobytes())
            output.write(np.array(row, dtype="<f4").tobytes())


class PrepareDataTest(unittest.TestCase):
    def test_cli_preserves_vectors_and_computes_exact_neighbors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root / "base.fvecs", [[0, 0], [1, 0], [2, 0]])
            write_fixture(root / "query.fvecs", [[0, 0], [2, 0], [0.5, 0]])
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--base", str(root / "base.fvecs"),
                 "--queries", str(root / "query.fvecs"), "--outdir", str(root / "out"),
                 "--k", "2", "--dataset-name", "hand_checked_fixture"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            ids = np.load(root / "out" / "ground_truth.npy")
            self.assertEqual(ids.tolist(), [[0, 1], [2, 1], [0, 1]])
            manifest = json.loads((root / "out" / "manifest.json").read_text())
            self.assertEqual((manifest["node_count"], manifest["query_count"], manifest["dim"]),
                             (3, 3, 2))
            self.assertEqual((root / "out" / "base.fvecs").read_bytes(),
                             (root / "base.fvecs").read_bytes())
            self.assertEqual(len(manifest["base_sha256"]), 64)

    def test_subset_truth_is_recomputed_for_selected_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root / "base.fvecs", [[0, 0], [1, 0], [2, 0]])
            write_fixture(root / "query.fvecs", [[2, 0]])
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--base", str(root / "base.fvecs"),
                 "--queries", str(root / "query.fvecs"), "--outdir", str(root / "out"),
                 "--base-count", "2", "--k", "2"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(np.load(root / "out" / "ground_truth.npy").tolist(), [[1, 0]])

    def test_truncated_vector_is_rejected_before_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "base.fvecs").write_bytes(np.array([2, 0], dtype="<i4").tobytes())
            write_fixture(root / "query.fvecs", [[0, 0]])
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--base", str(root / "base.fvecs"),
                 "--queries", str(root / "query.fvecs"), "--outdir", str(root / "out"),
                 "--k", "1"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("truncated fvecs", result.stderr)
            self.assertFalse((root / "out" / "manifest.json").exists())

    def test_query_dimension_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root / "base.fvecs", [[0, 0]])
            write_fixture(root / "query.fvecs", [[0, 0, 0]])
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--base", str(root / "base.fvecs"),
                 "--queries", str(root / "query.fvecs"), "--outdir", str(root / "out"),
                 "--k", "1"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("query dimension mismatch", result.stderr)

    def test_output_directory_cannot_replace_source_vectors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root / 'base.fvecs', [[0, 0], [1, 0], [2, 0]])
            write_fixture(root / 'query.fvecs', [[0, 0]])
            original = (root / 'base.fvecs').read_bytes()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), '--base', str(root / 'base.fvecs'),
                 '--queries', str(root / 'query.fvecs'), '--outdir', str(root),
                 '--base-count', '2', '--k', '1'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('output path collision', result.stderr)
            self.assertEqual((root / 'base.fvecs').read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
