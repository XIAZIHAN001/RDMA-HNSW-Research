#include "experiment_io.h"
#include "validated_vectors.h"
#include "trace_io.h"
#include "read_strategies.h"
#include "workload.h"
#include "transport_options.h"
#include "remote_snapshot.h"
#include "rdma_hnsw/runtime/compute_node.h"
#include <algorithm>
#include <chrono>
#include <iostream>
#include <map>
#include <set>

using namespace rdma_hnsw;
namespace {
double milliseconds(std::chrono::steady_clock::time_point start) {
  return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
}
double exact_recall(const Vector &query, const std::vector<NodeRecord> &snapshot,
                   const std::vector<NodeId> &ids, std::size_t k) {
  std::vector<std::pair<double, NodeId>> distances;
  for (const auto &node : snapshot) if (!node.deleted) {
    double distance = 0;
    for (std::size_t i = 0; i < query.size(); ++i) {
      const double delta = static_cast<double>(query[i]) - node.vector[i];
      distance += delta * delta;
    }
    distances.emplace_back(distance, node.id);
  }
  const auto count = std::min(k, distances.size());
  if (count == 0) return ids.empty() ? 1 : 0;
  std::partial_sort(distances.begin(), distances.begin() + count, distances.end());
  std::set<NodeId> predicted(ids.begin(), ids.end());
  std::size_t matches = 0;
  for (std::size_t i = 0; i < count; ++i) matches += predicted.contains(distances[i].second);
  return static_cast<double>(matches) / count;
}
std::vector<NodeRecord> snapshot(ComputeNode &node) {
  return node.config().transport==TransportKind::Mmap?GraphStore::read_all(node.query_engine().catalog()):
      lab::remote_snapshot(node.query_engine().catalog(),node.transport());
}
}
int main(int argc, char **argv) {
 try {
  std::map<std::string,std::string> args;
  bool verify = false;
  const std::set<std::string> allowed{"--index","--queries","--vectors","--workload","--results","--trace",
    "--read-policy","--cache-capacity","--prefix-granularity","--cache-mode","--ef-search","--k",
    "--transport","--host","--port","--ib-device","--ib-port","--rdma-connection","--rdma-timeout-ms"};
  for (int i=1;i<argc;++i) {
    const std::string key=argv[i];
    if (key=="--help") {
      std::cout << "lab_dynamic_client (--index COPY.bin | --transport rdma --host IPv4) --queries FILE --vectors FILE --workload CSV --results CSV "
                   "[--trace CSV] [--verify] [--read-policy fixed|header_actual|cached_length|prefix] "
                   "[--cache-mode warm|cold] [--cache-capacity N] [--ef-search N] [--k N]\n"
                   "[--rdma-connection cm] [--port N] [--ib-device NAME] [--ib-port N] [--rdma-timeout-ms N]\n"
                   "Serial updates: mmap applies/reloads; RDMA CM submits to server and refreshes catalog.\n";
      return 0;
    }
    if (key=="--verify") { verify=true;continue; }
    if (!allowed.contains(key) || i+1>=argc) throw std::invalid_argument("unknown option or missing value: "+key);
    args[key]=argv[++i];
  }
  for (const auto *key : {"--queries","--vectors","--workload","--results"})
    if (!args.contains(key)) throw std::invalid_argument(std::string(key)+" is required");
  lab::check_outputs(args,{"--index","--queries","--vectors","--workload"},{"--results","--trace"});
  const auto policy_name=args.contains("--read-policy") ? args.at("--read-policy") : "cached_length";
  ReadPolicy policy;
  if (policy_name=="fixed") policy=ReadPolicy::Fixed;
  else if (policy_name=="header_actual") policy=ReadPolicy::HeaderActual;
  else if (policy_name=="cached_length") policy=ReadPolicy::CachedLength;
  else if (policy_name=="prefix") policy=ReadPolicy::Prefix;
  else throw std::invalid_argument("unsupported read policy");
  const auto mode=args.contains("--cache-mode") ? args.at("--cache-mode") : "warm";
  if (mode!="warm" && mode!="cold") throw std::invalid_argument("invalid cache-mode");
  ComputeNodeConfig config;
  lab::configure_transport(args,config);
  if(config.transport==TransportKind::Rdma&&config.rdma_connection!=RdmaConnection::Cm)
    throw std::invalid_argument("dynamic RDMA requires CM snapshot publication");
  config.ef_search=args.contains("--ef-search") ? lab::positive_number(args.at("--ef-search")) : 256;
  config.k=args.contains("--k") ? lab::positive_number(args.at("--k")) : 10;
  config.cache_capacity=args.contains("--cache-capacity") ? lab::nonnegative_number(args.at("--cache-capacity")) : 10000;
  config.prefix_granularity=args.contains("--prefix-granularity") ? lab::positive_number(args.at("--prefix-granularity")) : 8;
  if (config.ef_search<config.k) throw std::invalid_argument("ef-search must be at least k");
  ComputeNode node(config);
  std::unique_ptr<StorageEngine> storage;
  if(config.transport==TransportKind::Mmap)storage=std::make_unique<StorageEngine>(config.index_path);
  const auto queries=lab::validated_vectors(args.at("--queries"),node.query_engine().catalog().header.dim);
  const auto vectors=lab::validated_vectors(args.at("--vectors"),node.query_engine().catalog().header.dim);
  const auto operations=lab::load_workload(args.at("--workload"));
  lab::validate_workload(operations,node.query_engine().catalog().header.node_count,queries.size(),vectors.size());
  auto state=std::make_shared<lab::StrategyState>();
  auto trace=std::make_shared<lab::TraceState>();
  trace->capture=args.contains("--trace");
  const auto inject=[&] {
    state->valid_node_count=node.query_engine().catalog().header.node_count;
    trace->snapshot_id=node.query_engine().catalog().header.generation;
    node.query_engine().set_read_strategy(std::make_unique<lab::TraceStrategy>(lab::make_lab_strategy(policy,state),trace));
  };
  inject();
  auto output=lab::output_file(args.at("--results"));
  output << "seq,type,vector_ref,node_id,epoch,snapshot_before,snapshot_id,node_count,elapsed_ms,reload_ms,result_ids,"
            "verified_recall,degree_changes,neighbor_id_changes,neighbor_reads,neighbor_requested_bytes,"
            "neighbor_useful_bytes,neighbor_wasted_bytes,neighbor_dependent_reads,neighbor_header_reads,neighbor_accesses,"
            "cache_hits,cache_misses,cache_evictions,fallbacks,cold_miss_fallbacks,hint_underestimates,"
            "capacity_clamps,read_ops,requested_bytes,policy,cache_mode,cache_capacity,prefix_granularity,"
            "ef_search,k,transport,timing_mode,status,rdma_connection,update_timing_scope,"
            "transport_read_ops,transport_read_bytes,transport_write_ops,transport_write_bytes\n";
  std::ofstream trace_output;
  if (trace->capture) { trace_output=lab::output_file(args.at("--trace"));lab::trace_header(trace_output); }
  auto current=verify ? snapshot(node) : std::vector<NodeRecord>{};
  std::size_t updates=0, query_count=0;
  for (const auto &op:operations) {
    const auto before_generation=node.query_engine().catalog().header.generation;
    std::vector<NodeId> ids;
    double elapsed=0,reload=0,recall=-1;
    std::size_t changed_degrees=0,changed_ids=0;
    trace->query_id=op.seq;trace->reads={};trace->rows.clear();trace->accesses=0;
    trace->cold_miss_fallbacks=0;trace->hint_underestimates=0;
    const auto before_stats=node.query_engine().stats();
    const auto transport_before=lab::transport_counts(node.transport());lab::TransportCounts transport_delta{};
    if (op.type=="query") {
      const auto start=std::chrono::steady_clock::now();
      ids=node.query(queries[op.vector_ref]);elapsed=milliseconds(start);++query_count;
      transport_delta=lab::transport_delta(lab::transport_counts(node.transport()),transport_before);
      if (verify) recall=exact_recall(queries[op.vector_ref],current,ids,config.k);
    } else {
      const auto update_type=op.type=="insert" ? update_op::Insert : op.type=="erase" ? update_op::Erase : update_op::Upsert;
      const auto start=std::chrono::steady_clock::now();
      const UpdateRequest request{update_type,op.node_id,op.type=="erase"?Vector{}:vectors[op.vector_ref]};
      const auto result=storage?storage->apply(request):node.update(request);
      elapsed=milliseconds(start);
      if (!result.ok) throw std::runtime_error(result.error);
      if(!storage)transport_delta=lab::transport_delta(lab::transport_counts(node.transport()),transport_before);
      const auto refresh_start=std::chrono::steady_clock::now();
      if(storage)node.reload();
      if (mode=="cold") state=std::make_shared<lab::StrategyState>();
      inject();reload=milliseconds(refresh_start);
      if (trace->snapshot_id!=result.generation) throw std::runtime_error("reload snapshot mismatch");
      ++updates;
      if (verify) {
        auto after=snapshot(node);
        for (std::size_t i=0;i<std::min(current.size(),after.size());++i)
          for (std::size_t l=0;l<std::min(current[i].neighbors.size(),after[i].neighbors.size());++l) {
            changed_degrees+=current[i].neighbors[l].size()!=after[i].neighbors[l].size();
            changed_ids+=current[i].neighbors[l]!=after[i].neighbors[l];
          }
        current=std::move(after);
      }
    }
    if (trace->reads.capacity_clamps) throw std::runtime_error("degree exceeds capacity");
    const auto after_stats=node.query_engine().stats();
    const auto &m=trace->reads;
    output << op.seq << ',' << op.type << ',' << op.vector_ref << ',' << op.node_id << ',' << op.epoch << ','
           << before_generation << ',' << trace->snapshot_id << ',' << state->valid_node_count << ',' << elapsed << ','
           << reload << ',';
    for (std::size_t j=0;j<ids.size();++j) { if(j) output << ';';output << ids[j]; }
    output << ',' << recall << ',' << changed_degrees << ',' << changed_ids << ',' << m.read_ops << ','
           << m.requested_bytes << ',' << m.useful_bytes << ',' << m.wasted_bytes << ',' << m.dependent_reads << ','
           << m.header_reads << ',' << trace->accesses << ',' << m.cache_hits << ',' << m.cache_misses << ','
           << m.cache_invalidations << ',' << m.fallbacks << ',' << trace->cold_miss_fallbacks << ','
           << trace->hint_underestimates << ',' << m.capacity_clamps << ',';
    if (op.type=="query") output << after_stats.reads.read_ops-before_stats.reads.read_ops << ','
                                 << after_stats.reads.requested_bytes-before_stats.reads.requested_bytes;
    else output << "0,0";
    output << ',' << policy_name << ',' << mode << ',' << config.cache_capacity << ',' << config.prefix_granularity << ','
           << config.ef_search << ',' << config.k << ',' << lab::transport_name(config) << ',' << (verify ? "correctness_instrumented" : trace->capture ? "trace_instrumented" : "operation_only")
           << ",ok," << lab::connection_name(config) << ',' << (storage?"local_apply_then_separate_reload":"remote_submit_publish_catalog_then_reinject");
    lab::write_transport_counts(output,transport_delta);output << '\n';
    if (trace->capture) lab::trace_rows(trace_output,*trace,policy_name.c_str(),"dynamic");
  }
  output.flush();if(trace_output.is_open()) trace_output.flush();
  if (!output || (trace_output.is_open()&&!trace_output)) throw std::runtime_error("dynamic output write failed");
  std::cout << "completed_queries=" << query_count << " completed_updates=" << updates
            << " actual_update_ratio=" << static_cast<double>(updates)/operations.size() << '\n';
  return 0;
 } catch (const std::exception &error) { std::cerr << "lab_dynamic_client: " << error.what() << '\n';return 1; }
}
