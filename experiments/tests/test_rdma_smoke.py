"""Actual offline preparation and unavailable-device runs preserve their inputs."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_lab_client import vectors

SCRIPT=Path(__file__).resolve().parents[1]/'python/rdma_smoke.py'
sys.path.insert(0,str(SCRIPT.parent))
BUILD=Path(os.environ['LAB_CLIENT']).resolve().parent

class SmokeTest(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('rdma_smoke',SCRIPT)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

    def prepare(self,folder):
        vectors(folder/'base.fvecs',[[i,0] for i in range(6)])
        vectors(folder/'query.fvecs',[[i+.125,0] for i in range(4)])
        result=subprocess.run([sys.executable,'-B',str(SCRIPT),'prepare','--base',str(folder/'base.fvecs'),
            '--queries',str(folder/'query.fvecs'),'--build-dir',str(BUILD),'--outdir',str(folder/'bundle'),
            '--nodes','6','--query-count','4','--operations','10','--k','2','--ef-search','32'],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return folder/'bundle'

    def test_prepare_freezes_real_inputs_and_all_cases_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);bundle=self.prepare(folder);module=self.module()
            manifest=module.load_bundle(bundle)
            self.assertEqual(manifest['measurement'],'offline_preparation_only')
            self.assertFalse(manifest['hardware_connection_verified'])
            self.assertEqual(len(manifest['cases']),16)
            self.assertEqual({c['policy'] for c in manifest['cases']},{'fixed','header_actual','cached_length','prefix'})
            self.assertFalse((bundle/'runs').exists())
            for case in manifest['cases']:
                command=module.case_command(bundle,manifest,case,'compute',folder/'run','203.0.113.7','device',18515)
                self.assertNotIn('--index',command);self.assertIn('--transport',command)
                self.assertEqual(command[command.index('--transport')+1],'rdma')
                if case['mode']!='static':self.assertIn('--verify',command)
            (bundle/'query.fvecs').write_bytes(b'changed')
            with self.assertRaises(ValueError):module.load_bundle(bundle)

    def test_storage_copy_and_reused_outputs_preserve_master(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);bundle=self.prepare(folder);module=self.module()
            original=(bundle/'master/index.bin').read_bytes();run=folder/'run'
            module.reset_storage(bundle,run)
            self.assertEqual((run/'index.bin').read_bytes(),original)
            (run/'index.bin').write_bytes(b'updated disposable copy')
            with self.assertRaises(ValueError):module.reset_storage(bundle,run)
            self.assertEqual((bundle/'master/index.bin').read_bytes(),original)

    def test_missing_host_is_rejected_before_output_or_process(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);bundle=self.prepare(folder);run=folder/'run'
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'run','--role','compute','--case','static_fixed',
                '--bundle',str(bundle),'--outdir',str(run)],capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse(run.exists())

    def test_actual_unavailable_backend_is_failed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);bundle=self.prepare(folder);run=folder/'run'
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'run','--role','compute','--case','static_fixed',
                '--bundle',str(bundle),'--outdir',str(run),'--host','127.0.0.1','--device','lab_no_hardware'],capture_output=True)
            self.assertEqual(result.returncode,1,result.stderr)
            receipt=json.loads((run/'run.json').read_text())
            self.assertEqual(receipt['schema'],'rdma_smoke_run_v2')
            self.assertEqual(receipt['execution_paths'],dict(bundle=bundle.resolve().as_posix(),
                run=run.resolve().as_posix(),executable=(BUILD/'lab_client').resolve().as_posix()))
            self.assertEqual(receipt['status'],'failed');self.assertNotEqual(receipt['returncode'],0)
            self.assertFalse(receipt['hardware_connection_verified']);self.assertFalse((run/'results.csv').exists())
            self.assertNotIn('--index',receipt['command'])

    def test_prepare_refuses_to_replace_existing_input_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);bundle=self.prepare(folder);before=(folder/'base.fvecs').read_bytes()
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'prepare','--base',str(folder/'base.fvecs'),
                '--queries',str(folder/'query.fvecs'),'--build-dir',str(BUILD),'--outdir',str(folder)],capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual((folder/'base.fvecs').read_bytes(),before)

    def test_truth_and_workload_must_be_frozen_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);bundle=self.prepare(folder);module=self.module();path=bundle/'manifest.json'
            original=json.loads(path.read_text())
            for name in ['ground_truth.npy','workload.csv','query.fvecs']:
                with self.subTest(name=name):
                    changed=json.loads(json.dumps(original));del changed['files'][name];path.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):module.load_bundle(bundle)

if __name__=='__main__':unittest.main()
