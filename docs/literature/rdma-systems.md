# RDMA 系统：往返、控制面与同步边界

来源：[RDMA 存储系统综述](https://doi.org/10.1109/TPDS.2022.3188656)、[FaRM](https://www.usenix.org/conference/nsdi14/technical-sessions/dragojevi%C4%87)、[FaSST](https://www.usenix.org/conference/osdi16/technical-sessions/presentation/kalia)、[单边 RDMA 同步研究](https://doi.org/10.1145/3716377)。根据所提供原文的摘要、引言和本课题相关机制整理，不覆盖其完整系统实现。

综述从通信模式、并发控制、容错、缓存和资源管理解释 RDMA 存储系统的设计取舍。FaRM 把远程内存抽象、直接访问和消息传递结合；FaSST 则研究两边 datagram RPC 的灵活性与资源效率。两者说明“绕过远端 CPU”不是唯一目标：多次依赖读取、连接状态、批处理和控制消息都可能影响性能。本课题不复现其事务、可靠性或集群性能。

## 多次读取需要一致的版本

2025 年 TODS 同步研究指出，一些单边同步方案会出现罕见数据损坏，正确性取决于具体同步设计，不能仅从单次 READ 完成推定。

本课题的第一次 READ 带 degree，可判断预测长度是否足够，却不能单独保证第二次 READ 与它来自同一版本。目前 CM 会话使用不可变 MR 快照，更新后刷新 catalog/会话才能看到新版本；mmap epoch 在 reader 结束后才更新和刷新。旧会话稳定与新数据可见是两种不同性质，必须分别验证。

## 测量范围必须写清

- 逻辑 READ 是查询/策略请求；CM 物理 READ WR 会按 1 MiB staging 大小分段，不能笼统假设二者次数总相等。
- 查询阶段的 READ/WRITE WR 计数不包括控制面的 SEND/RECV；未采集不能解释成没有成本。
- 远端更新耗时包括提交、服务端发布和 catalog 获取；未采集服务端 rewrite 字节时应报告 unknown/null。
- 每个 reader 使用独立 ComputeNode/QP；连接、资源和刷新成本都需要真实设备补验收。

代码入口：[CM 后端](../../src/transport/rdma_cm_transport.cc)、[远端校验](../../experiments/python/analyze_rdma.py)、[设备验收手册](../../experiments/docs/rdma-migration.md)。
