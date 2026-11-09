"""Verify all prefix pilot traces against historical replay and policy-equivalent results."""
import argparse
from collections import Counter
import json
from pathlib import Path
import numpy as np
import analyze
from analyze_cache_pilot import rows, QUERY_FIELDS, FIELDS
from prepare_data import check_outputs, sha256_file
from simulate_reads import replay
from run_prefix_pilot import CONFIGS

def verify_counts(trace,log,policy,g,mode):
    predicted=replay(trace,policy,3,100,10000,mode,g)
    aggregate={int(q.get('seq',q.get('query_id'))):Counter() for q in log}
    for actual,model in zip(trace,predicted):
        query_id=int(actual['query_id'])
        if query_id not in aggregate:raise ValueError('unknown traced query')
        for field in FIELDS:
            if int(actual[field])!=model[field]:raise ValueError(f'model mismatch {field}: G={g} mode={mode}')
            aggregate[query_id][field]+=int(actual[field])
        aggregate[query_id]['accesses']+=1
    for query in log:
        counts=aggregate[int(query.get('seq',query.get('query_id')))]
        if counts['accesses']!=int(query['neighbor_accesses']):raise ValueError('neighbor access mismatch')
        for field in FIELDS:
            query_field=QUERY_FIELDS.get(field,field)
            if query_field in query and counts[field]!=int(query[query_field]):raise ValueError('query counter mismatch '+field)
    return {field:sum(row[field] for row in predicted) for field in FIELDS},sum(row['estimated_latency_us'] for row in predicted)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--dynamic-source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    index=repo/'data/sift10000/index.bin';truth_path=repo/'data/sift10000/ground_truth.npy'
    inputs=list(args.source.glob('*.csv'))+list(args.source.glob('*.json'))+list(args.dynamic_source.glob('*.csv'))+[index,truth_path]
    check_outputs(inputs,[args.output])
    catalog,graph=analyze.graph_degrees(index);truth=np.load(truth_path,allow_pickle=False)
    static={};reference=None;comparisons=0
    for policy,g in CONFIGS:
        name=f'static_{policy}_g{g}'
        queries=rows(args.source/f'{name}.csv');trace=rows(args.source/f'{name}_trace.csv')
        analyze.validate_queries(queries,catalog,truth);analyze.check_trace(trace,catalog,graph)
        analyze.validate_trace_counts(trace,queries)
        recall=analyze.recall_at_k(queries,truth)
        signature=[q['result_ids'] for q in queries]
        if reference is None:reference=signature
        elif signature!=reference:raise ValueError('static policy results differ')
        counters,model_us=verify_counts(trace,queries,policy,g,'warm');comparisons+=len(trace)
        static[f'{policy}_g{g}']=dict(recall_at_k=recall,query_count=len(queries),counters=counters,
            assumed_adjacency_us_per_query=model_us/len(queries),trace_sha256=sha256_file(args.source/f'{name}_trace.csv'))
    dynamic={}
    for distribution in ['random','hotspot']:
        reference=[(q['seq'],q['snapshot_id'],q['result_ids'],q['verified_recall']) for q in
                   rows(args.dynamic_source/f'{distribution}_fixed_warm.csv') if q['type']=='query']
        dynamic[distribution]={}
        for g in [1,2,4,8,16]:
            for mode in ['warm','cold']:
                name=f'{distribution}_prefix_g{g}_{mode}'
                log=rows(args.source/f'{name}.csv');queries=[q for q in log if q['type']=='query'];trace=rows(args.source/f'{name}_trace.csv')
                signature=[(q['seq'],q['snapshot_id'],q['result_ids'],q['verified_recall']) for q in queries]
                if signature!=reference:raise ValueError('dynamic prefix/fixed results differ')
                if any(q['status']!='ok' for q in log):raise ValueError('failed dynamic operation')
                counters,model_us=verify_counts(trace,queries,'prefix',g,mode);comparisons+=len(trace)
                dynamic[distribution][f'g{g}_{mode}']=dict(query_count=len(queries),update_count=len(log)-len(queries),
                    mean_verified_recall=sum(float(q['verified_recall']) for q in queries)/len(queries),
                    counters=counters,assumed_adjacency_us_per_query=model_us/len(queries),
                    trace_sha256=sha256_file(args.source/f'{name}_trace.csv'))
    evidence=dict(measurement='local_mmap_trace_and_correctness_pilot',static=static,dynamic=dynamic,
        metadata=json.loads((args.source/'metadata.json').read_text(encoding='utf-8')),
        actual_model_access_pairs_verified=comparisons,all_policies_have_identical_results=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(evidence,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('verified_access_pairs='+str(comparisons))
if __name__=='__main__':main()
