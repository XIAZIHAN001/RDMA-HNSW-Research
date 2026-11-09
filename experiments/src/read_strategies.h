#pragma once
#include "rdma_hnsw/read/read_strategy.h"
#include <memory>
#include "degree_cache.h"

namespace lab {
struct StrategyState {
  std::size_t valid_node_count{};
  DegreeCache lengths;
  DegreeCache prefix_levels;
};
std::unique_ptr<rdma_hnsw::NeighborReadStrategy> make_lab_strategy(
    rdma_hnsw::ReadPolicy policy, std::shared_ptr<StrategyState> state);
std::unique_ptr<rdma_hnsw::NeighborReadStrategy> make_cached_length_strategy(
    std::shared_ptr<StrategyState> state);
std::unique_ptr<rdma_hnsw::NeighborReadStrategy> make_prefix_strategy(
    std::shared_ptr<StrategyState> state);
} // namespace lab
