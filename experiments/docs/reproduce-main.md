# 主实验复现说明

大型本地主矩阵已按用户要求暂缓，本页保留未来复现步骤。此前 results/main-matrix-v1 的 23 次旧构建试测已停止，性能数据不作正式结论；不得恢复该目录。所有命令从仓库根目录执行。主矩阵在本地 Linux 文件系统使用索引副本；保留原始母本和 manifest。Windows 挂载目录用于源文件和持久日志，不能把挂载性能差异归因于读取策略。

## 数据准备

公开仓库不附带 SIFT 数据。先从 INRIA/IRISA TexMex 的官方发布取得 SIFT1M，将 fvecs 解压到 data/raw/sift，记录下载 URL 与 SHA256；原工作目录的已有数据不会随 git clone 下载。对每个规模执行：

```bash
# 依次将 N 改为 10000 / 50000 / 100000
N=10000
OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4 \
python3 -B experiments/python/prepare_data.py \
  --base data/raw/sift/sift_base.fvecs \
  --queries data/raw/sift/sift_query.fvecs \
  --base-count "$N" --query-count 1000 --k 10 \
  --dataset-name SIFT1M --outdir "data/main_sift${N}"
```

每个子集必须重新算 exact truth。初始全量官方 truth 不能直接过滤成子集 truth。索引由 Linux 文件系统中的 base 副本构建，再复制到母本位置，避免挂载目录耗时：

```bash
build_dir=experiments/build
scratch=$(mktemp -d /tmp/hnsw-index.XXXXXX)
cp "data/main_sift${N}/base.fvecs" "$scratch/base.fvecs"
"$build_dir/upstream/hnsw_build" --input "$scratch/base.fvecs" --dim 128 \
  --M 16 --ef-construction 100 --seed 42 --output "$scratch/index.bin"
cp "$scratch/index.bin" "data/main_sift${N}/index.bin"
# scratch 为本命令新建的专属临时目录；检查路径后按本地规则清理。
```

MNIST 需要另行从 Keras 官方数据源取得并放到 data/downloads/mnist.npz；先前验证的 SHA256 为 `731c5ac602752760c8e48fbffcf8c3b850d9dc2a2aedcf2cc48468fc17b673d1`：

```bash
OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4 \
python3 -B experiments/data/prepare_mnist.py \
  --archive data/downloads/mnist.npz --outdir data/main_mnist10000
```

同上方法建索引，但 dim=784，其他参数不变。该转换脚本保留原始像素，不做归一化。MNIST 是对预算下 GIST 优先项的替代；不要把两类数据结果混为一谈。

## 构建与正式运行

```bash
cmake -S experiments -B experiments/build \
  -DENABLE_RDMA=OFF -DENABLE_RDMA_CM=OFF -DBUILD_TESTING=ON
cmake --build experiments/build -j4
ctest --test-dir experiments/build --output-on-failure
python3 -B experiments/python/run_main_matrix.py \
  --matrix experiments/configs/main-matrix.json --outdir results/main-matrix-release-v1
```

未来新运行的已有目录可用作断点恢复：只跳过状态为 complete 且结果、epoch CSV hash 一致的轮次。源码、二进制或数据改变时 driver 会拒绝恢复，需使用新输出目录。`--limit N` 仅用于预算试测，不产生完整矩阵验收。

每轮全新索引副本，预生成 workload 共享于同输入比较组，保留每轮完整命令、stdout/stderr 与返回码。正式实验共 64 配置×3 轮=192 次。额外的 results/main-matrix 中两组数据是早期预算试测，不合入正式统计。

## 核验和重建图表

```bash
python3 -B experiments/python/analyze_main_matrix.py \
  --source results/main-matrix-release-v1 --output experiments/reports/main/evidence.json
```

分析器要求完整 192 次成功运行，逐操作输入、每 reader 快照、更新发布、策略计数、byte 守恒和结果一致。静态全部查询实例核对 exact truth；动态每非空 reader/epoch 首查询核对当前快照。计数应在三个 repeat 完全重复；耗时按每轮均值、每轮 percentile 的均值和样本 SD 报告。数据/日志不纳入 Git 体积，但本地保留。主报告生成脚本按用户要求移入临时记录，待正式实验完成后再恢复和验证，不属于当前可执行复现步骤。

从干净目录构建时用另一个目录或 Linux 临时目录，避免覆盖冻结实验二进制。运行 lab_epoch_client 的完整一组 c000：复制 SIFT10K 母本，指定相同 workload、预热 1000、fixed、G8、capacity10000、threads1、ef256、k10、verify-samples0；再用 validate_run 核验结果，并比较 quality_sha256 和 counters。复现记录输出至 results/reproduce-main；实际执行状态见实验进度。

## 当前适用范围

三个客户端现已支持真实 RDMA 接入，但本页矩阵仍是 mmap 配置。mmap 不是网络性能；模型 L/B 是未校准假设；真实 RDMA 的入口适配、连通、设备参数和硬件矩阵必须另行保存，详见 rdma-migration.md。不得把 mmap CSV 的 transport 字段改成 rdma。动态阶段重排为 queries→updates→reload，不支持把该结果作为持续竞争读写的吞吐。
