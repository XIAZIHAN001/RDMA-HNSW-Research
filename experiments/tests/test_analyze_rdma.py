"""Hand-checked RDMA CSV cases reject wrong snapshots, scopes and accounting."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import numpy as np

SCRIPT=Path(__file__).resolve().parents[1]/'python/analyze_rdma.py'
sys.path.insert(0,str(SCRIPT.parent))

class RdmaAnalysisTest(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('analyze_rdma',SCRIPT)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

    def fixture(self,mode='static',policy='fixed',threads=1):
        case=dict(case_id=('epoch'+str(threads) if mode=='epoch' else mode)+'_'+policy,mode=mode,policy=policy,threads=threads)
        config=dict(nodes=3,dim=2,query_count=2,operation_count=3,k=2,ef_search=16,granularity=8,cache_capacity=10000,
                    warmup=0,cache_mode='warm',initial_generation=7)
        common=dict(elapsed_ms=1,neighbor_reads=1,neighbor_requested_bytes=20,neighbor_useful_bytes=12,
            neighbor_wasted_bytes=8,read_ops=3,requested_bytes=84,neighbor_dependent_reads=0,neighbor_header_reads=0,
            capacity_clamps=0,neighbor_accesses=1,cache_hits=0,cache_misses=0,cache_evictions=0,fallbacks=0,
            cold_miss_fallbacks=0,hint_underestimates=0,cache_capacity=10000,prefix_granularity=8,policy=policy,
            ef_search=16,k=2,transport='rdma',rdma_connection='cm',status='ok',transport_read_ops=3,
            transport_read_bytes=84,transport_write_ops=0,transport_write_bytes=0,snapshot_id=7)
        if policy!='fixed':
            common.update(neighbor_reads=2,neighbor_requested_bytes=12,neighbor_wasted_bytes=0,read_ops=4,
                          requested_bytes=76,neighbor_dependent_reads=1,neighbor_header_reads=1,transport_read_ops=4,transport_read_bytes=76)
        if policy in ['cached_length','prefix']:
            common.update(cache_misses=1,fallbacks=1,cold_miss_fallbacks=1)
        if mode=='static':
            log=[{**common,'query_id':i,'result_ids':ids,'timing_mode':'query_only'} for i,ids in enumerate(['0;1','1;2'])]
            return log,[],[],case,config,np.array([[0,1],[1,2]],dtype=np.uint32)
        ops=[dict(seq=0,type='query',vector_ref=0,node_id=0,epoch=0),dict(seq=1,type='upsert',vector_ref=1,node_id=1,epoch=0),
             dict(seq=2,type='query',vector_ref=1,node_id=0,epoch=0)]
        scope='remote_submit_publish_catalog_then_reinject' if mode=='dynamic' else 'remote_submit_publish_catalog'
        common.update(node_count=3,cache_mode='warm',update_timing_scope=scope,
                      timing_mode='correctness_instrumented' if mode=='dynamic' else 'epoch_operation_only')
        log=[{**common,**ops[0],'snapshot_before':7,'reload_ms':0,'result_ids':'0;1','verified_recall':1,'thread_id':0},
             {**common,**ops[1],'snapshot_before':7,'snapshot_id':8,'reload_ms':.2,'elapsed_ms':2,'result_ids':'','verified_recall':-1,
              'thread_id':0,'transport_read_ops':1,'transport_read_bytes':80,'transport_write_ops':1,'transport_write_bytes':64},
             {**common,**ops[2],'snapshot_before':8,'snapshot_id':8 if mode=='dynamic' else 7,'reload_ms':0,
              'result_ids':'1;2','verified_recall':1,'thread_id':1 if threads==4 else 0}]
        for key in ['neighbor_reads','neighbor_requested_bytes','neighbor_useful_bytes','neighbor_wasted_bytes','read_ops','requested_bytes',
                    'neighbor_dependent_reads','neighbor_header_reads','neighbor_accesses','cache_hits','cache_misses','cache_evictions',
                    'fallbacks','cold_miss_fallbacks','hint_underestimates','capacity_clamps']:log[1][key]=0
        epochs=[]
        if mode=='epoch':
            for row in log:row.update(threads=threads,warmup=0,logical_index_rewrite_bytes=0,rewrite_measurement='not_collected')
            log=[log[0],log[2],log[1]]
            epochs=[dict(epoch=0,snapshot_before=7,snapshot_after=8,query_count=2,update_count=1,query_phase_ms=2.5,
                update_phase_ms=2,reload_phase_ms=.2,verification_ms=.5,initial_warmup_ms=.1,logical_index_rewrite_bytes=0,
                rewrite_measurement='not_collected',reload_transport_read_ops=threads*2,reload_transport_read_bytes=threads*80)]
        return log,epochs,ops,case,config,None

    def test_all_policies_and_three_real_schemas(self):
        module=self.module()
        for mode,threads in [('static',1),('dynamic',1),('epoch',1),('epoch',4)]:
            for policy in ['fixed','header_actual','cached_length','prefix']:
                with self.subTest(mode=mode,threads=threads,policy=policy):
                    log,epochs,ops,case,config,truth=self.fixture(mode,policy,threads)
                    result=module.validate_case(log,epochs,ops,case,config,truth)
                    self.assertEqual(result['query_count'],2);self.assertEqual(result['minimum_recall'],1)
                    self.assertFalse(result['hardware_connection_verified'])
                    if mode=='epoch':
                        self.assertEqual(result['query_phase_qps'],800)
                        self.assertIsNone(result['server_rewrite_bytes'])

    def test_wrong_transport_counts_quality_and_parameters_rejected(self):
        module=self.module()
        changes=[('transport','mmap'),('rdma_connection','ib'),('status','failed'),('elapsed_ms','nan'),
                 ('snapshot_id',99),('policy','prefix'),('ef_search',15),('result_ids','0;0'),('result_ids','0;3'),
                 ('transport_write_ops',1),('transport_read_ops',2),('transport_read_bytes',43),
                 ('neighbor_requested_bytes',19),('query_id',1)]
        for key,value in changes:
            with self.subTest(key=key,value=value):
                log,epochs,ops,case,config,truth=self.fixture();log[0][key]=value
                with self.assertRaises(ValueError):module.validate_case(log,epochs,ops,case,config,truth)
        log,epochs,ops,case,config,truth=self.fixture();log[0]['result_ids']='0;2'
        with self.assertRaises(ValueError):module.validate_case(log,epochs,ops,case,config,truth)

    def test_missing_updates_probes_and_epoch_publication_rejected(self):
        module=self.module()
        for mode in ['dynamic','epoch']:
            for change in ['missing','snapshot','write','verification','scope','order']:
                with self.subTest(mode=mode,change=change):
                    log,epochs,ops,case,config,truth=self.fixture(mode)
                    update=next(row for row in log if row['type']=='upsert')
                    if change=='missing':log.pop()
                    if change=='snapshot':update['snapshot_id']=7
                    if change=='write':update['transport_write_ops']=0
                    if change=='verification':log[0]['verified_recall']=-1
                    if change=='scope':update['update_timing_scope']='local_apply'
                    if change=='order':log.reverse()
                    with self.assertRaises(ValueError):module.validate_case(log,epochs,ops,case,config,truth)
        for key,value in [('snapshot_after',7),('query_count',1),('update_phase_ms',3),('rewrite_measurement','local_file_size_sum'),
                          ('logical_index_rewrite_bytes',10),('reload_transport_read_ops',0)]:
            with self.subTest(epoch_field=key):
                log,epochs,ops,case,config,truth=self.fixture('epoch');epochs[0][key]=value
                with self.assertRaises(ValueError):module.validate_case(log,epochs,ops,case,config,truth)

    def write_csv(self,path,entries):
        with path.open('w',newline='') as output:
            writer=csv.DictWriter(output,fieldnames=list(entries[0]));writer.writeheader();writer.writerows(entries)

    def evidence(self,root):
        bundle=root/'bundle';bundle.mkdir();runs=root/'runs';runs.mkdir();binaries=root/'binaries';binaries.mkdir()
        log,epochs,ops,case,config,truth=self.fixture()
        _,_,ops,_,_,_=self.fixture('dynamic')
        self.write_csv(bundle/'workload.csv',ops);np.save(bundle/'ground_truth.npy',truth,allow_pickle=False)
        (bundle/'master').mkdir();(bundle/'master/index.bin').write_bytes(b'test fixture master; not a real index')
        for name in ['base.fvecs','query.fvecs','vectors.fvecs']:(bundle/name).write_bytes(b'fixture input')
        dig=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        executable={}
        for name in ['lab_client','lab_dynamic_client','lab_epoch_client']:
            path=binaries/name;path.write_bytes(b'test fixture executable')
            executable[name]=dict(path=str(path),sha256=dig(path))
        cases=[]
        for mode,threads in [('static',1),('dynamic',1),('epoch',1),('epoch',4)]:
            for policy in ['fixed','header_actual','cached_length','prefix']:
                _,_,_,case,_,_=self.fixture(mode,policy,threads);cases.append(case)
        manifest=dict(schema='rdma_smoke_bundle_v1',measurement='offline_preparation_only',config=config,cases=cases,binaries=executable,
            source_inputs={},files={path.relative_to(bundle).as_posix():dig(path) for path in bundle.rglob('*') if path.is_file()})
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        for case in cases:
            run=runs/case['case_id'];run.mkdir()
            log,epochs,_,_,_,_=self.fixture(case['mode'],case['policy'],case['threads'])
            self.write_csv(run/'results.csv',log)
            if epochs:self.write_csv(run/'epochs.csv',epochs)
            (run/'stdout.txt').write_bytes(b'test fixture stdout');(run/'stderr.txt').write_bytes(b'')
            name={'static':'lab_client','dynamic':'lab_dynamic_client','epoch':'lab_epoch_client'}[case['mode']]
            command=[str(binaries/name),'--host','127.0.0.1','--transport','rdma','--rdma-connection','cm','--port','18515',
                '--ib-device','fixture','--ib-port','1','--rdma-timeout-ms','10000','--queries',str(bundle/'query.fvecs'),
                '--read-policy',case['policy'],'--prefix-granularity','8','--cache-capacity','10000','--ef-search','16',
                '--k','2','--results',str(run/'results.csv')]
            if case['mode']=='static':command+=['--max-queries','2']
            else:command+=['--vectors',str(bundle/'vectors.fvecs'),'--workload',str(bundle/'workload.csv'),'--cache-mode','warm','--verify']
            if case['mode']=='epoch':command+=['--threads',str(case['threads']),'--warmup','0','--epochs',str(run/'epochs.csv')]
            record=dict(schema='rdma_smoke_run_v2',case=case,role='compute',command=command,status='complete',returncode=0,
                bundle_sha256=dig(bundle/'manifest.json'),index_master_sha256=manifest['files']['master/index.bin'],
                binary_sha256=executable[name]['sha256'],evidence_origin='test_fixture',
                execution_paths=dict(bundle=bundle.as_posix(),run=run.as_posix(),executable=(binaries/name).as_posix()),
                outputs={path.name:dig(path) for path in run.iterdir() if path.is_file()})
            (run/'run.json').write_text(json.dumps(record))
        return bundle,runs

    def test_full_fixture_pipeline_never_claims_hardware_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle,runs=self.evidence(root);output=root/'validation.json'
            command=[sys.executable,'-B',str(SCRIPT),'--bundle',str(bundle),'--runs',str(runs),'--output',str(output)]
            result=subprocess.run(command,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse(output.exists())
            result=subprocess.run(command+['--allow-fixture'],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads(output.read_text());self.assertTrue(report['fixture_evidence'])
            self.assertEqual(report['validated_cases'],16);self.assertTrue(report['complete'])
            self.assertFalse(report['hardware_connection_verified']);self.assertFalse(report['hardware_performance_acceptance'])
            for target in [bundle/'manifest.json',bundle/'master/index.bin',root/'binaries/lab_client',runs/'static_fixed/results.csv']:
                original=target.read_bytes();alias=root/('alias_'+target.name);os.link(target,alias)
                for destination in [target,alias]:
                    with self.subTest(destination=destination):
                        bad=[sys.executable,'-B',str(SCRIPT),'--bundle',str(bundle),'--runs',str(runs),
                             '--output',str(destination),'--allow-fixture']
                        result=subprocess.run(bad,capture_output=True);self.assertNotEqual(result.returncode,0)
                        self.assertEqual(target.read_bytes(),original)

    def test_receipt_parameters_and_binary_must_match_recorded_case(self):
        module=self.module()
        for change in ['k','binary','failed','unknown','hash','queries_path','results_path','executable_path','missing_paths']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);bundle,runs=self.evidence(root);path=runs/'static_fixed/run.json'
                record=json.loads(path.read_text())
                if change=='k':record['command'][record['command'].index('--k')+1]='1'
                if change=='binary':record['command'][0]=str(root/'binaries/lab_dynamic_client')
                if change=='failed':record['returncode']=1
                if change=='unknown':record['case']['case_id']='unknown'
                if change=='hash':record['outputs']['results.csv']='0'*64
                if change=='executable_path':record['command'][0]=str(root/'other/lab_client')
                if change=='missing_paths':record.pop('execution_paths')
                if change in ['queries_path','results_path']:
                    flag='--queries' if change=='queries_path' else '--results'
                    unrelated=root/'unrelated';unrelated.mkdir()
                    file=unrelated/('query.fvecs' if change=='queries_path' else 'results.csv');file.write_bytes(b'unrelated input/output')
                    record['command'][record['command'].index(flag)+1]=str(file)
                path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):module.analyze(bundle,runs,allow_fixture=True)

    def test_archived_artifacts_keep_original_execution_paths(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle,runs=self.evidence(root);archive=root/'archive';archive.mkdir()
            shutil.move(str(bundle),str(archive/'bundle'));shutil.move(str(runs),str(archive/'runs'))
            result=module.analyze(archive/'bundle',archive/'runs',allow_fixture=True)
            self.assertEqual(result['validated_cases'],16)

    def test_cross_policy_quality_disagreement_and_missing_cases_rejected(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle,runs=self.evidence(root);path=runs/'static_prefix/run.json'
            path.unlink()
            with self.assertRaises(ValueError):module.analyze(bundle,runs,allow_fixture=True)
            report=module.analyze(bundle,runs,allow_partial=True,allow_fixture=True)
            self.assertFalse(report['comparison_complete']);self.assertEqual(report['validated_cases'],15)
            csv_path=runs/'static_fixed/results.csv';content=csv_path.read_text().replace('0;1','1;0');csv_path.write_text(content)
            receipt=runs/'static_fixed/run.json';record=json.loads(receipt.read_text())
            record['outputs']['results.csv']=hashlib.sha256(csv_path.read_bytes()).hexdigest();receipt.write_text(json.dumps(record))
            with self.assertRaises(ValueError):module.analyze(bundle,runs,allow_partial=True,allow_fixture=True)

    def test_impossible_query_accounting_and_fractional_truth_rejected(self):
        module=self.module()
        for change in ['extra_bytes','extra_ops','zero_reads','wrong_node_bytes','fractional_truth']:
            with self.subTest(change=change):
                log,epochs,ops,case,config,truth=self.fixture()
                if change=='extra_bytes':log[0]['transport_read_bytes']+=1000000;log[0]['transport_read_ops']+=1000
                if change=='extra_ops':log[0]['transport_read_ops']+=1
                if change=='zero_reads':
                    for key in module.COUNTERS+module.TRANSPORT:log[0][key]=0
                if change=='wrong_node_bytes':log[0]['requested_bytes']+=1;log[0]['transport_read_bytes']+=1
                if change=='fractional_truth':truth=np.array([[.9,1.9],[1.9,2.9]],dtype=np.float64)
                with self.assertRaises(ValueError):module.validate_case(log,epochs,ops,case,config,truth)

    def test_large_node_reads_allow_actual_cm_segmentation(self):
        module=self.module();log,epochs,ops,case,config,truth=self.fixture();config['dim']=262144
        # Two node READs of 1MiB+24, each split into two WRs, plus one neighbor READ.
        for entry in log:
            entry.update(requested_bytes=2097220,transport_read_bytes=2097220,transport_read_ops=5)
        self.assertEqual(module.validate_case(log,epochs,ops,case,config,truth)['minimum_recall'],1)

if __name__=='__main__':unittest.main()
