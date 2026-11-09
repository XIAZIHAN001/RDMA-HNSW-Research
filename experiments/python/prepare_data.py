"""Validate fvecs, prepare deterministic subsets, and compute exact L2 truth."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_outputs(inputs, outputs):
    # Resolve/stat each unique path once: experiment matrices have hundreds of
    # outputs, and pairwise filesystem calls are expensive on mounted drives.
    source_paths, source_files = set(), set()
    for path in inputs:
        source_paths.add(path.resolve())
        if path.exists():
            stat = path.stat()
            source_files.add((stat.st_dev, stat.st_ino))
    destinations, destination_files = {}, {}
    for output in dict.fromkeys(outputs):
        resolved = output.resolve()
        file_id = None
        if output.exists():
            stat = output.stat()
            file_id = stat.st_dev, stat.st_ino
        if (resolved in source_paths or (file_id is not None and file_id in source_files) or
                resolved in destinations or (file_id is not None and file_id in destination_files)):
            raise ValueError('output path collision: ' + str(output))
        destinations[resolved] = output
        if file_id is not None:
            destination_files[file_id] = output


def read_fvecs(path, limit=None):
    size = Path(path).stat().st_size
    if size == 0 or size % 4:
        raise ValueError("empty or truncated fvecs file")
    words = np.memmap(path, dtype="<i4", mode="r")
    dim = int(words[0])
    if dim <= 0:
        raise ValueError("invalid fvecs dimension")
    if len(words) % (dim + 1):
        raise ValueError("truncated fvecs record")
    records = words.reshape(-1, dim + 1)
    if np.any(records[:, 0] != dim):
        raise ValueError("inconsistent fvecs dimensions")
    if limit is not None and not 0 < limit <= len(records):
        raise ValueError("requested subset count is outside the source")
    count = len(records) if limit is None else limit
    vectors = np.array(records[:count, 1:].view("<f4"), dtype=np.float32, order="C")
    if not np.isfinite(vectors).all():
        raise ValueError("fvecs contains non-finite coordinates")
    return vectors


def write_fvecs(path, vectors):
    with open(path, "wb") as output:
        header = np.array([vectors.shape[1]], dtype="<i4").tobytes()
        for vector in vectors:
            output.write(header)
            output.write(np.asarray(vector, dtype="<f4").tobytes())


def exact_topk(base, queries, k, batch_size=16):
    if queries.shape[1] != base.shape[1]:
        raise ValueError("query dimension mismatch")
    if not 1 <= k <= len(base):
        raise ValueError("k must fit the selected base")
    base64 = base.astype(np.float64)
    norms = np.einsum("ij,ij->i", base64, base64)
    node_ids = np.arange(len(base), dtype=np.int64)
    result = np.empty((len(queries), k), dtype=np.uint32)
    for begin in range(0, len(queries), batch_size):
        query = queries[begin:begin + batch_size].astype(np.float64)
        distances = (np.einsum("ij,ij->i", query, query)[:, None] + norms[None, :]
                     - 2 * query @ base64.T)
        np.maximum(distances, 0, out=distances)
        for offset, row in enumerate(distances):
            # Stable ID ordering makes tied distances reproducible.
            result[begin + offset] = np.lexsort((node_ids, row))[:k]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--base-count", type=int)
    parser.add_argument("--query-count", type=int)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--dataset-name", default="provided_fvecs")
    args = parser.parse_args(argv)
    try:
        check_outputs([args.base, args.queries],
                      [args.outdir / name for name in
                       ['base.fvecs', 'query.fvecs', 'ground_truth.npy', 'manifest.json']])
        base = read_fvecs(args.base, args.base_count)
        queries = read_fvecs(args.queries, args.query_count)
        start = time.perf_counter()
        truth = exact_topk(base, queries, args.k)
        truth_seconds = time.perf_counter() - start
        args.outdir.mkdir(parents=True, exist_ok=True)
        write_fvecs(args.outdir / "base.fvecs", base)
        write_fvecs(args.outdir / "query.fvecs", queries)
        np.save(args.outdir / "ground_truth.npy", truth)
        manifest = {
            "dataset": args.dataset_name, "distance": "squared_l2",
            "node_count": len(base), "query_count": len(queries),
            "dim": base.shape[1], "k": args.k,
            "selection": "first_n_in_original_order",
            "source_base": str(args.base.resolve()),
            "source_queries": str(args.queries.resolve()),
            "source_base_sha256": sha256_file(args.base),
            "source_queries_sha256": sha256_file(args.queries),
            "base_sha256": sha256_file(args.outdir / "base.fvecs"),
            "query_sha256": sha256_file(args.outdir / "query.fvecs"),
            "ground_truth_sha256": sha256_file(args.outdir / "ground_truth.npy"),
            "ground_truth_seconds": truth_seconds,
            "truth_method": "float64 exhaustive squared L2; distance then node ID",
        }
        (args.outdir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({key: manifest[key] for key in
                          ["dataset", "node_count", "query_count", "dim",
                           "ground_truth_seconds"]}))
        return 0
    except (OSError, ValueError) as error:
        print("prepare_data: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
