import importlib.util
from pathlib import Path
import sys
import unittest
import json
import tempfile
import os
import subprocess
import numpy as np
SCRIPT=Path(__file__).resolve().parents[1]/'python/analyze_main_matrix.py'
sys.path.insert(0,str(SCRIPT.parent))
class MainAnalysisTest(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('main_analysis',SCRIPT);self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
    def fixture(self):
        config=dict(nodes=3,policy='fixed',cache_capacity=1000,granularity=8,threads=1,warmup=0,operation_count=3,update_ratio=1/3,ef_search=16,k=1)
        ops=[dict(seq=0,type='query',vector_ref=0,node_id=0,epoch=0),dict(seq=1,type='upsert',vector_ref=0,node_id=0,epoch=0),dict(seq=2,type='query',vector_ref=1,node_id=0,epoch=0)]
        common=dict(thread_id=0,node_count=3,elapsed_ms=1,neighbor_reads=1,neighbor_requested_bytes=20,neighbor_useful_bytes=8,neighbor_wasted_bytes=12,
          neighbor_dependent_reads=0,neighbor_header_reads=0,neighbor_accesses=1,cache_hits=0,cache_misses=0,cache_evictions=0,fallbacks=0,cold_miss_fallbacks=0,
          hint_underestimates=0,capacity_clamps=0,read_ops=3,requested_bytes=40,logical_index_rewrite_bytes=0,policy='fixed',cache_mode='warm',cache_capacity=1000,
          prefix_granularity=8,threads=1,warmup=0,ef_search=16,k=1,transport='mmap',timing_mode='epoch_operation_only',status='ok')
        log=[{**ops[0],**common,'snapshot_id':1,'result_ids':'0','verified_recall':1},
             {**ops[2],**common,'snapshot_id':1,'result_ids':'1','verified_recall':-1},
             {**ops[1],**common,'snapshot_id':2,'result_ids':'','verified_recall':-1,'logical_index_rewrite_bytes':100}]
        for field in ['neighbor_reads','neighbor_requested_bytes','neighbor_useful_bytes','neighbor_wasted_bytes','neighbor_dependent_reads','neighbor_header_reads','neighbor_accesses','read_ops','requested_bytes']:
            log[2][field]=0
        epoch=[dict(epoch=0,snapshot_before=1,snapshot_after=2,query_count=2,update_count=1,query_phase_ms=2,update_phase_ms=1,reload_phase_ms=1,
                    verification_ms=.1,initial_warmup_ms=0,logical_index_rewrite_bytes=100)]
        return log,epoch,ops,config
    def test_frozen_epoch_order_and_separate_cost_scopes(self):
        log,epochs,ops,config=self.fixture();summary=self.module.validate_run(log,epochs,ops,config)
        self.assertEqual(summary['query_count'],2);self.assertEqual(summary['update_count'],1)
        self.assertEqual(summary['query_phase_qps'],1000);self.assertEqual(summary['accounted_mixed_ops_per_second'],750)
        self.assertEqual(summary['recall_checked_query_count'],1)
    def test_corrupt_evidence_is_rejected(self):
        for field,value in [('snapshot_id',99),('policy','prefix'),('status','failed'),('capacity_clamps',1),('result_ids','3'),('neighbor_reads',2)]:
            with self.subTest(field=field):
                log,epochs,ops,config=self.fixture();log[0][field]=value
                with self.assertRaises(ValueError):self.module.validate_run(log,epochs,ops,config)
        log,epochs,ops,config=self.fixture();log[0]['verified_recall']=-1
        with self.assertRaises(ValueError):self.module.validate_run(log,epochs,ops,config)
        log,epochs,ops,config=self.fixture();epochs[0]['logical_index_rewrite_bytes']=1
        with self.assertRaises(ValueError):self.module.validate_run(log,epochs,ops,config)
    def test_metadata_matrix_must_match_frozen_input(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);matrix=folder/'matrix.json'
            original=dict(repeats=3,configs=[])
            matrix.write_text(json.dumps(original))
            metadata=dict(matrix_file=str(matrix),matrix=dict(repeats=1,configs=[]),throughput_scope='test',input_hashes={str(matrix):self.module.sha256_file(matrix)})
            (folder/'metadata.json').write_text(json.dumps(metadata));(folder/'status.json').write_text(json.dumps(dict(runs={})))
            with self.assertRaises(ValueError):self.module.analyze(folder,True)
    def frozen_inputs(self, folder):
        source=folder/'raw';source.mkdir()
        matrix=folder/'matrix.json'
        matrix.write_bytes((SCRIPT.parents[1]/'configs/main-matrix.json').read_bytes())
        code=folder/'client.cc';code.write_bytes(b'int main() { return 0; }\n')
        binary=folder/'client.bin';binary.write_bytes(b'\x7fELF frozen executable\x00')
        inputs=[matrix,code,binary]
        metadata=dict(matrix_file=str(matrix),matrix=json.loads(matrix.read_text()),throughput_scope='test',
                      input_hashes={str(path.resolve()):self.module.sha256_file(path) for path in inputs})
        (source/'metadata.json').write_text(json.dumps(metadata))
        (source/'status.json').write_text(json.dumps(dict(runs={})))
        return source,inputs
    def test_analyzer_cannot_overwrite_frozen_inputs(self):
        for input_index in range(3):
            for hardlink in [False,True]:
                with self.subTest(input_index=input_index,hardlink=hardlink), tempfile.TemporaryDirectory() as directory:
                    folder=Path(directory);source,inputs=self.frozen_inputs(folder)
                    original={path:path.read_bytes() for path in inputs}
                    output=inputs[input_index]
                    if hardlink:
                        output=folder/'report.json';os.link(inputs[input_index],output)
                    result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--source',str(source),
                                           '--output',str(output),'--allow-partial'],capture_output=True,text=True,encoding='utf-8',errors='replace')
                    for path,content in original.items():self.assertEqual(path.read_bytes(),content,str(path))
                    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
    def test_analyzer_can_write_separate_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);source,inputs=self.frozen_inputs(folder)
            original={path:path.read_bytes() for path in inputs};output=folder/'reports/report.json'
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--source',str(source),
                                   '--output',str(output),'--allow-partial'],capture_output=True,text=True,encoding='utf-8',errors='replace')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads(output.read_text());self.assertFalse(report['complete'])
            self.assertEqual(report['complete_runs'],0);self.assertGreater(report['expected_runs'],0)
            for path,content in original.items():self.assertEqual(path.read_bytes(),content)
if __name__=='__main__':unittest.main()
