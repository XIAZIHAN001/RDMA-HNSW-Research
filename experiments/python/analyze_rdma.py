"""Validate recorded RDMA correctness smoke logs; fixture logs never prove hardware."""
import argparse
from collections import defaultdict
import hashlib
import ipaddress
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys

import numpy as np
from analyze_main_matrix import COUNTERS, integer, number, require, rows, validate_counts
from prepare_data import check_outputs, sha256_file
from rdma_smoke import POLICIES, confined, load_bundle

TRANSPORT=['transport_read_ops','transport_read_bytes','transport_write_ops','transport_write_bytes']


def recorded_path(value):
    require(isinstance(value,str) and value,'missing recorded absolute path')
    path=PurePosixPath(value.replace('\\','/'))
    require((path.is_absolute() or PureWindowsPath(value).is_absolute()) and '..' not in path.parts,
            'recorded execution path must be absolute and canonical')
    return path


def validate_command(command,binary,case,config,execution_paths):
    require(isinstance(execution_paths,dict) and set(execution_paths)=={'bundle','run','executable'},
            'missing original execution paths')
    paths={key:recorded_path(value) for key,value in execution_paths.items()}
    require(isinstance(command,list) and command and all(isinstance(value,str) for value in command),
            'invalid recorded command')
    require(recorded_path(command[0])==paths['executable'] and paths['executable'].name==binary,'wrong recorded executable')
    options={};index=1
    while index<len(command):
        key=command[index];require(key.startswith('--') and key not in options,'duplicate/invalid command option')
        if key=='--verify':options[key]=True;index+=1;continue
        require(index+1<len(command) and not command[index+1].startswith('--'),'missing command option value')
        options[key]=command[index+1];index+=2
    fixed={'--transport':'rdma','--rdma-connection':'cm','--read-policy':case['policy'],
           '--prefix-granularity':str(config['granularity']),'--cache-capacity':str(config['cache_capacity']),
           '--ef-search':str(config['ef_search']),'--k':str(config['k']),'--ib-port':'1','--rdma-timeout-ms':'10000'}
    files={'--queries':'query.fvecs','--results':'results.csv'}
    if case['mode']=='static':fixed['--max-queries']=str(config['query_count'])
    else:
        fixed.update({'--cache-mode':config['cache_mode'],'--verify':True})
        files.update({'--vectors':'vectors.fvecs','--workload':'workload.csv'})
    if case['mode']=='epoch':
        fixed.update({'--threads':str(case['threads']),'--warmup':str(config['warmup'])});files['--epochs']='epochs.csv'
    require(set(options)==set(fixed)|set(files)|{'--host','--port','--ib-device'},'missing/extra command option')
    for key,value in fixed.items():require(options[key]==value,'command/config mismatch: '+key)
    for key,value in files.items():
        root=paths['run'] if key in ['--results','--epochs'] else paths['bundle']
        require(recorded_path(options[key])==root/value,'command input/output mismatch: '+key)
    ipaddress.IPv4Address(options['--host'])
    require(1<=integer(options['--port'])<=65535 and options['--ib-device'],'invalid recorded endpoint')


def common(entry,case,config):
    for key,value in [('transport','rdma'),('rdma_connection','cm'),('policy',case['policy']),('status','ok')]:
        require(entry[key]==value,'wrong RDMA measurement field: '+key)
    for key,value in [('cache_capacity',config['cache_capacity']),('prefix_granularity',config['granularity']),
                      ('ef_search',config['ef_search']),('k',config['k'])]:
        require(integer(entry[key])==value,'wrong configuration: '+key)
    require(number(entry['elapsed_ms'])>0,'nonpositive elapsed time')
    transport={key:integer(entry[key]) for key in TRANSPORT}
    require(all(value>=0 for value in transport.values()),'negative transport counter')
    require(all(bool(transport[op])==bool(transport[byte]) for op,byte in
        [('transport_read_ops','transport_read_bytes'),('transport_write_ops','transport_write_bytes')]),'WR/byte presence mismatch')
    require(transport['transport_read_bytes']>=transport['transport_read_ops'] and
            transport['transport_write_bytes']>=transport['transport_write_ops'],'WR bytes smaller than op count')
    return transport


