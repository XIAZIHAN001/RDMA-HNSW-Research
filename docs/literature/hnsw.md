# HNSW：搜索算法与传输策略分离

来源：Malkov、Yashunin，[Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs](https://arxiv.org/abs/1603.09320v4)，重点为摘要、层级构造及查询过程。

HNSW 用随机层级组织近邻图，上层提供较大尺度的导航，较低层进一步搜索。构图连接数、搜索候选宽度和邻居选择都会影响 recall 与工作量。传输优化实验应固定这些因素，否则访问的图、候选集合或精度改变后，网络字节差异就不能单独归因于读取机制。

## 与本代码的连接

- `src/graph/hnsw_builder.cc` 使用 vendored hnswlib 构图并导出教学文件格式；运行时 `QueryEngine` 是本项目自己的实现，不能直接等同于完整 hnswlib 查询性能。
- 当前 Linux x86_64 格式的节点头部为 24 字节，节点向量占 `4×dim` 字节；邻接 slot 含 4 字节 degree 和预留邻居 ID。
- 底层邻接容量为 `2M`，上层为 `M`；容量是预留空间，degree 是本次快照中的实际邻居数量。
- `NeighborReadStrategy` 改变 READ 的范围与依赖关系；完整邻居和顺序必须保持一致，搜索流程不随策略改动。

M=16 时，底层固定邻接请求为 `4+4×32=132` 字节；若实际 degree=10，精确邻接数据只有 44 字节。节点向量请求另计，不能把邻接收益直接当成总流量收益。

## 实验应回答什么

先检查每种策略返回的邻居和最终 Top-k ID 是否一致，再比较按实际访问加权的 degree/容量分布，而非只看全图平均 degree。最后将邻接请求字节与总请求字节同时报告，并在相同 recall 和参数下解释延迟。

代码入口：[读取路径](../../experiments/docs/read-path.md)、[策略接口](../../experiments/src/read_strategies.h)、[策略回归](../../experiments/tests/strategy_test.cc)。
