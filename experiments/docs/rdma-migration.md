# RDMA 迁移与设备验收手册

当前状态：**没有 RDMA 服务器；硬件验收未执行**。2026-10-08 检查本机 WSL，`/sys/class/infiniband` 不存在。主实验全部采用 mmap。现已安装 libibverbs-dev/librdmacm-dev 39.0，独立 Release 构建确认两个后端启用。20 项无设备测试通过，CM 集成测试 Skipped；这些不代表网络连通或硬件性能。

接入代码已完成独立审查，发现的冻结输入覆盖问题已修复并回归。构建、测试、动态库链接及无设备连接失败的实际命令与退出码见 [验证记录](../../artifacts/validation/offline-tools-2026-10-09.json)；完整逐行日志保留在原本地工作目录，公开版本只含去掉本机路径的摘要。无设备连接返回非零，未创建成功测量文件。

部署接口依据当前仓库 [docs/erdma.md](../../docs/erdma.md)、ComputeNode 和 CM 后端源码。云平台设备与驱动要求须在部署当天根据厂商文档核对。

2026-10-09 已补充 [离线准备与验收工具](rdma-offline-tools.md)：本地预检、16 case 小型数据包、按角色恢复母本/运行与 RDMA 日志核验。独立审查四项校验问题已修复；本地 23/23 通过，RDMA 构建 23 通过、1 Skipped；修复后重跑记录见 [离线工具验证](../../artifacts/validation/offline-tools-2026-10-09.json)。这些继续保持无硬件结论。

## 1. 两端准备

计算端和存储端使用相同 Git 提交、Linux 小端 x86_64 ABI 和编译选项。记录 `git rev-parse HEAD`、`git status --short`、GCC/CMake 版本。复制查询 fvecs、数据 manifest、冻结配置与 workload；正式实验索引在存储端保留不可修改的母本，每轮从母本创建独立副本。

在 Ubuntu 上准备编译依赖；已安装云厂商软件栈时，开发包必须与其匹配：

```bash
sudo apt-get install build-essential cmake libibverbs-dev librdmacm-dev
cmake -S experiments -B experiments/build-rdma \
  -DENABLE_RDMA=ON -DENABLE_RDMA_CM=ON -DBUILD_TESTING=ON
cmake --build experiments/build-rdma -j4
ctest --test-dir experiments/build-rdma --output-on-failure
```

CMake 必须打印 `ibverbs transport enabled` 和 `librdmacm connection backend enabled`。缺少依赖时选项 ON 仍可能只编出 stub，不能仅凭命令退出码认为 RDMA 已编入。保留完整配置日志。没有设备时 CM 集成测试应为 Skipped，单独记录其他测试的通过数。

两端采集：

```bash
uname -a
ibv_devices
ibv_devinfo
ip -brief address
ulimit -l
# 替换为存储端实际 RDMA 网络 IPv4
ip route get "$STORAGE_RDMA_IP"
```

记录 provider/设备名/端口、网卡及固件、CPU/内存、网络 MTU、NUMA、驱动、memlock 与 CPU 绑定。需要的锁定内存由索引字节数、每会话快照和暂存缓冲共同决定；按实际设备策略设置，不在本地假定固定数值。

## 2. 连接方式与最小连通性

| 后端 | 两端参数 | 前提 |
| --- | --- | --- |
| 原有 IB | `--transport rdma --rdma-connection ib` | 有效 LID、手动 QP 路径 |
| CM/eRDMA | `--transport rdma --rdma-connection cm` | RDMA_CM provider 和按 IPv4 路由可达的 RDMA 网络 |

设备名参数只校验路由选出的设备，不负责改变 IP 路由。管理网 ping 通不足以证明 CM 能连通；当前接口采用 IPv4。

```bash
# 存储端
rping -s -a "$STORAGE_RDMA_IP" -p 18516
# 计算端
rping -c -a "$STORAGE_RDMA_IP" -p 18516 -C 10 -v
```

`rping` 包名和 provider 安装方法依平台而异。若失败，先检查路由、网卡/provider、ERI/访问规则和 memlock；保留错误日志。随后使用专门的可修改索引测试，不能对正式数据运行集成测试：

```bash
experiments/build-rdma/upstream/hnsw_build --generate 4096 --dim 32 \
  --M 16 --output data/rdma-integration.bin
# 存储端
experiments/build-rdma/upstream/storage_server \
  --index data/rdma-integration.bin --transport rdma --rdma-connection cm \
  --bind "$STORAGE_RDMA_IP" --port 18515 \
  --ib-device "$STORAGE_DEVICE" --ib-port 1 --rdma-timeout-ms 10000
# 计算端
RDMA_CM_TEST_HOST="$STORAGE_RDMA_IP" RDMA_CM_TEST_PORT=18515 \
RDMA_CM_TEST_DEVICE="$COMPUTE_DEVICE" \
  experiments/build-rdma/upstream/cm_integration_test
```

