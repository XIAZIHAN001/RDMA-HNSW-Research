#include "rdma_hnsw/common/data.h"
#include "rdma_hnsw/runtime/compute_node.h"
#include "trace_io.h"
#include "experiment_io.h"
#include "validated_vectors.h"
#include "read_strategies.h"
#include "transport_options.h"
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>

int main(int argc, char **argv) {
  using namespace rdma_hnsw;
  using lab::positive_number;
  try {
    std::map<std::string, std::string> args;
    const std::set<std::string> allowed{
      "--index", "--queries", "--results", "--trace", "--ef-search", "--k", "--max-queries", "--read-policy", "--cache-capacity", "--prefix-granularity",
      "--transport","--host","--port","--ib-device","--ib-port","--rdma-connection","--rdma-timeout-ms"};
    for (int i = 1; i < argc; ++i) {
      const std::string key = argv[i];
      if (key == "--help") {
        std::cout << "lab_client (--index FILE | --transport rdma --host IPv4) --queries FILE --results CSV "
                     "[--trace CSV] [--read-policy fixed|header_actual|cached_length|prefix] [--cache-capacity N] "
                     "[--ef-search N] [--k N] [--max-queries N] [--rdma-connection cm|ib] "
                     "[--port N] [--ib-device NAME] [--ib-port N] [--rdma-timeout-ms N]\n";
        return 0;
      }
      if (!allowed.contains(key) || i + 1 >= argc)
        throw std::invalid_argument("unknown option or missing value: " + key);
      args[key] = argv[++i];
    }
    for (const auto *key : {"--queries", "--results"})
      if (!args.contains(key))
        throw std::invalid_argument(std::string(key) + " is required");

    lab::check_outputs(args, {"--index", "--queries"}, {"--results", "--trace"});
    const auto policy_name = args.contains("--read-policy") ? args.at("--read-policy") : "fixed";
    ReadPolicy policy;
    if (policy_name == "fixed") policy = ReadPolicy::Fixed;
    else if (policy_name == "header_actual") policy = ReadPolicy::HeaderActual;
    else if (policy_name == "cached_length") policy = ReadPolicy::CachedLength;
    else if (policy_name == "prefix") policy = ReadPolicy::Prefix;
    else throw std::invalid_argument("unsupported read policy: " + policy_name);

    ComputeNodeConfig config;
    lab::configure_transport(args,config);
    config.ef_search = args.contains("--ef-search") ? positive_number(args.at("--ef-search")) : 64;
    config.k = args.contains("--k") ? positive_number(args.at("--k")) : 10;
    if (config.ef_search < config.k)
      throw std::invalid_argument("ef-search must be at least k");
    config.cache_capacity = args.contains("--cache-capacity") ? lab::nonnegative_number(args.at("--cache-capacity")) : 10000;
    config.prefix_granularity = args.contains("--prefix-granularity") ? positive_number(args.at("--prefix-granularity")) : 8;
    ComputeNode node(config);
    const auto queries = lab::validated_vectors(args.at("--queries"),
                                                node.query_engine().catalog().header.dim);
    const auto count = args.contains("--max-queries")
        ? std::min(positive_number(args.at("--max-queries")), queries.size()) : queries.size();
    auto state = std::make_shared<lab::TraceState>();
    state->capture = args.contains("--trace");
    state->snapshot_id = node.query_engine().catalog().header.generation;
    auto strategy_state = std::make_shared<lab::StrategyState>();
    strategy_state->valid_node_count = node.query_engine().catalog().header.node_count;
    node.query_engine().set_read_strategy(std::make_unique<lab::TraceStrategy>(
        lab::make_lab_strategy(policy, strategy_state), state));
    auto results = lab::output_file(args.at("--results"));
    results << "query_id,elapsed_ms,neighbor_reads,neighbor_requested_bytes,"
               "neighbor_useful_bytes,neighbor_wasted_bytes,visited_nodes,"
               "read_ops,requested_bytes,result_ids,timing_mode,snapshot_id,status,"
               "policy,ef_search,k,transport,neighbor_dependent_reads,neighbor_header_reads,"
               "capacity_clamps,neighbor_accesses,cache_hits,cache_misses,cache_evictions,fallbacks,"
               "cold_miss_fallbacks,hint_underestimates,cache_capacity,prefix_granularity,rdma_connection,"
               "transport_read_ops,transport_read_bytes,transport_write_ops,transport_write_bytes\n";
    std::ofstream trace;
    if (state->capture) {
      trace = lab::output_file(args.at("--trace"));
      lab::trace_header(trace);
    }
    double query_ms = 0;
    for (std::size_t i = 0; i < count; ++i) {
      state->query_id = i;
      state->reads = {};
      state->accesses = 0;
      state->cold_miss_fallbacks = 0; state->hint_underestimates = 0;
      state->rows.clear();
      const auto before = node.query_engine().stats();
      const auto transport_before=lab::transport_counts(node.transport());
      const auto start = std::chrono::steady_clock::now();
      const auto ids = node.query(queries[i]);
      const auto elapsed = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - start).count();
      query_ms += elapsed;
      const auto after = node.query_engine().stats();
      const auto transport_delta=lab::transport_delta(lab::transport_counts(node.transport()),transport_before);
      if (state->reads.capacity_clamps)
        throw std::runtime_error("degree exceeds capacity; refusing a corrupted-index measurement");
      results << i << ',' << elapsed << ',' << state->reads.read_ops << ','
              << state->reads.requested_bytes << ',' << state->reads.useful_bytes << ','
              << state->reads.wasted_bytes << ',' << after.visited_nodes - before.visited_nodes
              << ',' << after.reads.read_ops - before.reads.read_ops << ','
              << after.reads.requested_bytes - before.reads.requested_bytes << ',';
      for (std::size_t j = 0; j < ids.size(); ++j) {
        if (j) results << ';';
        results << ids[j];
      }
      results << ',' << (state->capture ? "trace_instrumented" : "query_only") << ','
              << state->snapshot_id << ",ok," << policy_name << ',' << config.ef_search << ','
              << config.k << ',' << lab::transport_name(config) << ',' << state->reads.dependent_reads << ','
              << state->reads.header_reads << ',' << state->reads.capacity_clamps << ','
              << state->accesses << ',' << state->reads.cache_hits << ',' << state->reads.cache_misses << ','
              << state->reads.cache_invalidations << ',' << state->reads.fallbacks << ','
              << state->cold_miss_fallbacks << ',' << state->hint_underestimates << ','
              << config.cache_capacity << ',' << config.prefix_granularity << ',' << lab::connection_name(config);
      lab::write_transport_counts(results,transport_delta);results << '\n';
      if (state->capture) lab::trace_rows(trace, *state, policy_name.c_str(), "trace");
    }
    results.flush();
    if (trace.is_open()) trace.flush();
    if (!results || (trace.is_open() && !trace))
      throw std::runtime_error("measurement output write failed");
    std::cout << "completed_queries=" << count << " query_elapsed_ms=" << query_ms
              << " policy=" << policy_name
              << " timing_mode=" << (state->capture ? "trace_instrumented" : "query_only")
              << '\n';
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "lab_client: " << error.what() << '\n';
    return 1;
  }
}
