# RDMA 离线准备与小规模验收工具

当前没有服务器。以下工具的离线准备、输入保护、日志规则和失败路径可以本地验证；真实远端连接、发布及重连仍待设备验收。正式性能矩阵保持暂停。

## 1. 本机预检

从仓库根目录执行，输出路径必须是新文件：

```bash
python3 -B experiments/python/rdma_preflight.py \
  --build-dir experiments/build-rdma --output results/preflight-local.json
```

退出 0 表示本地最低环境条件具备；退出 2 表示设备或构建不可用；退出 1 表示输入/输出或脚本错误。检查实际 Linux 设备、CM 设备节点、构建开关、统一 Release 参数，以及三个客户端和 storage_server 各自的可执行权限、ibverbs/CM 动态库链接；同时保存内核、provider 和 IP 信息。它不会建立网络连接，`remote_connection_verified` 始终为 false。本地条件具备后仍要按 rdma-migration.md 检查两端路由、memlock、provider、rping 和 CM 集成。

## 2. 离线准备验收包

```bash
python3 -B experiments/python/rdma_smoke.py prepare \
  --base data/main_sift10000/base.fvecs \
  --queries data/main_sift10000/query.fvecs \
  --build-dir experiments/build-rdma --outdir results/rdma-smoke-bundle
python3 -B experiments/python/rdma_smoke.py plan \
  --bundle results/rdma-smoke-bundle
```

默认取前 1000 个节点、100 个查询，重新构建专用母本并计算子集 exact truth；生成 40 操作、30% upsert 的固定 workload。四策略各有 static、dynamic、epoch1、epoch4，共 16 种小规模正确性配置。M16、ef_search256、k10、G8、capacity10000、warm 缓存、不预热。

manifest.json 保存数据、母本、真值、workload、程序和源码哈希、提交号及准备命令。准备和 plan 都不会连接服务器；plan 中的 `$STORAGE_RDMA_IP` 等是待填写参数，输出为 argv 清单。包目录必须不存在，避免覆盖原始输入或旧证据。

母本用于每轮恢复和包完整性检查。计算程序本身始终通过 RDMA 查询，命令没有 --index；客户端取当前快照的 truth 也走远程传输。

## 3. 设备到位后按角色运行

先在两端确认相同代码、ABI、Release 和 RDMA 后端，复制同一个验收包。工具核对执行程序的哈希；如果构建产物与包不同，使用一致产物或重新准备包。可用 --build-dir 指定部署端程序位置。

每个 case 需要先启动对应存储服务，运行计算端，再停止存储服务。开始下一 case 时必须使用新目录和新存储进程，不复用上一轮更新后的索引。以下示例要求环境变量已填写真实 IP 和设备名：

```bash
# 存储端：先复制母本到独立运行目录，再启动服务；此命令持续运行。
python3 -B experiments/python/rdma_smoke.py run \
  --role storage --case static_fixed --bundle results/rdma-smoke-bundle \
  --build-dir experiments/build-rdma --outdir results/storage-runs/static_fixed \
  --host "$STORAGE_RDMA_IP" --device "$STORAGE_DEVICE"

# 计算端：记录实际 argv、程序和包哈希、stdout/stderr、返回码及结果哈希。
python3 -B experiments/python/rdma_smoke.py run \
  --role compute --case static_fixed --bundle results/rdma-smoke-bundle \
  --build-dir experiments/build-rdma --outdir results/compute-runs/static_fixed \
  --host "$STORAGE_RDMA_IP" --device "$COMPUTE_DEVICE"
```

case 名称使用 `static_<policy>`、`dynamic_<policy>`、`epoch1_<policy>`、`epoch4_<policy>`。policy 为 fixed/header_actual/cached_length/prefix。两端分别使用自己的设备名。所有模式限定 CM；动态与 epoch 自动加 --verify。先用 rping/CM 集成确认连通，不能只凭存储进程启动判断网络已建立。

新运行目录避免覆盖旧日志。计算端默认进程超时 120 秒，可用 --timeout 调整；超时、异常或非零退出都会留下 failed 记录。更新错误可能发生在提交后，不能盲目重试；先按迁移手册检查服务器状态并恢复母本。存储端 Ctrl+C 会停止本次进程并保存 stopped 记录，母本不被服务端修改。

run.json 使用 rdma_smoke_run_v2，记录执行时 bundle、run 和 executable 的绝对路径。分析器将 argv 中的完整路径与这些记录绑定，并核对包和输出哈希。两端原始目录可以不同，归档文件也可移动；保留原始记录即可，不需要改写 argv。旧 v1 记录缺少路径绑定，须重新运行生成 v2。

## 4. RDMA 日志核验

```bash
python3 -B experiments/python/analyze_rdma.py \
  --bundle results/rdma-smoke-bundle --runs results/compute-runs \
  --output results/rdma-smoke-validation.json
```

默认要求 16 个 case 全部成功，minimum recall 至少 0.95。检查实际三种 CSV 格式、输入/命令/二进制一致、结果哈希、操作完整性、策略计数、READ/WRITE WR、快照发布与 reader 分配。静态使用包中 exact truth；动态和 epoch 必须每条查询有 C++ 当前远程快照核验值。只在相同调度内比较策略和并发数；串行与 epoch 的查询快照不同，不相互比较结果签名。

静态真值必须是整数 ID 数组，浮点数不会截断成 ID。查询必须含节点和邻接读取；节点逻辑读取按当前 Linux x86_64 ABI 的 24 字节头部加 dim×4 核对。物理 READ 字节等于逻辑请求字节，WR 次数按 CM 每次 1 MiB 分段核对；M16 验收包的邻接请求每次只占一个 WR。这些规则限定当前源码和验收包，修改布局或 staging 大小时需要同步核验规则。

--allow-partial 可核对已有完整 case，但输出 complete/comparison_complete 为 false；不会跳过失败记录。--min-recall 可明确调整质量门槛。输出必须为新文件，不能覆盖包、原始日志、源码或程序，包括硬链接。

远端 rewrite 的 0 按 not_collected 解释，报告 server_rewrite_bytes 为 null。控制 SEND/RECV 未采集。此工具做日志正确性验收；硬件连接验证、服务器实际初始状态和正式性能验收不会仅凭 CSV 被自动标为通过，需要保留两端验收记录。

## 5. 无设备验证的证据范围

测试夹具必须标为 test_fixture，并显式使用 --allow-fixture；报告 measurement 为 test_fixture_validation，硬件验收字段为 false。真实 mmap 回归日志保留 transport=mmap，不改写成 RDMA。

本检查点独立审查修复后的执行记录见 [summary.json](../../artifacts/validation/offline-tools-2026-10-09.json)：实际小型 SIFT 包、16 种 mmap 正确性样例（736 查询、144 upsert，最低 recall=1）、16 种手算日志夹具、无设备预检和连接失败。本地 23/23 测试通过，真实 RDMA 库构建 23 通过、1 硬件测试跳过。它们验证准备/分析流程，不能证明真实网络行为或性能。设备到位后的补验收步骤见 [迁移手册](rdma-migration.md)。