验收握手、分段 READ、错误更新后连接可用性、insert/erase/upsert、旧快照稳定、reload、重连及多会话访问。保存退出码和 stdout/stderr；硬件测试退出 77 是跳过，不是通过。

## 3. 三个真实 RDMA 客户端入口

`lab_client`、`lab_dynamic_client`、`lab_epoch_client` 已加入显式传输选择。默认 mmap 必须给 --index；RDMA 必须给 --transport rdma --host，且禁止 --index，不会回退本地索引。静态入口支持 ib/cm，动态与 epoch 限定 CM，因为需要会话新快照发布和 catalog 刷新。RDMA 默认选择 cm。

先在存储端从母本复制一份专门的可修改索引，启动服务。每种策略/每轮比较都重新恢复母本并重新启动会话，避免前一轮更新改变下一轮初始状态。

```bash
# 存储端；两个路径必须不同。不要在服务运行中改文件。
cp data/main_sift10000/index.bin data/rdma-run-copy.bin
experiments/build-rdma/upstream/storage_server \
  --index data/rdma-run-copy.bin --transport rdma --rdma-connection cm \
  --bind "$STORAGE_RDMA_IP" --port 18515 \
  --ib-device "$STORAGE_DEVICE" --ib-port 1 --rdma-timeout-ms 10000

# 计算端静态功能验证：先用 100 条查询，四策略分别执行。
experiments/build-rdma/lab_client \
  --transport rdma --rdma-connection cm --host "$STORAGE_RDMA_IP" --port 18515 \
  --ib-device "$COMPUTE_DEVICE" --ib-port 1 --rdma-timeout-ms 10000 \
  --queries data/main_sift10000/query.fvecs --max-queries 100 \
  --read-policy prefix --prefix-granularity 8 --cache-capacity 10000 \
  --ef-search 256 --k 10 --results results/rdma-smoke/prefix.csv \
  --trace results/rdma-smoke/prefix_trace.csv
```

将 read-policy 改为 fixed/header_actual/cached_length/prefix，使用同一服务器母本和查询。日志 transport=rdma、rdma_connection=cm；成功时由真实 RemoteTransport 读取当前服务端会话 MR，不读取本地 index.bin。连接/READ 失败退出非零；没有设备时不能产生成功测量。

现有 analyze.py 和 analyze_main_matrix.py 仍是本地日志分析器，会拒绝 RDMA 性能数据。新增 analyze_rdma.py 核验小型 RDMA 验收包三类日志、快照、计数和四策略结果；正式硬件矩阵的多轮聚合仍待真实日志就绪后实现。动态与 epoch 的 --verify 已直接通过远程传输取当前会话的节点、向量和邻接计算真值，不依赖服务端本地文件路径；核验读取在 query/update 计时与操作传输计数之外。

### 单会话更新

RDMA 更新走 `ComputeNode::update()`，其 mailbox WRITE、控制请求、服务端应用及新快照发布都属于更新链路。成功更新的会话会刷新 catalog；保留策略自有状态并更新 valid_node_count/trace snapshot。不要从客户端用 StorageEngine 直接修改服务端文件，也不要直接 RDMA WRITE 图快照。

先在小索引逐次 query/upsert/erase/insert；由存储端保存对应快照的有效节点、向量和邻接，用当前快照重算精确 truth。验证 retained hint 低估确实发生且只补剩余邻居。服务端磁盘提交、快照复制/MR 注册与客户端 catalog 刷新最好分别计时；如果 API 暂时只能给整体更新时间，字段必须标明包含范围。

```bash
# 在计算端生成小型 query/upsert workload。
experiments/build-rdma/lab_workload --out results/rdma-smoke/ops.csv \
  --operations 40 --update-ratio 0.3 --nodes 10000 --queries 1000 \
  --vectors 1000 --seed 42 --epoch-size 10 --distribution random

# 单会话串行功能核验；会改变服务端专用副本。
experiments/build-rdma/lab_dynamic_client \
  --transport rdma --host "$STORAGE_RDMA_IP" --port 18515 \
  --ib-device "$COMPUTE_DEVICE" --rdma-connection cm --rdma-timeout-ms 10000 \
  --queries data/main_sift10000/query.fvecs --vectors data/main_sift10000/query.fvecs \
  --workload results/rdma-smoke/ops.csv --read-policy prefix \
  --cache-mode warm --cache-capacity 10000 --prefix-granularity 8 \
  --ef-search 256 --k 10 --verify --results results/rdma-smoke/dynamic-prefix.csv
```

RDMA update 的 elapsed_ms 包含 submit/publish/catalog 刷新；reload_ms 在此入口只记录策略状态重新注入，日志 update_timing_scope 明确区分 mmap 的 apply+独立 reload。远程真值核验是重开销的功能验证，不作为性能排名。

### 跨会话 epoch

