"""Run a reproducible, serial mmap cache pilot on disposable Linux index copies."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_data import check_outputs, read_fvecs, sha256_file

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--queries',type=Path,required=True)
    parser.add_argument('--outdir',type=Path,required=True)
    parser.add_argument('--operations',type=int,default=40)
    parser.add_argument('--update-ratio',type=float,default=.3)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    binary=repo/'experiments/build/lab_dynamic_client'
    generator=repo/'experiments/build/lab_workload'
    queries=read_fvecs(args.queries)
    import struct
    with args.index.open('rb') as source:
        nodes=struct.unpack_from('<Q',source.read(64),32)[0]
    outputs=[args.outdir/'commands.json',args.outdir/'metadata.json']
    for distribution in ['random','hotspot']:
        outputs.append(args.outdir/f'{distribution}_workload.csv')
        for policy,mode in [('fixed','warm'),('header_actual','warm'),('cached_length','warm'),('cached_length','cold')]:
            outputs.extend(args.outdir/f'{distribution}_{policy}_{mode}{suffix}.csv' for suffix in ['', '_trace'])
    check_outputs([args.index,args.queries],outputs)
    args.outdir.mkdir(parents=True,exist_ok=True)
    commands=[]
    def run(command):
        commands.append(list(map(str,command)))
        result=subprocess.run(commands[-1],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)
        print(result.stdout.strip(),flush=True)
    with tempfile.TemporaryDirectory(prefix='hnsw_cache_pilot_') as directory:
        for distribution in ['random','hotspot']:
            workload=args.outdir/f'{distribution}_workload.csv'
            run([generator,'--out',workload,'--operations',args.operations,'--update-ratio',args.update_ratio,
                 '--nodes',nodes,'--queries',len(queries),'--vectors',len(queries),'--seed',42,
                 '--distribution',distribution,'--epoch-size',10])
            for policy,mode in [('fixed','warm'),('header_actual','warm'),('cached_length','warm'),('cached_length','cold')]:
                name=f'{distribution}_{policy}_{mode}'
                index=Path(directory)/f'{name}.bin'
                shutil.copyfile(args.index,index)
                run([binary,'--index',index,'--queries',args.queries,'--vectors',args.queries,
                     '--workload',workload,'--results',args.outdir/f'{name}.csv',
                     '--trace',args.outdir/f'{name}_trace.csv','--read-policy',policy,
                     '--cache-mode',mode,'--cache-capacity',10000,'--ef-search',256,'--k',10,'--verify'])
    (args.outdir/'commands.json').write_text(json.dumps(commands,indent=2)+'\n',encoding='utf-8')
    metadata=dict(measurement='local_mmap_correctness_pilot',semantics='serial update/reload/query',
                  operations=args.operations,target_update_ratio=args.update_ratio,seed=42,
                  initial_index_sha256=sha256_file(args.index),queries_sha256=sha256_file(args.queries),
                  dynamic_client_sha256=sha256_file(binary),workload_generator_sha256=sha256_file(generator),
                  timing_mode='correctness_instrumented',index_filesystem='Linux temporary copy',
                  truth='exact float64 L2 over active nodes in the current snapshot; outside timing')
    (args.outdir/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
