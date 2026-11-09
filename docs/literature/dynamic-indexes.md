# DiskANN / FreshDiskANN：存储布局、更新与当前真值

来源：[DiskANN，NeurIPS 2019](https://papers.nips.cc/paper/2019/hash/09853c7fb1d3f8ee67a61b6bf4a7f8e6-Abstract.html)、[FreshDiskANN，arXiv:2105.09613v1](https://arxiv.org/abs/2105.09613v1)。本页据所提供原文的摘要、引言和系统设计动机整理。

DiskANN 把图式近邻搜索与 SSD/内存预算一起设计，提出 Vamana 并面向单机大规模索引。它提示我们，图算法、数据布局、缓存和 I/O 单位共同决定性能，不能把“每次少读字节”孤立地看成系统优化。SSD 页与 RDMA 请求有不同成本，本项目的 HNSW 固定 slot 不等于 DiskANN 的布局。

FreshDiskANN 关注数据集合持续插入、删除时索引的新鲜性和查询质量。其问题定义要求查询针对随时间变化的有效数据集合，并同时衡量检索质量、更新成本和硬件资源。本项目不实现其 SSD 系统及更新算法，而借用“必须用当前有效集合判断正确性”的原则。

## 在本课题中的必要细节

更新前缓存的 degree 只是预测。degree 增大时，首次读取必须包含真实 degree，并在不足时补回剩余邻居；degree 下降时，忽略多读的无效部分并学习新长度。删除、插入或 upsert 后仍用初始 base 的 truth，可能把正确结果判错或漏报错误。

小图回归应覆盖 insert/upsert/erase 和完整当前快照 truth；较大性能实验的抽样 probe 要明确频率，不能把抽样通过写成逐查询全量验证。离线验收包的动态/epoch 模式显式加 `--verify`，属于正确性采集，其系统工作量不能混同无核验的性能运行。

目前 epoch 调度先完成一批查询，再顺序更新并刷新 reader；它不是原始操作流的持续竞争读写，也不具备论文系统的并发和故障保证。

代码入口：[动态回归](../../experiments/tests/dynamic_test.cc)、[串行客户端](../../experiments/src/dynamic_client.cc)、[epoch 客户端](../../experiments/src/epoch_client.cc)、[远程 truth 快照](../../experiments/src/remote_snapshot.h)。
