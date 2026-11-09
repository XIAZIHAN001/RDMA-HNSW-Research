#pragma once
#include "read_strategies.h"

namespace lab {
struct ExactRead {
  rdma_hnsw::Bytes bytes;
  bool ok{};
  std::string error;
};
bool bounded_layer(const rdma_hnsw::RemoteNodeView &view);
ExactRead read_exact(rdma_hnsw::ReadContext &context, rdma_hnsw::Offset offset, std::size_t size);
rdma_hnsw::NeighborReadResult decode_neighbors(const rdma_hnsw::Bytes &bytes,
                                               std::size_t count, std::size_t node_count);
bool valid_neighbors(const std::vector<rdma_hnsw::NodeId> &neighbors, std::size_t node_count);
rdma_hnsw::NeighborReadResult read_prediction(const rdma_hnsw::RemoteNodeView &view,
    rdma_hnsw::ReadContext &context, const std::shared_ptr<StrategyState> &state, bool prefix);
} // namespace lab
