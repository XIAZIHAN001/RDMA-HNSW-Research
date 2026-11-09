import csv
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

REPO = Path(__file__).resolve().parents[2]
BUILDER = Path(os.environ.get('HNSW_BUILD', REPO / 'build/hnsw_build'))
PILOT = Path(os.environ.get('LAB_UPDATE_PILOT', REPO / 'experiments/build/lab_update_pilot'))


class UpdatePilotTest(unittest.TestCase):
    def test_five_updates_publish_snapshots_and_measure_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / 'query.fvecs').open('wb') as output:
                for row in [[0.1, 0.2], [0.3, 0.4]]:
                    output.write(np.array([2], dtype='<i4').tobytes())
                    output.write(np.array(row, dtype='<f4').tobytes())
            built = subprocess.run([str(BUILDER), '--generate', '10', '--dim', '2',
                                    '--M', '2', '--output', str(root / 'index.bin')],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            try:
                result = subprocess.run([str(PILOT), '--index', str(root / 'index.bin'),
                                         '--vectors', str(root / 'query.fvecs'),
                                         '--results', str(root / 'updates.csv')],
                                        capture_output=True, text=True)
            except OSError as error:
                self.fail(str(error))
            self.assertEqual(result.returncode, 0, result.stderr)
            with (root / 'updates.csv').open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual([int(row['generation']) for row in rows], [2, 3, 4, 5, 6])
            self.assertTrue(all(float(row['update_ms']) >= 0 for row in rows))
            self.assertTrue(all(float(row['reload_ms']) >= 0 for row in rows))
            self.assertTrue(all(int(row['peak_rss_kib']) > 0 for row in rows))


if __name__ == '__main__':
    unittest.main()
