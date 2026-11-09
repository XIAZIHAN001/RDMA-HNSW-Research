import csv
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_lab_client import vectors
CLIENT=Path(os.environ['LAB_EPOCH_CLIENT'])
BUILDER=Path(os.environ['HNSW_BUILD'])
class EpochClientTest(unittest.TestCase):
    def test_four_policies_and_two_concurrencies_match_frozen_epoch_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vectors(root/'base.fvecs',[[i,0] for i in range(6)])
            vectors(root/'vectors.fvecs',[[i,0] for i in range(7)])
            vectors(root/'queries.fvecs',[[i+.125,0] for i in range(6)])
            subprocess.run([str(BUILDER),'--input',str(root/'base.fvecs'),'--dim','2','--M','4',
                            '--output',str(root/'initial.bin')],check=True,capture_output=True)
            operations=[]
            for epoch in range(5):
                if 0<epoch<4:
                    operations.append(dict(seq=len(operations),type=['erase','upsert','insert'][epoch-1],
                                           vector_ref=[0,5,6][epoch-1],node_id=[5,5,6][epoch-1],epoch=epoch))
                for query in range(6):operations.append(dict(seq=len(operations),type='query',vector_ref=query,node_id=0,epoch=epoch))
            with (root/'workload.csv').open('w',newline='') as output:
                writer=csv.DictWriter(output,fieldnames=['seq','type','vector_ref','node_id','epoch']);writer.writeheader();writer.writerows(operations)
            reference=None
            for policy in ['fixed','header_actual','cached_length','prefix']:
                for threads in [1,4]:
                    shutil.copyfile(root/'initial.bin',root/'copy.bin')
                    result=subprocess.run([str(CLIENT),'--index',str(root/'copy.bin'),'--queries',str(root/'queries.fvecs'),
                        '--vectors',str(root/'vectors.fvecs'),'--workload',str(root/'workload.csv'),
                        '--results',str(root/'out.csv'),'--epochs',str(root/'epochs.csv'),
                        '--trace',str(root/'trace.csv'),'--threads',str(threads),'--warmup','2',
                        '--read-policy',policy,'--prefix-granularity','1','--cache-capacity','10000','--k','3','--ef-search','32','--verify'],
                        capture_output=True,text=True)
                    self.assertEqual(result.returncode,0,result.stderr)
                    with (root/'out.csv').open() as source:log=list(csv.DictReader(source))
                    self.assertEqual(len(log),33)
                    queries=sorted([r for r in log if r['type']=='query'],key=lambda r:int(r['seq']))
                    self.assertTrue(all(float(r['verified_recall'])==1 for r in queries))
                    self.assertEqual([int(r['snapshot_id']) for r in queries],[1]*12+[2]*6+[3]*6+[4]*6)
                    signature=[r['result_ids'] for r in queries]
                    if reference is None:reference=signature
                    else:self.assertEqual(signature,reference)
                    with (root/'epochs.csv').open() as source:epochs=list(csv.DictReader(source))
                    self.assertEqual(len(epochs),5)
                    self.assertEqual(sum(int(r['query_count']) for r in epochs),30)
                    self.assertEqual(sum(int(r['update_count']) for r in epochs),3)
                    self.assertTrue(all(float(r['query_phase_ms'])>0 for r in epochs))
                    with (root/'trace.csv').open() as source:trace=list(csv.DictReader(source))
                    self.assertTrue(any(r['phase']=='warmup' for r in trace))
                    self.assertEqual({int(r['thread_id']) for r in trace},set(range(threads)))
if __name__=='__main__':unittest.main()
