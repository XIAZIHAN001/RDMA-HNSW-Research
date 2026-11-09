"""Check actual compiler commands for consistent upstream/experiment modes."""
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
SOURCE=Path(__file__).resolve().parents[1]
class BuildConfigurationTest(unittest.TestCase):
    def configured(self,extra):
        with tempfile.TemporaryDirectory(prefix='hnsw_config_') as directory:
            run=subprocess.run(['cmake','-S',str(SOURCE),'-B',directory,'-DENABLE_RDMA=OFF','-DENABLE_RDMA_CM=OFF','-DBUILD_TESTING=OFF',*extra],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            commands=json.loads((Path(directory)/'compile_commands.json').read_text())
            return {Path(c['file']).name:shlex.split(c['command']) for c in commands}
    def test_default_uses_release_for_all_comparison_paths(self):
        commands=self.configured([])
        for source in ['read_strategy.cc','header_actual_strategy.cc','cached_length_strategy.cc','prefix_strategy.cc','epoch_client.cc']:
            self.assertIn(source,commands)
            self.assertIn('-O3',commands[source],source)
            self.assertIn('-DNDEBUG',commands[source],source)
    def test_explicit_debug_is_respected_by_both_subprojects(self):
        commands=self.configured(['-DCMAKE_BUILD_TYPE=Debug'])
        for source in ['read_strategy.cc','prefix_strategy.cc','epoch_client.cc']:
            self.assertIn(source,commands);self.assertIn('-g',commands[source]);self.assertNotIn('-O3',commands[source])
if __name__=='__main__':unittest.main()
