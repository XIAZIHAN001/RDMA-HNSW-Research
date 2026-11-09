# 论文阅读与研究设计

这些是根据已提供原文和本地代码整理的说明，聚焦课题需要的思路、实现边界和待验证问题；不是全文翻译或论文系统的复现。论文性能数字不能作为本项目的实验结果。公开仓库保留原创笔记和正式出处，原文及中英对照 PDF 不重复上传。

| 文献 | 本课题需要理解的内容 | 笔记 / 出处 |
| --- | --- | --- |
| HNSW，Malkov / Yashunin | 多层图、搜索质量与连接/候选参数 | [笔记](hnsw.md) · [arXiv v4](https://arxiv.org/abs/1603.09320v4) |
| SHINE，Widmoser / Kocher / Augsten | 保持图结构的存算分离、向量流量与缓存 | [笔记](shine.md) · [arXiv v1](https://arxiv.org/abs/2507.17647v1) |
| DiskANN，NeurIPS 2019 | Vamana、SSD 与内存预算的协同设计 | [动态图笔记](dynamic-indexes.md) · [论文](https://papers.nips.cc/paper/2019/hash/09853c7fb1d3f8ee67a61b6bf4a7f8e6-Abstract.html) |
| FreshDiskANN，2021 | 持续更新、有效数据集合与检索质量 | [动态图笔记](dynamic-indexes.md) · [arXiv v1](https://arxiv.org/abs/2105.09613v1) |
| A Survey of Storage Systems in the RDMA Era，2022 | 通信、并发控制、缓存、资源管理 | [RDMA 笔记](rdma-systems.md) · [DOI](https://doi.org/10.1109/TPDS.2022.3188656) |
| FaRM，NSDI 2014 | 远程内存、单边访问与消息传递 | [RDMA 笔记](rdma-systems.md) · [USENIX](https://www.usenix.org/conference/nsdi14/technical-sessions/dragojevi%C4%87) |
| FaSST，OSDI 2016 | 两边 RPC、远端 CPU 与往返成本的取舍 | [RDMA 笔记](rdma-systems.md) · [USENIX](https://www.usenix.org/conference/osdi16/technical-sessions/presentation/kalia) |
| Synchronizing Disaggregated Data Structures with One-Sided RDMA，TODS 2025 | 多次 READ 的一致性与同步陷阱 | [RDMA 笔记](rdma-systems.md) · [DOI](https://doi.org/10.1145/3716377) |

任务要求和实施日期位于独立的 [docs/project](../project/taskbook.md)。