每个逻辑查询线程独立 ComputeNode/连接/策略状态。一个 epoch 的读线程全部结束后，由写会话提交指定更新，再逐读会话 reload，重新注入原策略和各自历史 hint，更新其快照号，然后开始下个读 epoch。每个查询及补读都应属于同一不可变快照。

现有会话不会自动看见其他会话提交的版本。把 mmap 的 epoch 调度迁入 RDMA 时，要在开始下一阶段前核对所有 reader 的 generation 一致。持续读写竞争是单独实验：同时记录每个读会话的版本/快照年龄，不把旧版本的稳定 hint 当成动态缓存收益。

```bash
# 先停止服务并恢复母本，重新启动；复用同一 workload。
experiments/build-rdma/lab_epoch_client \
  --transport rdma --host "$STORAGE_RDMA_IP" --port 18515 \
  --ib-device "$COMPUTE_DEVICE" --rdma-connection cm --rdma-timeout-ms 10000 \
  --queries data/main_sift10000/query.fvecs --vectors data/main_sift10000/query.fvecs \
  --workload results/rdma-smoke/ops.csv --read-policy prefix --threads 4 \
  --cache-mode warm --cache-capacity 10000 --prefix-granularity 8 \
  --ef-search 256 --k 10 --verify \
  --results results/rdma-smoke/epoch-prefix.csv --epochs results/rdma-smoke/epoch-prefix-stages.csv
```

实现为每 reader 一条连接，另设独立写会话。查询全部结束后写会话有序提交更新；读会话逐个重连取得最新快照并重新注入原 hint 状态。实际 RDMA 会话行为仍需要真实设备验收；当前通过编译、模拟传输与失败路径核验。实验期间不得有外部写者，否则 generation 连续性检查可能拒绝该轮结果。

## 4. 硬件测量字段与资源成本

沿用冻结矩阵的 dataset/index/workload/commit 哈希、M/ef/k/G/capacity/threads、预热和目标/实际更新比例。另加连接方式、IP/设备/端口、provider、memlock、试验时间、读会话版本及硬件参数。

| 字段 | 来源与范围 |
| --- | --- |
| 邻接逻辑 READ/请求/有效/浪费字节 | TraceStrategy/ReadMetrics 差分；READ 是策略调用 |
| 全数据 READ/WRITE ops/bytes | CSV transport_read_ops/bytes、transport_write_ops/bytes，取 RemoteTransport::stats() 的成功完成差分 |
| 控制 SEND/RECV | 当前 TransportStats 不包含；需单独插桩或记未采集 |
| READ/WRITE WR | CM 对大于 1MiB 的请求分段，逻辑调用与实际 WR 不一定相等 |
| query/update/reload | 分别计时，truth 和导出不在调用计时内 |
| QPS/混合吞吐 | 明确实测墙钟窗口；预热、正确性和失败样本独立记录 |
| 存储 MR/内存 | 每个会话快照有完整字节副本，更新时还可能短暂持有新旧副本 |
| Recall@10 | 静态核验 exact truth；动态核验对应会话快照的 truth |

每轮停止旧会话、恢复同一母本、重新启动服务，使用相同查询/预热/workload。不能在没有重置初始状态的情况下依次测四种策略。先单读会话，再四会话；检查存储 RAM 和锁定内存能容纳各会话快照，避免把内存不足当成算法退化。

## 5. 失败处理和补验收表

READ 超时/错误后结束该连接并重连；错误样本不得作为成功延迟。更新出现 publish/传输错误时，磁盘提交可能已经完成，先检查服务端最终状态，再决定恢复或重跑；不要盲目重复写入。异常定位至少保留 run_id、seq、session、snapshot、操作和两端日志。

| 项目 | 当前状态 | 设备可用后的证据 |
| --- | --- | --- |
| RDMA 构建 | 已启用 ibverbs+CM；当前 23 通过、1 Skipped | 仍需部署机器同版本构建 |
| 设备/provider/rping | 未执行 | 两端环境与连通日志 |
| CM 集成 | 无硬件，待执行 | 集成退出码、两端 stdout/stderr |
| 四策略静态一致性 | 接入口已实现；真实 RDMA 未执行 | 同索引逐查询结果与 recall |
| 单会话更新 hint 修复 | mmap 已验证；RDMA 未执行 | 新快照真值、自然 degree 变化和 fallback |
| 跨会话 epoch | mmap 已验证；RDMA 未执行 | 各 reader generation 和刷新成本 |
| 四策略正式硬件矩阵 | 未执行 | 真实原始日志、三轮统计、硬件参数 |

硬件验收待补不影响本地机制证据的保存；是否可按无设备阶段结题，需要指导教师确认。

RDMA epoch 无法从计算端得到服务端文件的物理/逻辑重写量，rewrite_measurement=not_collected，logical_index_rewrite_bytes=0 表示未采集。不能将其解释为服务端无写入。reload_transport_read_ops/bytes 为重新建立读会话后读取 catalog 的数据 READ；控制 SEND/RECV 未计入 TransportStats。
