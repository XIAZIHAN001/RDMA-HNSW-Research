#pragma once
#include "rdma_hnsw/read/read_strategy.h"
#include <memory>
#include <vector>

namespace lab {
struct TraceRow {
  std::uint64_t query_id, access_seq, snapshot_id;
  rdma_hnsw::NodeId node_id;
  std::uint32_t graph_level;
  std::size_t degree, capacity;
  rdma_hnsw::ReadMetrics reads;
};

struct TraceState {
  std::uint64_t query_id{}, access_seq{}, snapshot_id{};
  std::uint64_t thread_id{};
  std::uint64_t accesses{}, cold_miss_fallbacks{}, hint_underestimates{};
  bool capture{};
  rdma_hnsw::ReadMetrics reads;
  std::vector<TraceRow> rows;
};

class TraceStrategy final : public rdma_hnsw::NeighborReadStrategy {
public:
  TraceStrategy(std::unique_ptr<rdma_hnsw::NeighborReadStrategy> inner,
                std::shared_ptr<TraceState> state)
      : inner_(std::move(inner)), state_(std::move(state)) {}
  rdma_hnsw::NeighborReadResult read(const rdma_hnsw::RemoteNodeView &view,
                                    rdma_hnsw::ReadContext &context) override {
    const auto before = context.metrics ? *context.metrics : rdma_hnsw::ReadMetrics{};
    auto result = inner_->read(view, context);
    if (!result.ok)
      return result;
    rdma_hnsw::ReadMetrics delta;
    if (context.metrics) {
      delta.requested_bytes = context.metrics->requested_bytes - before.requested_bytes;
      delta.useful_bytes = context.metrics->useful_bytes - before.useful_bytes;
      delta.wasted_bytes = context.metrics->wasted_bytes - before.wasted_bytes;
      delta.read_ops = context.metrics->read_ops - before.read_ops;
      delta.header_reads = context.metrics->header_reads - before.header_reads;
      delta.dependent_reads = context.metrics->dependent_reads - before.dependent_reads;
      delta.cache_hits = context.metrics->cache_hits - before.cache_hits;
      delta.cache_misses = context.metrics->cache_misses - before.cache_misses;
      delta.cache_invalidations = context.metrics->cache_invalidations - before.cache_invalidations;
      delta.prefix_reads = context.metrics->prefix_reads - before.prefix_reads;
      delta.fallbacks = context.metrics->fallbacks - before.fallbacks;
      delta.capacity_clamps = context.metrics->capacity_clamps - before.capacity_clamps;
    }
    state_->reads.merge(delta);
    state_->cold_miss_fallbacks += delta.fallbacks && delta.cache_misses;
    state_->hint_underestimates += delta.fallbacks && delta.cache_hits;
    ++state_->accesses;
    if (state_->capture)
      state_->rows.push_back({state_->query_id, state_->access_seq++, state_->snapshot_id,
                              view.id, view.level, result.neighbors.size(),
                              view.capacity, delta});
    return result;
  }
  const char *name() const override { return inner_->name(); }
private:
  std::unique_ptr<rdma_hnsw::NeighborReadStrategy> inner_;
  std::shared_ptr<TraceState> state_;
};
} // namespace lab
