import csv
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import numpy as np
from test_lab_client import vectors

CLIENT = Path(os.environ['LAB_DYNAMIC_CLIENT'])
BUILDER = Path(os.environ['HNSW_BUILD'])

class DynamicClientTest(unittest.TestCase):
    def test_updates_and_retained_hints_are_correct_in_each_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vectors(root / 'base.fvecs', [[i, 0] for i in range(6)])
            vectors(root / 'vectors.fvecs', [[i, 0] for i in range(7)])
            vectors(root / 'query.fvecs', [[i + .125, 0] for i in range(6)])
            built = subprocess.run([str(BUILDER), '--input', str(root / 'base.fvecs'), '--dim', '2',
                                    '--M', '4', '--output', str(root / 'initial.bin')], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            operations = []
            for epoch in range(4):
                if epoch:
                    operations.append(dict(seq=len(operations), type=['erase','upsert','insert'][epoch-1],
                                           vector_ref=[0,5,6][epoch-1], node_id=[5,5,6][epoch-1], epoch=epoch))
                for query in range(6):
                    operations.append(dict(seq=len(operations), type='query', vector_ref=query, node_id=0, epoch=epoch))
            with (root / 'workload.csv').open('w', newline='') as output:
                writer=csv.DictWriter(output, fieldnames=['seq','type','vector_ref','node_id','epoch'])
                writer.writeheader();writer.writerows(operations)
            observed = {}
            for policy, mode in [('fixed','warm'),('header_actual','warm'),('cached_length','warm'),('cached_length','cold'),('prefix','warm'),('prefix','cold')]:
                name=policy+'_'+mode
                shutil.copyfile(root/'initial.bin',root/f'{name}.bin')
                result=subprocess.run([str(CLIENT),'--index',str(root/f'{name}.bin'),'--queries',str(root/'query.fvecs'),
                                       '--vectors',str(root/'vectors.fvecs'),'--workload',str(root/'workload.csv'),
                                       '--results',str(root/f'{name}.csv'),'--read-policy',policy,
                                       '--cache-mode',mode,'--cache-capacity','10000','--ef-search','32','--k','3','--verify'],
                                       capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                with (root/f'{name}.csv').open() as source: rows=list(csv.DictReader(source))
                self.assertEqual(len(rows),27)
                queries=[row for row in rows if row['type']=='query']
                self.assertTrue(all(float(row['verified_recall'])==1 for row in queries))
                self.assertEqual({row['snapshot_id'] for row in queries},{'1','2','3','4'})
                observed[name]=[row['result_ids'] for row in queries]
                if policy=='cached_length' and mode=='warm':
                    after=[row for row in queries if int(row['snapshot_id'])>=3]
                    self.assertGreater(sum(int(row['hint_underestimates']) for row in after),0)
                    self.assertGreater(sum(int(row['cache_hits']) for row in after),0)
                if policy=='cached_length' and mode=='cold':
                    first=[row for row in queries if row['vector_ref']=='0']
                    self.assertTrue(all(int(row['cache_hits'])==0 for row in first))
            self.assertTrue(all(value==observed['fixed_warm'] for value in observed.values()))

    def test_preflight_errors_preserve_index_and_create_no_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vectors(root/'vectors.fvecs',[[0,0],[1,0],[2,0]])
            subprocess.run([str(BUILDER),'--input',str(root/'vectors.fvecs'),'--dim','2','--M','2',
                            '--output',str(root/'index.bin')],check=True,capture_output=True)
            original=(root/'index.bin').read_bytes()
            for row in ['0,insert,0,2,0','0,erase,0,3,0','1,query,0,0,0','0,query,3,0,0','0,unknown,0,0,0']:
                (root/'workload.csv').write_text('seq,type,vector_ref,node_id,epoch\n'+row+'\n')
                result=subprocess.run([str(CLIENT),'--index',str(root/'index.bin'),'--queries',str(root/'vectors.fvecs'),
                                       '--vectors',str(root/'vectors.fvecs'),'--workload',str(root/'workload.csv'),
                                       '--results',str(root/'out.csv')],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual((root/'index.bin').read_bytes(),original)
                self.assertFalse((root/'out.csv').exists())

if __name__=='__main__': unittest.main()
