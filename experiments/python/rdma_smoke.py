"""Prepare an offline correctness bundle; run one explicit storage/compute case later."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
from prepare_data import check_outputs, exact_topk, read_fvecs, sha256_file, write_fvecs

POLICIES=['fixed','header_actual','cached_length','prefix']
EXECUTABLES=['lab_client','lab_dynamic_client','lab_epoch_client','lab_workload',
             'upstream/hnsw_build','upstream/storage_server']


def save(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def confined(bundle,name):
    path=(bundle/name).resolve()
    if not path.is_relative_to(bundle.resolve()):raise ValueError('bundle path escapes its root: '+name)
    return path


def load_bundle(bundle):
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema']!='rdma_smoke_bundle_v1':raise ValueError('unsupported bundle schema')
    required={'base.fvecs','query.fvecs','vectors.fvecs','ground_truth.npy','workload.csv','master/index.bin'}
    if not required<=set(manifest['files']):raise ValueError('bundle data, truth and workload must all be frozen')
    for name,digest in manifest['files'].items():
        if sha256_file(confined(bundle,name))!=digest:raise ValueError('frozen bundle input changed: '+name)
    cases=manifest['cases']
    expected={mode+'_'+policy for mode in ['static','dynamic','epoch1','epoch4'] for policy in POLICIES}
    if len(cases)!=16 or {c['case_id'] for c in cases}!=expected:raise ValueError('invalid smoke cases')
    for case in cases:
        if (case['policy'] not in POLICIES or case['mode'] not in ['static','dynamic','epoch'] or
            case['threads']!=(4 if case['case_id'].startswith('epoch4_') else 1)):
            raise ValueError('invalid smoke case configuration')
        prefix='epoch'+str(case['threads']) if case['mode']=='epoch' else case['mode']
        if case['case_id']!=prefix+'_'+case['policy']:raise ValueError('case identity mismatch')
    return manifest


def prepare(args):
    if args.outdir.exists():raise ValueError('use a new bundle directory; never replace inputs')
    if not 1<=args.k<=args.nodes or args.ef_search<args.k or args.operations<4:raise ValueError('invalid smoke dimensions/counts')
    build=args.build_dir.resolve()
    binaries={name:dict(path=str(build/name),sha256=sha256_file(build/name)) for name in EXECUTABLES}
    base=read_fvecs(args.base,args.nodes);queries=read_fvecs(args.queries,args.query_count)
    if base.shape[1]!=queries.shape[1]:raise ValueError('base/query dimensions differ')
    truth=exact_topk(base,queries,args.k)
    args.outdir.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rdma_smoke_prepare_') as directory:
        stage=Path(directory);(stage/'master').mkdir()
        write_fvecs(stage/'base.fvecs',base);write_fvecs(stage/'query.fvecs',queries)
        write_fvecs(stage/'vectors.fvecs',queries);np.save(stage/'ground_truth.npy',truth,allow_pickle=False)
        commands=[
            [str(build/'upstream/hnsw_build'),'--input',str(stage/'base.fvecs'),'--dim',str(base.shape[1]),
             '--M','16','--ef-construction','100','--seed','42','--output',str(stage/'master/index.bin')],
            [str(build/'lab_workload'),'--out',str(stage/'workload.csv'),'--operations',str(args.operations),
             '--update-ratio','0.3','--nodes',str(args.nodes),'--queries',str(args.query_count),
             '--vectors',str(args.query_count),'--seed','42','--epoch-size','10','--distribution','random']]
        for ordinal,command in enumerate(commands):
            result=subprocess.run(command,capture_output=True,text=True,timeout=120)
            (stage/f'prepare_{ordinal}.stdout.txt').write_text(result.stdout,encoding='utf-8')
            (stage/f'prepare_{ordinal}.stderr.txt').write_text(result.stderr,encoding='utf-8')
            if result.returncode:raise RuntimeError(result.stderr)
        cases=[dict(case_id=mode+'_'+policy,mode='epoch' if mode.startswith('epoch') else mode,
                    policy=policy,threads=4 if mode=='epoch4' else 1)
               for mode in ['static','dynamic','epoch1','epoch4'] for policy in POLICIES]
        repo=Path(__file__).resolve().parents[2]
        sources=[repo/'CMakeLists.txt',repo/'experiments/CMakeLists.txt']
        for name in ['src','include','experiments/src','experiments/python']:
            sources += [p for p in (repo/name).rglob('*') if p.suffix in ['.cc','.h','.py']]
        inputs={str(p.resolve()):sha256_file(p) for p in [args.base,args.queries]+sources}
        revision=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
        dirty=subprocess.run(['git','status','--porcelain'],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
        manifest=dict(schema='rdma_smoke_bundle_v1',measurement='offline_preparation_only',
            prepared_utc=datetime.now(timezone.utc).isoformat(),hardware_connection_verified=False,
            config=dict(nodes=args.nodes,dim=base.shape[1],query_count=args.query_count,operation_count=args.operations,
                        k=args.k,ef_search=args.ef_search,granularity=8,cache_capacity=10000,warmup=0,
                        cache_mode='warm',initial_generation=1),cases=cases,binaries=binaries,source_inputs=inputs,
            files={p.relative_to(stage).as_posix():sha256_file(p) for p in stage.rglob('*') if p.is_file()},
            preparation_commands=commands,
            source_commit=revision,source_dirty=bool(dirty),
            semantics='per-case fresh storage copy; exclusive writer; epoch joins queries before ordered updates/reload')
        save(stage/'manifest.json',manifest)
        # The destination must not exist, even when a preparation command fails.
        shutil.copytree(stage,args.outdir)
    print('prepared_cases=16; hardware_connection_verified=false')


def case_command(bundle,manifest,case,role,run,host,device,port,build_dir=None):
    build=Path(build_dir).resolve() if build_dir else Path(manifest['binaries']['lab_client']['path']).parent
    connection=['--transport','rdma','--rdma-connection','cm','--port',str(port),
                '--ib-device',device,'--ib-port','1','--rdma-timeout-ms','10000']
    if role=='storage':
        return [str((build/'upstream/storage_server').resolve()),'--index',str(run/'index.bin'),'--bind',host,*connection]
    name={'static':'lab_client','dynamic':'lab_dynamic_client','epoch':'lab_epoch_client'}[case['mode']]
    c=manifest['config']
    command=[str((build/name).resolve()),'--host',host,*connection,'--queries',str(bundle/'query.fvecs'),
             '--read-policy',case['policy'],'--prefix-granularity',str(c['granularity']),
             '--cache-capacity',str(c['cache_capacity']),'--ef-search',str(c['ef_search']),
             '--k',str(c['k']),'--results',str(run/'results.csv')]
    if case['mode']=='static':command+=['--max-queries',str(c['query_count'])]
    else:
        command+=['--vectors',str(bundle/'vectors.fvecs'),'--workload',str(bundle/'workload.csv'),
                  '--cache-mode',c['cache_mode'],'--verify']
    if case['mode']=='epoch':
        command+=['--threads',str(case['threads']),'--warmup',str(c['warmup']),'--epochs',str(run/'epochs.csv')]
    return command


def reset_storage(bundle,run):
    if run.exists():raise ValueError('use a new run directory; reset only creates a fresh copy')
    run.mkdir(parents=True)
    shutil.copyfile(bundle/'master/index.bin',run/'index.bin')


def run_case(args):
    if not args.host or not args.device:raise ValueError('explicit --host and --device required before running')
    ipaddress.IPv4Address(args.host)
    if not 1<=args.port<=65535 or args.timeout<=0:raise ValueError('invalid port/timeout')
    bundle=args.bundle.resolve();manifest=load_bundle(bundle)
    matches=[case for case in manifest['cases'] if case['case_id']==args.case]
    if not matches:raise ValueError('unknown case')
    case=matches[0];run=args.outdir.resolve()
    if run.exists():raise ValueError('use a new per-role run directory')
    if run.is_relative_to(bundle):raise ValueError('run directory must be outside the frozen bundle')
    command=case_command(bundle,manifest,case,args.role,run,args.host,args.device,args.port,args.build_dir)
    name={'static':'lab_client','dynamic':'lab_dynamic_client','epoch':'lab_epoch_client'}[case['mode']] if args.role=='compute' else 'upstream/storage_server'
    if sha256_file(Path(command[0]))!=manifest['binaries'][name]['sha256']:
        raise ValueError('binary differs from prepared bundle; rebuild/copy consistently and prepare a new bundle')
    # Before allocating outputs, protect executables and all bundle/source inputs.
    inputs=[Path(command[0])]+list(bundle.rglob('*'))+[Path(p) for p in manifest['source_inputs']]
    check_outputs(inputs,[run/path for path in ['index.bin','results.csv','epochs.csv','run.json','stdout.txt','stderr.txt']])
    if args.role=='storage':reset_storage(bundle,run)
    else:run.mkdir(parents=True)
    record=dict(schema='rdma_smoke_run_v2',case=case,role=args.role,command=command,
                execution_paths=dict(bundle=bundle.as_posix(),run=run.as_posix(),executable=Path(command[0]).resolve().as_posix()),
                bundle_sha256=sha256_file(bundle/'manifest.json'),binary_sha256=sha256_file(Path(command[0])),
                index_master_sha256=manifest['files']['master/index.bin'],status='running',returncode=None,
                evidence_origin='client_process' if args.role=='compute' else 'storage_process',
                hardware_connection_verified=False,started_utc=datetime.now(timezone.utc).isoformat())
    save(run/'run.json',record);start=time.perf_counter()
    try:
        with (run/'stdout.txt').open('w') as stdout,(run/'stderr.txt').open('w') as stderr:
            result=subprocess.run(command,stdout=stdout,stderr=stderr,timeout=args.timeout if args.role=='compute' else None)
        code=result.returncode
    except subprocess.TimeoutExpired:code=124
    except KeyboardInterrupt:code=130
    except OSError as error:
        code=127;(run/'stderr.txt').write_text(str(error),encoding='utf-8')
    record.update(returncode=code,status=('complete' if code==0 else 'failed') if args.role=='compute' else 'stopped',
                  process_wall_seconds=time.perf_counter()-start,
                  outputs={p.name:sha256_file(p) for p in run.iterdir() if p.is_file() and p.name not in ['run.json','index.bin']})
    save(run/'run.json',record)
    print('case='+args.case+'; status='+record['status']+'; returncode='+str(code))
    return 0 if code==0 else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='action',required=True)
    p=subs.add_parser('prepare')
    for name in ['base','queries','build-dir','outdir']:p.add_argument('--'+name,type=Path,required=True)
    for name,default in [('nodes',1000),('query-count',100),('operations',40),('k',10),('ef-search',256)]:p.add_argument('--'+name,type=int,default=default)
    p=subs.add_parser('plan');p.add_argument('--bundle',type=Path,required=True);p.add_argument('--build-dir',type=Path)
    p=subs.add_parser('run')
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--role',choices=['storage','compute'],required=True)
    p.add_argument('--case',required=True);p.add_argument('--outdir',type=Path,required=True);p.add_argument('--build-dir',type=Path)
    p.add_argument('--host');p.add_argument('--device');p.add_argument('--port',type=int,default=18515);p.add_argument('--timeout',type=int,default=120)
    args=parser.parse_args()
    if args.action=='prepare':prepare(args);return 0
    if args.action=='run':return run_case(args)
    bundle=args.bundle.resolve();manifest=load_bundle(bundle)
    plan=[dict(case=case,storage=case_command(bundle,manifest,case,'storage',Path('storage-runs')/case['case_id'],
                '$STORAGE_RDMA_IP','$STORAGE_DEVICE',18515,args.build_dir),
               compute=case_command(bundle,manifest,case,'compute',Path('compute-runs')/case['case_id'],
                '$STORAGE_RDMA_IP','$COMPUTE_DEVICE',18515,args.build_dir)) for case in manifest['cases']]
    print(json.dumps(dict(measurement='commands_only_no_execution',hardware_connection_verified=False,cases=plan),indent=2))
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,RuntimeError,KeyError) as error:
        print('rdma_smoke: '+str(error),file=sys.stderr);sys.exit(1)
