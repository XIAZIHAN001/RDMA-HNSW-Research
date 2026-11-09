"""Readiness must follow actual devices, linkage and compile flags."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'python/rdma_preflight.py'
sys.path.insert(0,str(SCRIPT.parent))

class PreflightTest(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('rdma_preflight',SCRIPT)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

    def evidence(self):
        return dict(platform='Linux',devices=['test_device'],cm_device=True,
                    backend_options=dict(ENABLE_RDMA='ON',ENABLE_RDMA_CM='ON',CMAKE_BUILD_TYPE='Release'),
                    compiler_flags=['-O3 -DNDEBUG']*3,
                    linkage=dict(returncode=0,stdout='libibverbs.so.1\nlibrdmacm.so.1'),
                    binary_checks={name:dict(executable=True,linkage=dict(returncode=0,
                        stdout='libibverbs.so.1\nlibrdmacm.so.1')) for name in self.module().BINARIES},
                    binaries_present=True)

    def test_missing_device_or_actual_backend_is_never_ready(self):
        module=self.module()
        self.assertTrue(module.assess(self.evidence())['local_ready'])
        changes=[dict(devices=[]),dict(cm_device=False),dict(binaries_present=False),
                 dict(linkage=dict(returncode=0,stdout='libc.so.6')),
                 dict(linkage=dict(returncode=0,stdout='libibverbs.so.1 => not found\nlibrdmacm.so.1')),
                 dict(compiler_flags=['-g']*3),dict(platform='Windows'),
                 dict(backend_options=dict(ENABLE_RDMA='ON',ENABLE_RDMA_CM='OFF',CMAKE_BUILD_TYPE='Release'))]
        for change in changes:
            with self.subTest(change=change):
                evidence=self.evidence();evidence.update(change)
                result=module.assess(evidence)
                self.assertFalse(result['local_ready']);self.assertTrue(result['blockers'])
                self.assertFalse(result['remote_connection_verified'])

    def test_each_required_program_must_link_the_actual_backend(self):
        module=self.module()
        for name in module.BINARIES:
            with self.subTest(program=name):
                evidence=self.evidence()
                evidence['binary_checks'][name]['linkage']['stdout']='libc.so.6'
                self.assertFalse(module.assess(evidence)['local_ready'])

    def test_cli_preserves_build_input_even_when_used_as_output(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);cache=folder/'CMakeCache.txt';cache.write_bytes(b'keep cache\n')
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--build-dir',str(folder),'--output',str(cache)],capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual(cache.read_bytes(),b'keep cache\n')

    def test_empty_build_is_unavailable_with_saved_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);output=folder/'report.json'
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--build-dir',str(folder/'absent'),'--output',str(output)],capture_output=True)
            self.assertEqual(result.returncode,2,result.stderr)
            report=json.loads(output.read_text());self.assertFalse(report['local_ready'])
            self.assertFalse(report['remote_connection_verified'])

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux executable permissions')
    def test_collect_checks_every_programs_execute_permission(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for name in module.BINARIES:
                path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'not a real program')
                path.chmod(0o755)
            (folder/'lab_client').chmod(0o644)
            report=module.collect(folder)
            self.assertFalse(report['evidence']['binaries_present'])

if __name__=='__main__':unittest.main()
