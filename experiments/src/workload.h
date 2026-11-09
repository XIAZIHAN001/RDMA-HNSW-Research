#pragma once
#include "rdma_hnsw/common/types.h"
#include <string>
#include <vector>
namespace lab {
struct Operation {
  std::size_t seq{}, vector_ref{}, epoch{};
  std::string type;
  rdma_hnsw::NodeId node_id{};
  bool operator==(const Operation &) const = default;
};
std::vector<Operation> make_workload(std::size_t operations, double update_ratio, std::uint64_t seed,
                                    std::size_t nodes, std::size_t queries, std::size_t vectors,
                                    bool hotspot, std::size_t epoch_size);
std::vector<Operation> load_workload(const std::string &path);
void validate_workload(const std::vector<Operation> &operations, std::size_t nodes,
                       std::size_t queries, std::size_t vectors);
} // namespace lab
