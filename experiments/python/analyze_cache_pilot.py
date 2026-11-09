"""Verify serial cache-pilot operations, policy equivalence and C++/replay counters."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from prepare_data import check_outputs, sha256_file
from simulate_reads import replay, METRICS

FIELDS=[f for f in METRICS if f!='estimated_latency_us']
QUERY_FIELDS={'read_ops':'neighbor_reads','requested_bytes':'neighbor_requested_bytes',
              'useful_bytes':'neighbor_useful_bytes','wasted_bytes':'neighbor_wasted_bytes',
              'dependent_reads':'neighbor_dependent_reads','header_reads':'neighbor_header_reads'}
def rows(path):
    with path.open(newline='',encoding='utf-8') as source:return list(csv.DictReader(source))
def analyze(source):
    results={}
    comparisons=0
    for distribution in ['random','hotspot']:
        operations=rows(source/f'{distribution}_workload.csv')
        reference=None
        distribution_results={}
        for policy,mode in [('fixed','warm'),('header_actual','warm'),('cached_length','warm'),('cached_length','cold')]:
            name=f'{distribution}_{policy}_{mode}'
            log=rows(source/f'{name}.csv');trace=rows(source/f'{name}_trace.csv')
            if len(log)!=len(operations):raise ValueError('incomplete operations')
            snapshot=1
            queries=[]
            for operation,entry in zip(operations,log):
                for field in ['seq','type','vector_ref','node_id','epoch']:
                    if operation[field]!=entry[field]:raise ValueError('workload/log mismatch: '+field)
                if entry['status']!='ok':raise ValueError('operation failed')
                if int(entry['snapshot_before'])!=snapshot:raise ValueError('snapshot_before mismatch')
                if entry['type']!='query':snapshot+=1
                if int(entry['snapshot_id'])!=snapshot:raise ValueError('published snapshot mismatch')
                if entry['type']=='query':queries.append(entry)
            signature=[(entry['seq'],entry['snapshot_id'],entry['result_ids'],entry['verified_recall']) for entry in queries]
            if reference is None:reference=signature
            elif signature!=reference:raise ValueError('policies returned different results or recall')
            predicted=replay(trace,policy,3,100,10000,mode)
            aggregate={int(entry['seq']):Counter() for entry in queries}
            for actual,model in zip(trace,predicted):
                if int(actual['query_id']) not in aggregate:raise ValueError('trace unknown operation')
                for field in FIELDS:
                    if int(actual[field])!=model[field]:raise ValueError(f'{name}: model mismatch {field}')
                    aggregate[int(actual['query_id'])][field]+=int(actual[field])
                aggregate[int(actual['query_id'])]['accesses']+=1
                comparisons+=1
            for query in queries:
                counts=aggregate[int(query['seq'])]
                if counts['accesses']!=int(query['neighbor_accesses']):raise ValueError('access count mismatch')
                for field in FIELDS:
                    if field=='prefix_reads':continue
                    if counts[field]!=int(query[QUERY_FIELDS.get(field,field)]):raise ValueError('query/trace mismatch '+field)
            totals={field:sum(int(entry[QUERY_FIELDS.get(field,field)]) for entry in queries) for field in FIELDS if field!='prefix_reads'}
            updates=[entry for entry in log if entry['type']!='query']
            distribution_results[policy+'_'+mode]=dict(query_count=len(queries),update_count=len(updates),
                actual_update_ratio=len(updates)/len(log),mean_verified_recall=sum(float(q['verified_recall']) for q in queries)/len(queries),
                minimum_verified_recall=min(float(q['verified_recall']) for q in queries),
                natural_degree_changes=sum(int(entry['degree_changes']) for entry in updates),
                natural_neighbor_id_changes=sum(int(entry['neighbor_id_changes']) for entry in updates),
                counters=totals,mean_query_ms=sum(float(q['elapsed_ms']) for q in queries)/len(queries),
                mean_update_ms=sum(float(e['elapsed_ms']) for e in updates)/len(updates),
                mean_reload_ms=sum(float(e['reload_ms']) for e in updates)/len(updates),
                results_sha256=sha256_file(source/f'{name}.csv'),trace_sha256=sha256_file(source/f'{name}_trace.csv'))
        results[distribution]=distribution_results
    return dict(measurement='local_mmap_correctness_pilot',scopes='Serial dynamic correctness; not a performance comparison',
                metadata=json.loads((source/'metadata.json').read_text(encoding='utf-8')),distributions=results,
                actual_model_access_pairs_verified=comparisons,policies_have_identical_results=True)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    check_outputs(list(args.source.glob('*.csv'))+[args.source/'metadata.json'],[args.output])
    evidence=analyze(args.source)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(evidence,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('verified_access_pairs='+str(evidence['actual_model_access_pairs_verified']))
if __name__=='__main__':main()
