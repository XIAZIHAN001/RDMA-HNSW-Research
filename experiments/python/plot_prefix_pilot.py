"""Plot adjacency byte/READ tradeoffs and assumed-model prefix sensitivity."""
import argparse
import json
from pathlib import Path
from prepare_data import check_outputs

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--outdir',type=Path,required=True)
    args=parser.parse_args()
    check_outputs([args.evidence],[args.outdir/f'prefix_tradeoff.{extension}' for extension in ['png','svg']])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    evidence=json.loads(args.evidence.read_text(encoding='utf-8'));static=evidence['static']
    args.outdir.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False})
    figure,axes=plt.subplots(2,2,figsize=(12,8))
    labels=['Fixed','Header','Exact hint']+[f'G={g}' for g in [1,2,4,8,16]]
    values=list(static.values());x=np.arange(len(values))
    useful=[v['counters']['useful_bytes']/v['query_count']/1024 for v in values]
    wasted=[v['counters']['wasted_bytes']/v['query_count']/1024 for v in values]
    ax=axes[0,0];ax.bar(x,useful,label='Useful',color='#3478B9');ax.bar(x,wasted,bottom=useful,label='Unused',color='#CF7D36')
    ax.set(title='Static SIFT10K: adjacency bytes',ylabel='KiB/query',xticks=x,xticklabels=labels);ax.legend()
    operations=np.array([v['counters']['read_ops']/v['query_count'] for v in values])
    dependent=np.array([v['counters']['dependent_reads']/v['query_count'] for v in values])
    ax=axes[0,1];ax.bar(x,operations-dependent,label='First READ',color='#3478B9');ax.bar(x,dependent,bottom=operations-dependent,label='Dependent READ',color='#CF7D36')
    ax.set(title='Static SIFT10K: adjacency READs',ylabel='READ/query',xticks=x,xticklabels=labels);ax.legend()
    g=[1,2,4,8,16];ax=axes[1,0]
    ax.plot(g,[static[f'prefix_g{value}']['assumed_adjacency_us_per_query'] for value in g],marker='o',label='Prefix (static)')
    ax.axhline(static['fixed_g8']['assumed_adjacency_us_per_query'],color='#55616F',ls='--',label='Fixed')
    ax.axhline(static['cached_length_g8']['assumed_adjacency_us_per_query'],color='#CF7D36',ls=':',label='Exact hint')
    ax.set(title='Assumed serial adjacency model: L=3 us, B=100 Gbit/s',xlabel='Prefix G',ylabel='Estimated us/query',xticks=g);ax.legend()
    ax=axes[1,1]
    for distribution,color in [('random','#3478B9'),('hotspot','#CF7D36')]:
        values=evidence['dynamic'][distribution]
        ax.plot(g,[values[f'g{value}_warm']['counters']['hint_underestimates'] for value in g],marker='o',color=color,label=distribution)
    ax.set(title='Dynamic pilot: repairs after retained-hint hits',xlabel='Prefix G',ylabel='Repair count (28 queries, 12 upserts)',xticks=g);ax.legend()
    figure.suptitle('Historical prefix levels: extra bytes versus fewer dependent READs')
    figure.tight_layout()
    for extension in ['png','svg']:figure.savefig(args.outdir/f'prefix_tradeoff.{extension}',dpi=180)
    plt.close(figure)
if __name__=='__main__':main()
