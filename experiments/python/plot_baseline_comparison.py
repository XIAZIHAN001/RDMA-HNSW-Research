"""Plot the measured byte/operation tradeoff between fixed and header_actual."""
import argparse
import json
from pathlib import Path

from prepare_data import check_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    check_outputs([args.evidence], [args.outdir / f'{stem}.{extension}'
                                   for stem in ['baseline_tradeoff', 'local_and_model_scopes']
                                   for extension in ['png', 'svg']])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    evidence = json.loads(args.evidence.read_text(encoding='utf-8'))
    scales = evidence['scales']
    args.outdir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    figure, axes = plt.subplots(len(scales), 2, figsize=(10, 3.4 * len(scales)), squeeze=False)
    names = ['fixed', 'header_actual']
    x = np.arange(2)
    for row, (size, scale) in enumerate(scales.items()):
        useful = [scale[name]['metrics']['neighbor_useful_bytes'] / scale[name]['query_count'] / 1024 for name in names]
        wasted = [scale[name]['metrics']['neighbor_wasted_bytes'] / scale[name]['query_count'] / 1024 for name in names]
        ax = axes[row, 0]
        ax.bar(x, useful, label='Useful', color='#3478B9')
        ax.bar(x, wasted, bottom=useful, label='Unused capacity', color='#CF7D36')
        ax.set(title=f'SIFT {int(size):,}: adjacency bytes', ylabel='KiB per query', xticks=x, xticklabels=names)
        ax.legend(fontsize=8)
        dependent = np.array([scale[name]['metrics']['neighbor_dependent_reads'] / scale[name]['query_count'] for name in names])
        operations = np.array([scale[name]['metrics']['neighbor_reads'] / scale[name]['query_count'] for name in names])
        ax = axes[row, 1]
        ax.bar(x, operations - dependent, label='First READ', color='#3478B9')
        ax.bar(x, dependent, bottom=operations - dependent, label='Dependent READ', color='#CF7D36')
        ax.set(title=f'SIFT {int(size):,}: adjacency READs', ylabel='READ operations per query', xticks=x, xticklabels=names)
        ax.legend(fontsize=8)
    figure.suptitle('Baseline tradeoff: fewer bytes versus additional dependent reads')
    figure.tight_layout()
    for extension in ['png', 'svg']:
        figure.savefig(args.outdir / f'baseline_tradeoff.{extension}', dpi=180)
    plt.close(figure)

    figure, axes = plt.subplots(1, 2, figsize=(10, 3.7))
    locations = np.arange(len(scales))
    for name, shift, color in [('fixed', -.18, '#3478B9'), ('header_actual', .18, '#CF7D36')]:
        means = [scale[name]['mean_query_ms'] for scale in scales.values()]
        deviations = [scale[name]['stdev_run_mean_ms'] for scale in scales.values()]
        axes[0].bar(locations + shift, means, width=.36, yerr=deviations, capsize=4, color=color, label=name)
        model = [scale[name]['model']['totals']['estimated_latency_us'] / scale[name]['query_count'] / 1000
                 for scale in scales.values()]
        axes[1].bar(locations + shift, model, width=.36, color=color, label=name)
    for ax in axes:
        ax.set(xticks=locations, xticklabels=[f'{int(size):,}' for size in scales], xlabel='Base vectors')
        ax.legend(fontsize=8)
    axes[0].set(title='Local pread: full query (pilot)', ylabel='Mean latency (ms); SD of 3 run means')
    model = evidence['models']
    axes[1].set(title=f"Serial adjacency model: L={model['latency_us']:g} us, B={model['bandwidth_gbps']:g} Gbit/s",
                ylabel='Estimated adjacency latency per query (ms)')
    figure.suptitle('Local measurement and assumed model have different scopes')
    figure.tight_layout()
    for extension in ['png', 'svg']:
        figure.savefig(args.outdir / f'local_and_model_scopes.{extension}', dpi=180)
    plt.close(figure)
    print(f'Figures written to {args.outdir}')


if __name__ == '__main__':
    main()
