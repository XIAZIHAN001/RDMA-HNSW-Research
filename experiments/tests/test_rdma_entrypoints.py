"""Check actual entrypoints select RDMA and never silently use a local index."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
class RdmaEntrypointsTest(unittest.TestCase):
    def clients(self):return [os.environ[k] for k in ['LAB_CLIENT','LAB_DYNAMIC_CLIENT','LAB_EPOCH_CLIENT']]
    def args(self,client,folder):
        args=['--transport','rdma','--host','127.0.0.1','--ib-device','lab_no_hardware','--rdma-timeout-ms','50','--queries',str(folder/'queries.fvecs'),'--results',str(folder/'results.csv')]
        if 'dynamic' in client or 'epoch' in client:args += ['--vectors',str(folder/'vectors.fvecs'),'--workload',str(folder/'ops.csv')]
        if 'epoch' in client:args += ['--epochs',str(folder/'epochs.csv')]
        return args
    def test_rdma_selection_reaches_backend_and_creates_no_fake_result(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for client in self.clients():
                for policy in ['fixed','header_actual','cached_length','prefix']:
                    with self.subTest(client=client,policy=policy):
                        result=subprocess.run([client,*self.args(client,folder),'--read-policy',policy],capture_output=True,text=True,timeout=5)
                        self.assertNotEqual(result.returncode,0)
                        self.assertNotIn('unknown option',result.stderr)
                        self.assertNotIn('--index is required',result.stderr)
                        self.assertTrue('rdma' in result.stderr.lower() or 'cm backend' in result.stderr.lower(),result.stderr)
                        self.assertFalse((folder/'results.csv').exists())
    def test_rdma_refuses_local_index_before_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);index=folder/'local.bin';index.write_bytes(b'keep')
            for client in self.clients():
                result=subprocess.run([client,*self.args(client,folder),'--index',str(index)],capture_output=True,text=True,timeout=5)
                self.assertNotEqual(result.returncode,0);self.assertIn('RDMA must not use --index',result.stderr)
                self.assertEqual(index.read_bytes(),b'keep');self.assertFalse((folder/'results.csv').exists())
if __name__=='__main__':unittest.main()
