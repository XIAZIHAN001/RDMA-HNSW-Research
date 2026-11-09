# SHINE：确认真正的远程流量瓶颈

来源：Widmoser、Kocher、Augsten，[SHINE: A Scalable HNSW Index in Disaggregated Memory](https://arxiv.org/abs/2507.17647v1)，使用已提供的 v1 原文，重点为摘要、引言与缓存动机。

SHINE 在存算分离环境中保持全局 HNSW 图结构，避免图分片切断边造成的质量影响。它指出高维向量不断从远端读取会消耗大量网络带宽，利用计算端向量缓存，并逻辑组合计算节点缓存以提高总体利用率。

本课题缓存的是“节点 ID + 图层”的历史 degree 或分级 extent；它不保存完整向量，不实现 SHINE 的跨计算节点缓存。因此二者共享的是对远程读取成本的关注，而不是相同的缓存算法或系统。

## 对本实验的直接启发

当前代码每次节点读取为 `24+4×dim` 字节。128 维时是 536 字节，784 维时是 3160 字节；M16 的底层完整邻接只有 132 字节。搜索过程中节点向量还可能被重复读取。因此邻接浪费减少很多，总字节或 QPS 仍可能变化很小。

应该同时报告 `neighbor_requested_bytes / requested_bytes`、READ 次数、依赖补读、缓存命中/淘汰和 recall。改变维度时固定其他参数；线程数增加时注明每个 reader 有独立缓存，总缓存预算会随 reader 数增加。

论文的“可扩展”结论不能由本地 mmap 或单机缓存测试验证。本项目仍需要真实设备测量、统一缓存预算和明确的网络/CPU瓶颈分析。

代码入口：[查询读取](../../src/runtime/query_engine.cc)、[历史长度 LRU](../../experiments/src/degree_cache.h)、[prefix](../../experiments/src/prefix_strategy.cc)。
