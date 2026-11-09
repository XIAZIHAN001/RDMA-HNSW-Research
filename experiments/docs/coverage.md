# 需求覆盖

| 要求 | 实现与状态 |
| --- | --- |
| 读取路径、容量、布局 | read-path.md，已完成 |
| 四策略、边界与修复 | experiments/src、strategy_test，已完成 |
| 节点+层 LRU、动态生命周期 | degree_cache、dynamic_test，已完成 |
| 轨迹和开销模拟 | simulate_reads.py 与分析器，已完成 |
| 参数矩阵 | configs/main-matrix.json，已准备；正式运行暂停 |
| 三客户端 RDMA 接入 | transport_options、remote_snapshot，代码已完成 |
| 预检、离线包和日志核验 | 无设备检查点已完成 |
| 两端网络与硬件性能 | 待验收 |
| 结题与答辩 | 暂停 |

详见[项目进度](../../docs/project/progress.md)。
