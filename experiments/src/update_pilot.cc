#include "experiment_io.h"
#include "validated_vectors.h"
#include "rdma_hnsw/common/data.h"
#include "rdma_hnsw/runtime/compute_node.h"
#include <chrono>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <sys/resource.h>

int main(int argc, char **argv) {
  using namespace rdma_hnsw;
  try {
    std::map<std::string, std::string> args;
    const std::set<std::string> allowed{"--index", "--vectors", "--results", "--updates", "--seed"};
    for (int i = 1; i < argc; ++i) {
      const std::string key = argv[i];
      if (key == "--help") {
        std::cout << "lab_update_pilot --index COPY.bin --vectors FILE --results CSV "
                     "[--updates N] [--seed N]\nThe supplied index is updated in place.\n";
        return 0;
      }
      if (!allowed.contains(key) || i + 1 >= argc)
        throw std::invalid_argument("unknown option or missing value: " + key);
      args[key] = argv[++i];
    }
    for (const auto *key : {"--index", "--vectors", "--results"})
      if (!args.contains(key)) throw std::invalid_argument(std::string(key) + " is required");
    lab::check_outputs(args, {"--index", "--vectors"}, {"--results"});
    ComputeNodeConfig config;
    config.index_path = args.at("--index");
    ComputeNode node(config);
    StorageEngine storage(config.index_path);
    const auto &header = node.query_engine().catalog().header;
    if (header.node_count == 0) throw std::invalid_argument("cannot update an empty index");
    const auto vectors = lab::validated_vectors(args.at("--vectors"), header.dim);
    const auto updates = args.contains("--updates") ? lab::positive_number(args.at("--updates")) : 5;
    const auto seed = args.contains("--seed") ? lab::positive_number(args.at("--seed")) : 42;
    std::mt19937_64 generator(seed);
    std::uniform_int_distribution<NodeId> pick(0, header.node_count - 1);
    auto output = lab::output_file(args.at("--results"));
    output << "update_id,node_id,generation,update_ms,reload_ms,peak_rss_kib\n";
    for (std::size_t i = 0; i < updates; ++i) {
      const auto id = pick(generator);
      const auto start = std::chrono::steady_clock::now();
      const auto result = storage.apply({update_op::Upsert, id, vectors[i % vectors.size()]});
      const auto updated = std::chrono::steady_clock::now();
      if (!result.ok) throw std::runtime_error(result.error);
      node.reload();
      const auto reloaded = std::chrono::steady_clock::now();
      if (node.query_engine().catalog().header.generation != result.generation)
        throw std::runtime_error("reload did not observe the published snapshot");
      struct rusage usage{};
      if (getrusage(RUSAGE_SELF, &usage) != 0) throw std::runtime_error("getrusage failed");
      output << i << ',' << id << ',' << result.generation << ','
             << std::chrono::duration<double, std::milli>(updated - start).count() << ','
             << std::chrono::duration<double, std::milli>(reloaded - updated).count() << ','
             << usage.ru_maxrss << '\n';
    }
    output.flush();
    if (!output) throw std::runtime_error("update log write failed");
    std::cout << "completed_updates=" << updates << " seed=" << seed << '\n';
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "lab_update_pilot: " << error.what() << '\n';
    return 1;
  }
}
