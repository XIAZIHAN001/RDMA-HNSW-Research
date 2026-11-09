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
#include <exception>
#include <future>
#include <iostream>
#include <map>
#include <set>

using namespace rdma_hnsw;
namespace {
using Clock=std::chrono::steady_clock;
double ms(Clock::time_point start){return std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
struct Measurement {
  Generation snapshot{};std::size_t thread{},node_count{};
  double elapsed{},recall{-1};ReadMetrics reads,all_reads;
  lab::TransportCounts transport_reads{};
  std::size_t accesses{},cold{},under{};
  std::vector<NodeId> ids;std::vector<lab::TraceRow> trace;
};
struct Reader {
  std::unique_ptr<ComputeNode> node;
  std::shared_ptr<lab::StrategyState> state=std::make_shared<lab::StrategyState>();
  std::shared_ptr<lab::TraceState> trace=std::make_shared<lab::TraceState>();
};
void inject(Reader &r,ReadPolicy policy){
 r.state->valid_node_count=r.node->query_engine().catalog().header.node_count;
 r.trace->snapshot_id=r.node->query_engine().catalog().header.generation;
 r.node->query_engine().set_read_strategy(std::make_unique<lab::TraceStrategy>(lab::make_lab_strategy(policy,r.state),r.trace));
}
Measurement query(Reader &r,const Vector &vector,std::size_t id){
 auto &t=*r.trace;t.query_id=id;t.reads={};t.rows.clear();t.accesses=0;t.cold_miss_fallbacks=0;t.hint_underestimates=0;
 const auto before=r.node->query_engine().stats().reads;
 const auto transport_before=lab::transport_counts(r.node->transport());
 const auto start=Clock::now();auto ids=r.node->query(vector);const double elapsed=ms(start);
 const auto after=r.node->query_engine().stats().reads;
 if(t.reads.capacity_clamps)throw std::runtime_error("degree exceeds capacity");
 Measurement result;result.snapshot=t.snapshot_id;result.thread=t.thread_id;result.node_count=r.state->valid_node_count;
 result.elapsed=elapsed;result.reads=t.reads;result.accesses=t.accesses;result.cold=t.cold_miss_fallbacks;result.under=t.hint_underestimates;
 result.all_reads.read_ops=after.read_ops-before.read_ops;result.all_reads.requested_bytes=after.requested_bytes-before.requested_bytes;
 result.transport_reads=lab::transport_delta(lab::transport_counts(r.node->transport()),transport_before);
 result.ids=std::move(ids);result.trace=std::move(t.rows);return result;
}
double recall(const Vector &query,const std::vector<NodeRecord> &nodes,const std::vector<NodeId> &ids,std::size_t k){
 std::vector<std::pair<double,NodeId>> distances;distances.reserve(nodes.size());
 for(const auto &node:nodes)if(!node.deleted){double d=0;for(std::size_t i=0;i<query.size();++i){const double x=static_cast<double>(query[i])-node.vector[i];d+=x*x;}distances.emplace_back(d,node.id);}
 const auto count=std::min(k,distances.size());if(!count)return ids.empty()?1:0;
 std::partial_sort(distances.begin(),distances.begin()+count,distances.end());std::set<NodeId> actual(ids.begin(),ids.end());
 std::size_t hits=0;for(std::size_t i=0;i<count;++i)hits+=actual.contains(distances[i].second);return static_cast<double>(hits)/count;
}
}
int main(int argc,char **argv){
 try {
  std::map<std::string,std::string> args;bool verify=false;
  const std::set<std::string> allowed{"--index","--queries","--vectors","--workload","--results","--epochs","--trace","--threads","--warmup","--verify-samples","--read-policy","--cache-capacity","--prefix-granularity","--cache-mode","--ef-search","--k",
    "--transport","--host","--port","--ib-device","--ib-port","--rdma-connection","--rdma-timeout-ms"};
  for(int i=1;i<argc;++i){const std::string key=argv[i];
   if(key=="--help"){std::cout << "lab_epoch_client (--index COPY.bin | --transport rdma --host IPv4) --queries FILE --vectors FILE --workload CSV --results CSV --epochs CSV [--threads N] [--warmup N] [--verify | --verify-samples N] [--trace CSV] [--read-policy fixed|header_actual|cached_length|prefix] [--cache-mode warm|cold] [--rdma-connection cm] [--port N] [--ib-device NAME] [--ib-port N] [--rdma-timeout-ms N]\nEpoch semantics: all queries read one snapshot, then ordered updates commit, then readers reload.\n";return 0;}
   if(key=="--verify"){verify=true;continue;}
   if(!allowed.contains(key)||i+1>=argc)throw std::invalid_argument("unknown option or missing value: "+key);
   args[key]=argv[++i];
  }
  for(const auto *key:{"--queries","--vectors","--workload","--results","--epochs"})if(!args.contains(key))throw std::invalid_argument(std::string(key)+" is required");
  lab::check_outputs(args,{"--index","--queries","--vectors","--workload"},{"--results","--epochs","--trace"});
  const auto policy_name=args.contains("--read-policy")?args.at("--read-policy"):"prefix";
  ReadPolicy policy;if(policy_name=="fixed")policy=ReadPolicy::Fixed;else if(policy_name=="header_actual")policy=ReadPolicy::HeaderActual;
  else if(policy_name=="cached_length")policy=ReadPolicy::CachedLength;else if(policy_name=="prefix")policy=ReadPolicy::Prefix;else throw std::invalid_argument("unsupported read policy");
  const auto mode=args.contains("--cache-mode")?args.at("--cache-mode"):"warm";if(mode!="warm"&&mode!="cold")throw std::invalid_argument("invalid cache-mode");
  const auto thread_count=args.contains("--threads")?lab::positive_number(args.at("--threads")):1;
  if(thread_count>64)throw std::invalid_argument("threads exceeds experiment limit 64");
  const auto warmup=args.contains("--warmup")?lab::nonnegative_number(args.at("--warmup")):0;
  const auto samples=args.contains("--verify-samples")?lab::nonnegative_number(args.at("--verify-samples")):0;
  ComputeNodeConfig config;lab::configure_transport(args,config);
  if(config.transport==TransportKind::Rdma&&config.rdma_connection!=RdmaConnection::Cm)throw std::invalid_argument("epoch RDMA requires CM snapshot publication");
  config.ef_search=args.contains("--ef-search")?lab::positive_number(args.at("--ef-search")):256;
  config.k=args.contains("--k")?lab::positive_number(args.at("--k")):10;
  config.cache_capacity=args.contains("--cache-capacity")?lab::nonnegative_number(args.at("--cache-capacity")):10000;
  config.prefix_granularity=args.contains("--prefix-granularity")?lab::positive_number(args.at("--prefix-granularity")):8;
  if(config.ef_search<config.k)throw std::invalid_argument("ef-search must be at least k");
  std::vector<Reader> readers(thread_count);
  for(std::size_t i=0;i<thread_count;++i){auto &r=readers[i];r.node=std::make_unique<ComputeNode>(config);r.trace->thread_id=i;r.trace->capture=args.contains("--trace");inject(r,policy);}
  const auto queries=lab::validated_vectors(args.at("--queries"),readers[0].node->query_engine().catalog().header.dim);
  const auto vectors=lab::validated_vectors(args.at("--vectors"),readers[0].node->query_engine().catalog().header.dim);
  const auto operations=lab::load_workload(args.at("--workload"));lab::validate_workload(operations,readers[0].state->valid_node_count,queries.size(),vectors.size());
  std::unique_ptr<StorageEngine> storage;std::unique_ptr<ComputeNode> writer;
  if(config.transport==TransportKind::Mmap)storage=std::make_unique<StorageEngine>(config.index_path);
  else if(std::any_of(operations.begin(),operations.end(),[](const auto &op){return op.type!="query";}))writer=std::make_unique<ComputeNode>(config);
  auto out=lab::output_file(args.at("--results"));auto epochs=lab::output_file(args.at("--epochs"));std::ofstream trace_out;
  if(args.contains("--trace")){trace_out=lab::output_file(args.at("--trace"));lab::trace_header(trace_out);}
  out << "seq,type,vector_ref,node_id,epoch,thread_id,snapshot_id,node_count,elapsed_ms,result_ids,verified_recall,neighbor_reads,neighbor_requested_bytes,neighbor_useful_bytes,neighbor_wasted_bytes,neighbor_dependent_reads,neighbor_header_reads,neighbor_accesses,cache_hits,cache_misses,cache_evictions,fallbacks,cold_miss_fallbacks,hint_underestimates,capacity_clamps,read_ops,requested_bytes,logical_index_rewrite_bytes,policy,cache_mode,cache_capacity,prefix_granularity,threads,warmup,ef_search,k,transport,timing_mode,status,rdma_connection,update_timing_scope,rewrite_measurement,transport_read_ops,transport_read_bytes,transport_write_ops,transport_write_bytes\n";
  epochs << "epoch,snapshot_before,snapshot_after,query_count,update_count,query_phase_ms,update_phase_ms,reload_phase_ms,verification_ms,initial_warmup_ms,logical_index_rewrite_bytes,rewrite_measurement,reload_transport_read_ops,reload_transport_read_bytes\n";
  const auto warm_start=Clock::now();
  for(auto &r:readers)for(std::size_t i=0;i<warmup;++i){auto measured=query(r,queries[i%queries.size()],i);if(trace_out.is_open()){r.trace->rows=std::move(measured.trace);lab::trace_rows(trace_out,*r.trace,policy_name.c_str(),"warmup");}}
  const double warm_ms=ms(warm_start);
  std::vector<Measurement> measurements(operations.size());std::size_t total_queries=0,total_updates=0;
  for(std::size_t begin=0;begin<operations.size();){std::size_t end=begin+1;while(end<operations.size()&&operations[end].epoch==operations[begin].epoch)++end;
   const auto generation=readers[0].trace->snapshot_id;
   std::vector<std::vector<const lab::Operation*>> jobs(thread_count);std::vector<const lab::Operation*> updates;std::size_t query_count=0;
   for(std::size_t i=begin;i<end;++i){const auto &op=operations[i];if(op.type=="query")jobs[query_count++%thread_count].push_back(&op);else updates.push_back(&op);}
   const auto read_start=Clock::now();std::vector<std::future<void>> futures;
   for(std::size_t t=0;t<thread_count;++t)futures.push_back(std::async(std::launch::async,[&,t]{for(const auto *op:jobs[t])measurements[op->seq]=query(readers[t],queries[op->vector_ref],op->seq);}));
   for(auto &future:futures)future.get();
   const double read_ms=ms(read_start);
   // All workers have joined; no writes or reloads can overlap a query/fallback.
   const auto verify_start=Clock::now();
   if(verify||samples){const auto nodes=config.transport==TransportKind::Mmap?GraphStore::read_all(readers[0].node->query_engine().catalog()):lab::remote_snapshot(readers[0].node->query_engine().catalog(),readers[0].node->transport());
    for(const auto &job:jobs){std::size_t checked=0;for(const auto *op:job){if(!verify&&checked>=samples)break;auto &m=measurements[op->seq];m.recall=recall(queries[op->vector_ref],nodes,m.ids,config.k);++checked;}}}
   const double verify_ms=ms(verify_start);double update_ms=0,reload_ms=0;std::uint64_t rewritten=0;
   // Log query results before updates: catalog and the separately computed truth refer to this epoch.
   const auto write=[&](const lab::Operation &op,const Measurement &m,std::uint64_t bytes){const auto &r=m.reads;
    out << op.seq << ',' << op.type << ',' << op.vector_ref << ',' << op.node_id << ',' << op.epoch << ',' << m.thread << ',' << m.snapshot << ',' << m.node_count << ',' << m.elapsed << ',';
    for(std::size_t i=0;i<m.ids.size();++i){if(i)out << ';';out << m.ids[i];}
    out << ',' << m.recall << ',' << r.read_ops << ',' << r.requested_bytes << ',' << r.useful_bytes << ',' << r.wasted_bytes << ',' << r.dependent_reads << ',' << r.header_reads << ',' << m.accesses << ',' << r.cache_hits << ',' << r.cache_misses << ',' << r.cache_invalidations << ',' << r.fallbacks << ',' << m.cold << ',' << m.under << ',' << r.capacity_clamps << ',' << m.all_reads.read_ops << ',' << m.all_reads.requested_bytes << ',' << bytes << ',' << policy_name << ',' << mode << ',' << config.cache_capacity << ',' << config.prefix_granularity << ',' << thread_count << ',' << warmup << ',' << config.ef_search << ',' << config.k << ',' << lab::transport_name(config) << ',' << (trace_out.is_open()?"trace_instrumented":"epoch_operation_only") << ",ok," << lab::connection_name(config) << ',' << (storage?"local_apply":"remote_submit_publish_catalog") << ',' << (storage?"local_file_size_sum":"not_collected");
    lab::write_transport_counts(out,m.transport_reads);out << '\n';
   };
   for(std::size_t i=begin;i<end;++i)if(operations[i].type=="query")write(operations[i],measurements[i],0);
   if(trace_out.is_open())for(auto &r:readers)for(const auto *op:jobs[r.trace->thread_id]){r.trace->rows=std::move(measurements[op->seq].trace);r.trace->query_id=op->seq;lab::trace_rows(trace_out,*r.trace,policy_name.c_str(),"measured");}
   for(const auto *op:updates){const auto start=Clock::now();const auto type=op->type=="insert"?update_op::Insert:op->type=="erase"?update_op::Erase:update_op::Upsert;
    const UpdateRequest request{type,op->node_id,op->type=="erase"?Vector{}:vectors[op->vector_ref]};
    const auto before=writer?lab::transport_counts(writer->transport()):lab::TransportCounts{};
    const auto result=storage?storage->apply(request):writer->update(request);const double elapsed=ms(start);if(!result.ok)throw std::runtime_error(result.error);
    const auto bytes=storage?std::filesystem::file_size(config.index_path):0;rewritten+=bytes;update_ms+=elapsed;
    Measurement m;m.snapshot=result.generation;m.node_count=storage?GraphStore::load_catalog(config.index_path).header.node_count:writer->query_engine().catalog().header.node_count;m.elapsed=elapsed;
    if(writer)m.transport_reads=lab::transport_delta(lab::transport_counts(writer->transport()),before);
    write(*op,m,bytes);
   }
   lab::TransportCounts reload_counts{};
   if(!updates.empty()){const auto start=Clock::now();for(auto &r:readers){r.node->reload();const auto counts=lab::transport_counts(r.node->transport());for(std::size_t i=0;i<counts.size();++i)reload_counts[i]+=counts[i];if(mode=="cold")r.state=std::make_shared<lab::StrategyState>();inject(r,policy);}reload_ms=ms(start);}
   const auto after=readers[0].trace->snapshot_id;for(const auto &r:readers)if(r.trace->snapshot_id!=after)throw std::runtime_error("readers have different snapshots");
   if(after!=generation+updates.size())throw std::runtime_error("epoch snapshot/update count mismatch");
   epochs << operations[begin].epoch << ',' << generation << ',' << after << ',' << query_count << ',' << updates.size() << ',' << read_ms << ',' << update_ms << ',' << reload_ms << ',' << verify_ms << ',' << (begin==0?warm_ms:0) << ',' << rewritten << ',' << (storage?"local_file_size_sum":"not_collected") << ',' << reload_counts[0] << ',' << reload_counts[1] << '\n';
   total_queries+=query_count;total_updates+=updates.size();begin=end;
  }
  out.flush();epochs.flush();if(trace_out.is_open())trace_out.flush();if(!out||!epochs||(trace_out.is_open()&&!trace_out))throw std::runtime_error("epoch output write failed");
  std::cout << "completed_queries=" << total_queries << " completed_updates=" << total_updates << " actual_update_ratio=" << static_cast<double>(total_updates)/operations.size() << " threads=" << thread_count << '\n';return 0;
 }catch(const std::exception &error){std::cerr << "lab_epoch_client: " << error.what() << '\n';return 1;}
}
