"""Run the frozen local epoch matrix, preserving provenance and resumable raw logs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import time
from prepare_data import check_outputs,sha256_file

def save(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(path)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix',type=Path,required=True);parser.add_argument('--outdir',type=Path,required=True)
    parser.add_argument('--limit',type=int,help='Run only the first N pending tasks; validation/budget pilot')
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2];matrix=json.loads(args.matrix.read_text(encoding='utf-8'))
    binary=repo/'experiments/build/lab_epoch_client';generator=repo/'experiments/build/lab_workload'
    cache=(repo/'experiments/build/CMakeCache.txt').read_text()
    if 'CMAKE_BUILD_TYPE:STRING=Release' not in cache:
        raise ValueError('formal matrix requires a consistent Release build')
    flags={name:(repo/'experiments/build'/name).read_text() for name in [
        'CMakeFiles/lab_epoch_client.dir/flags.make','CMakeFiles/lab_strategies.dir/flags.make',
        'upstream/CMakeFiles/rdma_hnsw_core.dir/flags.make']}
    if any('-O3' not in value or '-DNDEBUG' not in value for value in flags.values()):
        raise ValueError('formal matrix compiler flags are inconsistent')
    datasets={f"{c['dataset']}{c['nodes']}":repo/f"data/main_{c['dataset']}{c['nodes']}" for c in matrix['configs']}
    sources=[repo/'CMakeLists.txt',repo/'experiments/CMakeLists.txt']
    for root in ['src','include','experiments/src','experiments/python']:
        sources += [p for p in (repo/root).rglob('*') if p.suffix in ['.cc','.h','.py']]
    inputs=[args.matrix,binary,generator]+sources+[path for folder in datasets.values() for path in [folder/'index.bin',folder/'query.fvecs',folder/'ground_truth.npy',folder/'manifest.json']]
    identity={str(path.resolve()):sha256_file(path) for path in inputs}
    status_path=args.outdir/'status.json';metadata_path=args.outdir/'metadata.json'
    tasks=[]
    for phase in ['main','cold_static_control','rest']:
        selected=[c for c in matrix['configs'] if (('main' in c['groups']) if phase=='main' else
                  ('cold_static_control' in c['groups']) if phase=='cold_static_control' else
                  ('main' not in c['groups'] and 'cold_static_control' not in c['groups']))]
        phase_tasks=[(c,repeat) for c in selected for repeat in range(matrix['repeats'])]
        random.Random(matrix['run_order_seed']+len(tasks)).shuffle(phase_tasks);tasks.extend(phase_tasks)
    outputs=[status_path,metadata_path,status_path.with_suffix('.json.tmp'),metadata_path.with_suffix('.json.tmp')]
    for c,repeat in tasks:
        name=f"{c['config_id']}_r{repeat}"
        outputs += [args.outdir/f'{name}{suffix}' for suffix in ['.csv','_epochs.csv','_stdout.txt','_stderr.txt']]
        outputs.append(args.outdir/f"workload_{c['dataset']}_{c['nodes']}_{c['update_ratio']}_{c['distribution']}_{c['operation_count']}.csv")
    check_outputs(inputs,outputs)
    args.outdir.mkdir(parents=True,exist_ok=True)
    if metadata_path.exists():
        metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
        if metadata['input_hashes']!=identity:raise ValueError('frozen inputs/binaries changed; use a new output directory')
    else:
        revision=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
        dirty=subprocess.run(['git','status','--porcelain'],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
        metadata=dict(measurement='local_mmap_epoch',started_utc=datetime.now(timezone.utc).isoformat(),source_commit=revision,source_dirty=bool(dirty),
                      input_hashes=identity,matrix=matrix,matrix_file=str(args.matrix.resolve()),compiler_flags=flags,index_filesystem='disposable Linux copies',
                      semantics='query block joins before ordered updates; readers reload once per update batch',
                      throughput_scope='query phase includes worker launch/join; accounted mixed time sums query/update/reload and excludes initialization/logging/truth')
        save(metadata_path,metadata)
    status=json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else dict(runs={})
    executed=0
    with tempfile.TemporaryDirectory(prefix='hnsw_main_') as directory:
        for ordinal,(c,repeat) in enumerate(tasks):
            name=f"{c['config_id']}_r{repeat}"
            if name in status['runs'] and status['runs'][name]['status']=='complete':
                previous=status['runs'][name]
                if sha256_file(args.outdir/f'{name}.csv')!=previous['result_sha256'] or sha256_file(args.outdir/f'{name}_epochs.csv')!=previous['epochs_sha256']:
                    raise ValueError('completed evidence changed: '+name)
                continue
            if args.limit is not None and executed>=args.limit:break
            folder=datasets[f"{c['dataset']}{c['nodes']}"]
            workload=args.outdir/f"workload_{c['dataset']}_{c['nodes']}_{c['update_ratio']}_{c['distribution']}_{c['operation_count']}.csv"
            if not workload.exists():
                subprocess.run(list(map(str,[generator,'--out',workload,'--operations',c['operation_count'],
                    '--update-ratio',c['update_ratio'],'--nodes',c['nodes'],'--queries',1000,'--vectors',1000,
                    '--seed',matrix['workload_seed'],'--epoch-size',c['epoch_size'],'--distribution',c['distribution']])),check=True,capture_output=True)
            index=Path(directory)/'index.bin';shutil.copyfile(folder/'index.bin',index)
            command=list(map(str,[binary,'--index',index,'--queries',folder/'query.fvecs','--vectors',folder/'query.fvecs',
                '--workload',workload,'--results',args.outdir/f'{name}.csv','--epochs',args.outdir/f'{name}_epochs.csv',
                '--read-policy',c['policy'],'--prefix-granularity',c['granularity'],'--cache-capacity',c['cache_capacity'],
                '--threads',c['threads'],'--warmup',c['warmup'],'--cache-mode','warm',
                '--verify-samples',0 if c['update_ratio']==0 else 1,'--ef-search',matrix['ef_search'],'--k',matrix['k']]))
            print(f"START {ordinal+1}/{len(tasks)} {name}: {c['dataset']}{c['nodes']} {c['policy']} G={c['granularity']} U={c['update_ratio']} T={c['threads']}",flush=True)
            start=time.perf_counter();result=subprocess.run(command,capture_output=True,text=True);seconds=time.perf_counter()-start
            (args.outdir/f'{name}_stdout.txt').write_text(result.stdout,encoding='utf-8')
            (args.outdir/f'{name}_stderr.txt').write_text(result.stderr,encoding='utf-8')
            record=dict(status='complete' if result.returncode==0 else 'failed',config=c,repeat=repeat,command=command,
                        returncode=result.returncode,process_wall_seconds=seconds,workload_sha256=sha256_file(workload),
                        result_sha256=sha256_file(args.outdir/f'{name}.csv') if (args.outdir/f'{name}.csv').exists() else None,
                        epochs_sha256=sha256_file(args.outdir/f'{name}_epochs.csv') if (args.outdir/f'{name}_epochs.csv').exists() else None)
            status['runs'][name]=record;save(status_path,status);executed+=1
            print(f"DONE {name} status={record['status']} seconds={seconds:.2f} {result.stdout.strip()}",flush=True)
            if result.returncode:raise RuntimeError(result.stderr)
    completed=sum(run['status']=='complete' for run in status['runs'].values())
    print(f'complete_runs={completed}/{len(tasks)}',flush=True)
if __name__=='__main__':main()
