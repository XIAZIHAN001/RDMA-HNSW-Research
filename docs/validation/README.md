# 发布版本验证

在整理后的公开源码目录执行干净的 Linux Release 构建，分别关闭与启用真实 ibverbs/CM 库，并运行完整 CTest。环境为 Ubuntu 22.04/WSL x86_64，无 RDMA 设备。

本次记录将写入 artifacts/validation/publication-2026-11-09.json。另见[历史正确性摘要](../../artifacts/validation/offline-tools-2026-10-09.json)；它不等同于本目录重新运行 SIFT 矩阵。

硬件测试跳过不能算硬件通过。发布还检查源码清单、文档内部链接和凭据格式；不代表真实两端验收或独立全量安全审计。
