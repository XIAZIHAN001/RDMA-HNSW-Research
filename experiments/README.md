# 实验子工程

四策略、静态/串行动态/epoch 三入口、轨迹模拟和 RDMA 接入已实现；真实设备和正式矩阵待验收。参见[完整进度](../docs/project/progress.md)。

从仓库根目录构建：

```bash
cmake -S experiments -B experiments/build -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_RDMA=OFF -DENABLE_RDMA_CM=OFF -DBUILD_TESTING=ON
cmake --build experiments/build -j4
ctest --test-dir experiments/build --output-on-failure
```

| 程序 | 用途 |
| --- | --- |
| lab_client | 静态策略、查询和可选轨迹 |
| lab_dynamic_client | 串行更新、当前快照核验 |
| lab_epoch_client | 独立 reader 与查询/更新/刷新阶段 |
| lab_workload | 固定种子的操作流 |
| prepare_data.py / simulate_reads.py | 子集真值与轨迹模拟 |
| rdma_preflight.py / rdma_smoke.py / analyze_rdma.py | 无设备准备与 RDMA 验收 |

参见[读取路径](docs/read-path.md)、[离线工具](docs/rdma-offline-tools.md)、[RDMA 手册](docs/rdma-migration.md)、[矩阵约束](docs/reproduce-main.md)和[论文笔记](../docs/literature/README.md)。

生成的数据、索引和日志应保留在新输出目录；不同策略使用同一母本、参数和 workload。--verify 的额外正确性工作会改变系统负载，不得与无核验性能运行混合比较。
