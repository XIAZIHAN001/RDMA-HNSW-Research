"""Collect four-policy SIFT10K traces and prefix G sensitivity; run in Linux/WSL."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare_data import check_outputs, sha256_file

CONFIGS=[('fixed',8),('header_actual',8),('cached_length',8)]+[('prefix',g) for g in [1,2,4,8,16]]
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir',type=Path,required=True)
    parser.add_argument('--dynamic-source',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    index=repo/'data/sift10000/index.bin';queries=repo/'data/sift10000/query.fvecs'
    inputs=[index,queries]+[args.dynamic_source/f'{d}_workload.csv' for d in ['random','hotspot']]
    outputs=[args.outdir/'commands.json',args.outdir/'metadata.json']
    outputs += [args.outdir/f'static_{policy}_g{g}{suffix}.csv' for policy,g in CONFIGS for suffix in ['', '_trace']]
    outputs += [args.outdir/f'{d}_prefix_g{g}_{mode}{suffix}.csv' for d in ['random','hotspot'] for g in [1,2,4,8,16] for mode in ['warm','cold'] for suffix in ['', '_trace']]
    check_outputs(inputs,outputs);args.outdir.mkdir(parents=True,exist_ok=True)
    commands=[]
    def run(command):
        commands.append(list(map(str,command)))
        result=subprocess.run(commands[-1],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)
        print(result.stdout.strip(),flush=True)
    with tempfile.TemporaryDirectory(prefix='hnsw_prefix_pilot_') as directory:
        path=Path(directory)/'index.bin'
        for policy,g in CONFIGS:
            shutil.copyfile(index,path)
            name=f'static_{policy}_g{g}'
            run([repo/'experiments/build/lab_client','--index',path,'--queries',queries,
                 '--results',args.outdir/f'{name}.csv','--trace',args.outdir/f'{name}_trace.csv',
                 '--read-policy',policy,'--prefix-granularity',g,'--cache-capacity',10000,'--ef-search',256,'--k',10])
        for distribution in ['random','hotspot']:
            for g in [1,2,4,8,16]:
                for mode in ['warm','cold']:
                    shutil.copyfile(index,path)
                    name=f'{distribution}_prefix_g{g}_{mode}'
                    run([repo/'experiments/build/lab_dynamic_client','--index',path,'--queries',queries,'--vectors',queries,
                         '--workload',args.dynamic_source/f'{distribution}_workload.csv',
                         '--results',args.outdir/f'{name}.csv','--trace',args.outdir/f'{name}_trace.csv',
                         '--read-policy','prefix','--prefix-granularity',g,'--cache-mode',mode,'--cache-capacity',10000,
                         '--ef-search',256,'--k',10,'--verify'])
    (args.outdir/'commands.json').write_text(json.dumps(commands,indent=2)+'\n',encoding='utf-8')
    metadata=dict(measurement='local_mmap_trace_and_correctness_pilot',nodes=10000,query_count_static=100,
        granularity=[1,2,4,8,16],cache_capacity=10000,ef_search=256,k=10,model='adjacency serial assumption L=3us B=100Gbit/s',
        static_timing_mode='trace_instrumented',dynamic_timing_mode='correctness_instrumented',
        index_sha256=sha256_file(index),queries_sha256=sha256_file(queries),
        lab_client_sha256=sha256_file(repo/'experiments/build/lab_client'),
        dynamic_client_sha256=sha256_file(repo/'experiments/build/lab_dynamic_client'),
        dynamic_workload_sha256={d:sha256_file(args.dynamic_source/f'{d}_workload.csv') for d in ['random','hotspot']})
    (args.outdir/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
