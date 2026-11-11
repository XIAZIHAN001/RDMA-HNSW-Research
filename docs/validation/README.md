# 发布版本验证

在整理后的公开源码目录执行干净的 Linux Release 构建，分别关闭与启用真实 ibverbs/CM 库，并运行完整 CTest。环境为 Ubuntu 22.04/WSL x86_64，无 RDMA 设备。

2026-11-09 实际结果：

| 检查 | 结果 |
| --- | --- |
| mmap Release | 23 项通过，0 失败 |
| ibverbs + CM Release | 23 项通过，0 失败，1 项硬件集成跳过 |
| 合成数据底座示例 | 1000 节点、32 维、100 次 mmap 查询，退出 0 |
| RDMA 预检 | 四个部署程序可执行且库链接正常；唯一阻塞是没有 RDMA 设备 |

GCC 11.4.0、CMake 3.22.1、NumPy 1.21.5；策略与客户端统一 Release。构建使用源码路径映射，避免预编译二进制包含工作目录前缀。

本次机器可读记录见[公开版本验证](../../artifacts/validation/publication-2026-11-09.json)。另见[历史正确性摘要](../../artifacts/validation/offline-tools-2026-10-09.json)；它不等同于本目录重新运行 SIFT 矩阵。

硬件测试跳过不能算硬件通过。发布还检查源码清单、文档内部链接和凭据格式；不代表真实两端验收或独立全量安全审计。

## 2026-11-11 双节点验证

完成静态 IB、SIFT 1K CM 功能验证、96 轮四档 RDMA CM 主实验及 42 轮新增 SIFT 10K 对照。[实验图表、逐轮指标与复现设置](../../artifacts/validation/rdma-cm-2026-11-11/README.md)
