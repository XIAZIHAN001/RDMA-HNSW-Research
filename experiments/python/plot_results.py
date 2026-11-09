"""Render degree and node-access figures from verified pilot summaries."""
import argparse
import csv
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summaries', type=Path, nargs='+', required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    summaries = [json.loads(path.read_text(encoding='utf-8')) for path in args.summaries]
    args.outdir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    figure, axes = plt.subplots(2, len(summaries), figsize=(6 * len(summaries), 7), squeeze=False)
    for col, summary in enumerate(summaries):
        for level in [0, 1]:
            ax = axes[level, col]
            for source, label in [('graph_distribution', 'All active node-layers'),
                                  ('access_distribution', 'Weighted by accesses')]:
                distribution = summary[source][str(level)]
                histogram = distribution['histogram']
                degree = np.array(sorted(map(int, histogram)))
                fraction = np.array([histogram[str(d)] / distribution['count'] for d in degree])
                ax.plot(degree, fraction * 100, marker='.', label=label)
            ax.set(title=f"SIFT1M first {summary['catalog']['node_count']:,}: level {level}",
                   xlabel='Actual degree', ylabel='Share (%)')
            ax.grid(alpha=.2)
            ax.legend(fontsize=8)
    figure.suptitle('Fixed-read pilot: graph distribution versus accessed distribution')
    figure.tight_layout()
    for extension in ['png', 'svg']:
        figure.savefig(args.outdir / f'degree_distributions.{extension}', dpi=180)
    plt.close(figure)

    figure, axes = plt.subplots(1, len(summaries), figsize=(6 * len(summaries), 3.8), squeeze=False)
    for col, (path, summary) in enumerate(zip(args.summaries, summaries)):
        with (path.parent / 'node_heat.csv').open(newline='', encoding='utf-8') as source:
            counts = np.array([int(row['accesses']) for row in csv.DictReader(source)])
        ax = axes[0, col]
        population = summary['catalog']['node_count']
        ax.plot(np.arange(1, len(counts) + 1) / population * 100, np.cumsum(counts) / counts.sum() * 100)
        ax.axvline(1, color='gray', linestyle=':', label='Top 1% of all nodes')
        ax.set(title=f'SIFT1M first {population:,}', xlabel='Nodes ranked by accesses (% of all nodes)',
               ylabel='Cumulative adjacency accesses (%)', ylim=(0, 102))
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    figure.suptitle('Node access concentration: 100 queries, ef_search=256')
    figure.tight_layout()
    for extension in ['png', 'svg']:
        figure.savefig(args.outdir / f'node_access_concentration.{extension}', dpi=180)
    plt.close(figure)
    print(f'Figures written to {args.outdir}')


if __name__ == '__main__':
    main()