def query(entry,case,config):
    counts=validate_counts(entry,case['policy'])
    require(integer(entry['transport_write_ops'])==integer(entry['transport_write_bytes'])==0,'query performed a WRITE')
    # Smoke bundles use M=16; every neighbor READ fits one CM staging buffer.
    # A node READ contains the 24-byte x86_64 disk header plus dim float32s,
    # and CM splits each such READ into 1MiB work requests.
    node_reads=counts['read_ops']-counts['neighbor_reads'];node_bytes=24+config['dim']*4
    require(node_reads>0 and counts['neighbor_accesses']>0,'query missing node/neighbor READs')
    require(counts['requested_bytes']==counts['neighbor_requested_bytes']+node_reads*node_bytes,
            'logical node READ byte mismatch')
    require(integer(entry['transport_read_bytes'])==counts['requested_bytes'],'physical/logical READ byte mismatch')
    require(integer(entry['transport_read_ops'])==counts['neighbor_reads']+node_reads*((node_bytes+1048575)//1048576),
            'CM READ work-request count mismatch')
    ids=[integer(value) for value in entry['result_ids'].split(';') if value]
    require(len(ids)==min(config['k'],config['nodes']) and len(set(ids))==len(ids) and
            all(0<=value<config['nodes'] for value in ids),'invalid result IDs')
    return ids,counts


def update(entry):
    require(entry['result_ids']=='' and number(entry['verified_recall'])==-1,'update contains query results')
    require(all(integer(entry[key])==0 for key in COUNTERS),'update contains logical query counters')
    require(integer(entry['transport_write_ops'])>0 and integer(entry['transport_read_ops'])>0,
            'update missing mailbox WRITE or catalog READ')


def validate_case(log,epochs,operations,case,config,truth=None,min_recall=.95):
    require(case['policy'] in POLICIES and case['mode'] in ['static','dynamic','epoch'],'unknown smoke case')
    require(math.isfinite(min_recall) and 0<=min_recall<=1,'invalid recall threshold')
    totals=dict.fromkeys(COUNTERS+TRANSPORT,0);signature=[];recalls=[];latencies=[]
    generation=config['initial_generation'];update_count=0;query_ms=update_ms=reload_ms=0.0
    def accept_query(entry,seq,expected_generation,reference=None):
        require(integer(entry['snapshot_id'])==expected_generation,'query snapshot mismatch')
        ids,counts=query(entry,case,config)
        for key,value in counts.items():totals[key]+=value
        if reference is not None:
            require(len(reference)==config['k'] and len(set(map(int,reference)))==len(reference) and
                    all(0<=int(i)<config['nodes'] for i in reference),'invalid static truth')
            recall=len(set(ids)&set(map(int,reference)))/len(reference)
        else:recall=number(entry['verified_recall'])
        require(min_recall<=recall<=1,'missing or insufficient current-snapshot recall')
        recalls.append(recall);latencies.append(number(entry['elapsed_ms']));signature.append([seq,expected_generation,ids])
    for entry in log:
        for key,value in common(entry,case,config).items():totals[key]+=value
    if case['mode']=='static':
        require(not epochs and not operations,'static case has dynamic stages')
        require(len(log)==config['query_count'] and log,'incomplete static queries')
        require(truth is not None and truth.shape==(config['query_count'],config['k']),'static truth shape mismatch')
        require(np.issubdtype(truth.dtype,np.integer),'static truth IDs must have integer dtype')
        require([integer(e['query_id']) for e in log]==list(range(len(log))),'duplicate/missing/reordered query ID')
        for entry in log:
            require(entry['timing_mode']=='query_only','wrong static timing scope')
            seq=integer(entry['query_id']);accept_query(entry,seq,generation,truth[seq])
        query_ms=sum(latencies)
    else:
        require(len(log)==len(operations)==config['operation_count'] and log,'incomplete workload')
        require([integer(op['seq']) for op in operations]==list(range(len(operations))),'invalid workload sequences')
        require([integer(op['epoch']) for op in operations]==sorted(integer(op['epoch']) for op in operations),'reordered workload epochs')
        indexed={integer(entry['seq']):entry for entry in log}
        require(len(indexed)==len(log) and set(indexed)==set(range(len(log))),'duplicate/missing operation sequence')
        expected_epochs=defaultdict(list)
        scope='remote_submit_publish_catalog_then_reinject' if case['mode']=='dynamic' else 'remote_submit_publish_catalog'
        for operation in operations:
            seq=integer(operation['seq']);entry=indexed[seq]
            require(operation['type'] in ['query','upsert'] and entry['type']==operation['type'],'workload operation mismatch')
            for key in ['seq','vector_ref','node_id','epoch']:
                require(integer(entry[key])==integer(operation[key]),'workload/log identity mismatch: '+key)
            require(integer(entry['vector_ref'])<config['query_count'] and
                    0<=integer(entry['node_id'])<config['nodes'],'workload input outside bundle')
            require(integer(entry['node_count'])==config['nodes'],'unexpected node count')
            require(entry['cache_mode']==config['cache_mode'] and entry['update_timing_scope']==scope,'wrong update/cache scope')
            expected_epochs[integer(operation['epoch'])].append(entry)
        if case['mode']=='dynamic':
            require(not epochs,'serial case has epoch log')
            require([integer(e['seq']) for e in log]==list(range(len(log))),'serial operations reordered')
            for entry in log:
                require(entry['timing_mode']=='correctness_instrumented','serial --verify timing scope missing')
                require(integer(entry['snapshot_before'])==generation,'serial snapshot-before mismatch')
                refresh=number(entry['reload_ms']);require(refresh>=0,'negative reinjection time')
                if entry['type']=='query':
                    require(refresh==0,'query reinjection time');accept_query(entry,integer(entry['seq']),generation)
                    query_ms+=number(entry['elapsed_ms'])
                else:
                    update(entry);generation+=1;update_count+=1
                    require(integer(entry['snapshot_id'])==generation,'serial update publication mismatch')
                    update_ms+=number(entry['elapsed_ms']);reload_ms+=refresh
        else:
            require([integer(e['epoch']) for e in epochs]==sorted(expected_epochs),'missing/reordered epoch stages')
            expected_order=[]
            for stage in epochs:
                entries=expected_epochs[integer(stage['epoch'])]
                qs=[e for e in entries if e['type']=='query'];us=[e for e in entries if e['type']=='upsert']
                expected_order += [integer(e['seq']) for e in qs+us]
                require(integer(stage['snapshot_before'])==generation and
                        integer(stage['snapshot_after'])==generation+len(us),'epoch publication mismatch')
                require(integer(stage['query_count'])==len(qs) and integer(stage['update_count'])==len(us),'epoch operation counts')
                for key in ['query_phase_ms','update_phase_ms','reload_phase_ms','verification_ms','initial_warmup_ms']:
                    require(number(stage[key])>=0,'negative epoch stage time')
                require(not qs or number(stage['query_phase_ms'])>0,'missing query stage time')
                require(bool(us)==(number(stage['reload_phase_ms'])>0),'reload/update stage mismatch')
                require(stage is epochs[0] or number(stage['initial_warmup_ms'])==0,'repeated warmup time')
                require(math.isclose(number(stage['update_phase_ms']),sum(number(e['elapsed_ms']) for e in us),
                                     rel_tol=1e-6,abs_tol=1e-5),'update stage sum mismatch')
                require(stage['rewrite_measurement']=='not_collected' and integer(stage['logical_index_rewrite_bytes'])==0,'wrong remote rewrite scope')
                read_ops=integer(stage['reload_transport_read_ops']);read_bytes=integer(stage['reload_transport_read_bytes'])
                require((read_ops>=case['threads'] and read_bytes>=read_ops) if us else read_ops==read_bytes==0,'missing/unexpected reload catalog READs')
                for ordinal,entry in enumerate(qs):
                    require(integer(entry['thread_id'])==ordinal%case['threads'],'wrong query reader assignment')
                    accept_query(entry,integer(entry['seq']),generation)
                for offset,entry in enumerate(us,1):
                    update(entry);require(integer(entry['snapshot_id'])==generation+offset,'writer publication mismatch')
                    require(integer(entry['thread_id'])==0,'invalid writer log thread')
                for entry in entries:
                    require(entry['timing_mode']=='epoch_operation_only','wrong epoch timing scope')
                    require(integer(entry['threads'])==case['threads'] and integer(entry['warmup'])==config['warmup'],'wrong epoch configuration')
                    require(entry['rewrite_measurement']=='not_collected' and integer(entry['logical_index_rewrite_bytes'])==0,'invented server rewrite bytes')
                generation+=len(us);update_count+=len(us)
                query_ms+=number(stage['query_phase_ms']);update_ms+=number(stage['update_phase_ms']);reload_ms+=number(stage['reload_phase_ms'])
            require([integer(entry['seq']) for entry in log]==expected_order,'epoch CSV operation order mismatch')
    require(recalls,'no measured query')
    return dict(query_count=len(recalls),update_count=update_count,minimum_recall=min(recalls),mean_recall=float(np.mean(recalls)),
                mean_query_ms=float(np.mean(latencies)),query_phase_qps=len(recalls)/query_ms*1000,
                stage_ms=dict(query=query_ms,update=update_ms,reload=reload_ms),counters=totals,
                quality_sha256=hashlib.sha256(json.dumps(sorted(signature),separators=(',',':')).encode()).hexdigest(),
                server_rewrite_bytes=None,control_send_recv=None,hardware_connection_verified=False,
                scope='correctness smoke; serial truth runs add system work; epoch QPS includes worker launch/join; no performance acceptance')


def analyze(bundle,runs,allow_partial=False,allow_fixture=False,min_recall=.95):
    manifest=load_bundle(bundle);expected={c['case_id']:c for c in manifest['cases']}
    operations=rows(bundle/'workload.csv');truth=np.load(bundle/'ground_truth.npy',allow_pickle=False)
    results={};groups={};fixture=False
    for receipt in sorted(runs.glob('*/run.json')):
        record=json.loads(receipt.read_text(encoding='utf-8'));run=receipt.parent
        if record['role']!='compute':continue
        require(record['schema']=='rdma_smoke_run_v2' and record['status']=='complete' and record['returncode']==0,'failed/incomplete run: '+str(receipt))
        case=record['case'];name=case['case_id']
        require(name in expected and case==expected[name] and name not in results,'unknown/duplicate case identity')
        require(record['bundle_sha256']==sha256_file(bundle/'manifest.json') and
                record['index_master_sha256']==manifest['files']['master/index.bin'],'bundle/master provenance mismatch')
        origin=record['evidence_origin'];require(origin in ['client_process','test_fixture'],'unknown evidence origin')
        require(origin!='test_fixture' or allow_fixture,'fixture evidence requires --allow-fixture')
        fixture |= origin=='test_fixture'
        binary={'static':'lab_client','dynamic':'lab_dynamic_client','epoch':'lab_epoch_client'}[case['mode']]
        require(record['binary_sha256']==manifest['binaries'][binary]['sha256'],'binary provenance mismatch')
        validate_command(record['command'],binary,case,manifest['config'],record.get('execution_paths'))
        required=['results.csv','stdout.txt','stderr.txt']+(['epochs.csv'] if case['mode']=='epoch' else [])
        require(all(key in record['outputs'] for key in required),'missing output hashes')
        for key,digest in record['outputs'].items():require(sha256_file(confined(run,key))==digest,'run output changed: '+key)
        result=validate_case(rows(run/'results.csv'),rows(run/'epochs.csv') if case['mode']=='epoch' else [],
            operations if case['mode']!='static' else [],case,manifest['config'],truth if case['mode']=='static' else None,min_recall)
        # Serial and epoch schedules observe different snapshots; compare each separately.
        group=case['mode'];signature=result['quality_sha256']
        require(group not in groups or groups[group]==signature,'policy/thread result disagreement: '+group)
        groups[group]=signature;results[name]=result
    require(results,'no complete compute runs')
    complete=set(results)==set(expected)
    require(allow_partial or complete,'smoke cases incomplete')
    return dict(schema='rdma_smoke_validation_v1',measurement='test_fixture_validation' if fixture else 'recorded_rdma_log_validation',
                complete=complete,validated_cases=len(results),expected_cases=len(expected),cases=results,
                comparison_complete=complete,validated_comparison_groups=len(groups),
                hardware_connection_verified=False,hardware_performance_acceptance=False,
                storage_input_verified=False,storage_input_note='per-role storage receipt and both-host connectivity require hardware acceptance',
                fixture_evidence=fixture,minimum_required_recall=min_recall)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['bundle','runs','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--allow-partial',action='store_true');parser.add_argument('--allow-fixture',action='store_true')
    parser.add_argument('--min-recall',type=float,default=.95);args=parser.parse_args()
    manifest=load_bundle(args.bundle)
    inputs=[p for root in [args.bundle,args.runs] for p in root.rglob('*') if p.is_file()]
    inputs += [Path(p) for p in manifest['source_inputs']]+[Path(v['path']) for v in manifest['binaries'].values()]
    check_outputs(inputs,[args.output])
    require(not args.output.exists(),'use a new validation output path')
    report=analyze(args.bundle,args.runs,args.allow_partial,args.allow_fixture,args.min_recall)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('validated_cases='+str(report['validated_cases'])+'/'+str(report['expected_cases'])+'; hardware_performance_acceptance=false')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,IndexError) as error:
        print('analyze_rdma: '+str(error),file=sys.stderr);sys.exit(1)
