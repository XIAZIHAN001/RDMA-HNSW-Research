# RDMA-HNSW Research

**面向动态图索引的分级读取优化机制**：基于教师的 RDMA-HNSW-Lab，比较固定读取、精确两阶段、历史长度缓存和分级前缀读取，研究邻接数据量、依赖 READ 与动态修复的取舍。

**当前：四策略、动态/epoch 客户端和实际 RDMA 接入代码已实现，无设备正确性检查点已完成。尚无真实远程 DRAM 连接及硬件性能验收。正式性能矩阵和结题材料暂停。**

| 阅读入口 | 内容 |
| --- | --- |
| [任务书](docs/project/taskbook.md) | 公开整理版，保留研究要求和原日期 |
| [项目解读](docs/project/project-brief.md) | 本地任务解读的文字版 |
| [五周时间表](docs/project/schedule.md) | 原七阶段要求压缩为五周的安排 |
| [项目进度](docs/project/progress.md) | 已实现、已有证据及待验收项 |
| [论文说明](docs/literature/README.md) | HNSW、SHINE、动态图 ANN、RDMA；与任务文档分开 |
| [读取路径](experiments/docs/read-path.md) | 文件布局、逻辑读取与计数范围 |
| [离线验收](experiments/docs/rdma-offline-tools.md) | 预检、验收包、按角色运行和核验 |
| [RDMA 部署](experiments/docs/rdma-migration.md) | 两端 CM、更新与快照验收 |
| [发布验证](docs/validation/README.md) | 本次公开源码重新构建记录 |

## 工程结构

```text
include/rdma_hnsw/       公共 C++ 接口
src/                    教学底座、mmap、IB 和 CM 后端
experiments/src/        四策略、动态/epoch、workload
experiments/python/     数据真值、轨迹模拟、运行与分析
experiments/tests/      策略、生命周期、CLI、计数及输入保护
experiments/configs/    参数和矩阵
example/                独立 API 示例
third_party/hnswlib/    构图依赖和 Apache-2.0 许可证
docs/project/           任务书、时间表和进度
docs/literature/        独立论文说明
artifacts/validation/   小型验证摘要
```

## Linux / WSL 构建

需要 C++20、CMake ≥3.18、Python 3 和 NumPy；绘图另用 Matplotlib。Ubuntu 22.04 示例：

```bash
sudo apt-get install build-essential cmake python3 python3-numpy python3-matplotlib
cmake -S experiments -B experiments/build -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_RDMA=OFF -DENABLE_RDMA_CM=OFF -DBUILD_TESTING=ON
cmake --build experiments/build -j4
ctest --test-dir experiments/build --output-on-failure
```

测试自建小图和输入，不需要下载数据集。自己的 fvecs 数据可用 `experiments/python/prepare_data.py` 创建子集和 exact truth。

底座合成示例：

```bash
mkdir -p data/quickstart
./experiments/build/upstream/hnsw_build --generate 1000 --dim 32 --M 16 \
  --ef-construction 100 --output data/quickstart/index.bin
./experiments/build/upstream/compute_client --transport mmap \
  --index data/quickstart/index.bin --queries-count 100 --ef-search 64 --k 10
```

此示例验证底座；四策略入口为 `lab_client`、`lab_dynamic_client`、`lab_epoch_client`。底座 `compute_client` 不会仅凭策略名字自动启用实验实现。

## 四策略

| 策略 | 读取方式 | 取舍 |
| --- | --- | --- |
| `fixed` | degree 与完整容量 slot | 一次逻辑 READ，可能含空槽 |
| `header_actual` | 先 degree，再实际邻居 | 浪费少，非空邻接增加依赖 READ |
| `cached_length` | 历史 degree 前缀，冷态先头部 | 低估补读；节点+层 LRU |
| `prefix` | 历史 degree 量化到 G 的倍数 | 以部分过读减少补读；G=1/2/4/8/16 |

底层容量 2M，上层 M。历史 degree 是预测，必须读取真实 degree 并在不足时补齐完整邻居；返回顺序与搜索流程不变。不使用 generation 预读替代修复。零邻居可学习零长度，仍读取 degree。

邻接流量与节点/向量流量分开计数；CM 的物理 WR 按 1 MiB 分段。未采集的控制面 SEND/RECV 或服务端 rewrite 不能算成零成本。

## RDMA 构建

```bash
sudo apt-get install libibverbs-dev librdmacm-dev
cmake -S experiments -B experiments/build-rdma -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_RDMA=ON -DENABLE_RDMA_CM=ON -DBUILD_TESTING=ON
cmake --build experiments/build-rdma -j4
ctest --test-dir experiments/build-rdma --output-on-failure
python3 -B experiments/python/rdma_preflight.py \
  --build-dir experiments/build-rdma --output results/preflight.json
```

预检退出 2 表示设备/构建不可用，退出 0 也不代表两端已连接。static 支持 IB/CM，dynamic/epoch 限 CM；离线验收工具统一 CM。RDMA 客户端要求显式远端地址，拒绝用本地 --index 替代。连接与更新可见性按两端手册补验收。

## 证据与发布范围

2026-10-09 本地小型 SIFT 检查点有 16 配置、736 查询、144 更新，最低 Recall@10=1，见[历史摘要](artifacts/validation/offline-tools-2026-10-09.json)。这不是硬件性能。旧试测曾有非统一优化选项，耗时排名不作为正式结论。

Git 保留源码、测试、CMake、配置和研究说明；完整数据集、原始日志、构建缓存、个人身份字段和代理执行记录留在本地。论文保留原创解读与出处。必要的 Linux x86_64 可执行产物放在 [Releases](https://github.com/XIAZIHAN001/RDMA-HNSW-Research/releases)。

## 来源与许可证

基于 [diandianjunA/RDMA-HNSW-Lab](https://github.com/diandianjunA/RDMA-HNSW-Lab)，教学基准为 `1fc068b8442b24592de635099ffb45dc7bcd42ef`；研究代码快照为 `d5c78a5e6ac28902a0cfac5c1c5300159454dc38`。公开仓库使用整理后的新历史，原工作仓库保持不变。

保留原工程 [MIT LICENSE](LICENSE) 和 hnswlib [Apache License 2.0](third_party/hnswlib/LICENSE)。论文和外部数据仍适用各自来源条款。
