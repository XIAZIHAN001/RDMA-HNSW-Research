#include "strategy_common.h"
#include <algorithm>
#include <cstring>
#include <limits>

namespace lab {
using namespace rdma_hnsw;
bool bounded_layer(const RemoteNodeView &view) {
  if (view.capacity > (std::numeric_limits<std::size_t>::max() - sizeof(std::uint32_t)) / sizeof(NodeId))
    return false;
  const auto size = sizeof(std::uint32_t) + view.capacity * sizeof(NodeId);
  return view.layer_offset <= std::numeric_limits<Offset>::max() - size;
}
ExactRead read_exact(ReadContext &context, Offset offset, std::size_t size) {
  if (size == 0 || offset > std::numeric_limits<Offset>::max() - size)
    return {{}, false, "invalid neighbor request range"};
  Bytes bytes(size);
  if (context.metrics) {
    ++context.metrics->read_ops;
    context.metrics->requested_bytes += size;
  }
  const auto result = context.transport.read({offset, 0}, bytes);
  if (!result.ok || result.bytes != size)
    return {{}, false, result.error.empty() ? "failed or short neighbor read" : result.error};
  return {std::move(bytes), true, {}};
}
bool valid_neighbors(const std::vector<NodeId> &neighbors, std::size_t node_count) {
  return std::all_of(neighbors.begin(), neighbors.end(),
                     [node_count](NodeId id) { return id < node_count; });
}
NeighborReadResult decode_neighbors(const Bytes &bytes, std::size_t count, std::size_t node_count) {
  if (count > std::numeric_limits<std::size_t>::max() / sizeof(NodeId) ||
      bytes.size() != count * sizeof(NodeId))
    return {{}, false, "invalid neighbor body length"};
  std::vector<NodeId> neighbors(count);
  if (count) std::memcpy(neighbors.data(), bytes.data(), bytes.size());
  if (!valid_neighbors(neighbors, node_count))
    return {{}, false, "neighbor ID is outside the current snapshot"};
  return {std::move(neighbors), true, {}};
}
rdma_hnsw::NeighborReadResult read_prediction(const rdma_hnsw::RemoteNodeView &view,
    rdma_hnsw::ReadContext &context, const std::shared_ptr<StrategyState> &state, bool prefix) {
  using namespace rdma_hnsw;
    if (!bounded_layer(view)) return {{}, false, "neighbor layer range overflow"};
    if (prefix && context.prefix_granularity == 0)
      return {{}, false, "prefix granularity must be positive"};
    auto &cache = prefix ? state->prefix_levels : state->lengths;
    const auto evictions_before = cache.evictions();
    cache.capacity(context.cache_capacity);
    const CacheKey key{view.id, view.level};
    const auto hint = cache.get(key);
    const auto granularity = context.prefix_granularity;
    const auto level = hint.value_or(1);
    // Saturate multiplication before it can overflow. A learned empty level stays zero.
    const auto prediction = prefix ? (level > view.capacity / granularity ? view.capacity : level * granularity)
                                   : (hint ? std::min(*hint, view.capacity) : 0);
    if (context.metrics) {
      if (prefix) ++context.metrics->prefix_reads;
      if (hint) ++context.metrics->cache_hits;
      else ++context.metrics->cache_misses;
      if (prediction == 0) ++context.metrics->header_reads;
      context.metrics->cache_invalidations += cache.evictions() - evictions_before;
    }
    auto first = read_exact(context, view.layer_offset, sizeof(std::uint32_t) + prediction * sizeof(NodeId));
    if (!first.ok) return {{}, false, first.error};
    std::uint32_t raw_degree{};
    std::memcpy(&raw_degree, first.bytes.data(), sizeof(raw_degree));
    if (raw_degree > view.capacity && context.metrics) ++context.metrics->capacity_clamps;
    const auto degree = std::min<std::size_t>(raw_degree, view.capacity);
    Bytes body(first.bytes.begin() + sizeof(raw_degree),
               first.bytes.begin() + sizeof(raw_degree) + std::min(prediction, degree) * sizeof(NodeId));
    if (degree > prediction) {
      if (context.metrics) { ++context.metrics->fallbacks; ++context.metrics->dependent_reads; }
      auto remainder = read_exact(context, view.layer_offset + sizeof(raw_degree) + prediction * sizeof(NodeId),
                                  (degree - prediction) * sizeof(NodeId));
      if (!remainder.ok) return {{}, false, remainder.error};
      body.insert(body.end(), remainder.bytes.begin(), remainder.bytes.end());
    }
    auto result = decode_neighbors(body, degree, state->valid_node_count);
    if (!result.ok) return result;
    const auto before_put = cache.evictions();
    cache.put(key, prefix ? degree / granularity + (degree % granularity != 0) : degree);
    if (context.metrics) {
      context.metrics->useful_bytes += sizeof(raw_degree) + degree * sizeof(NodeId);
      context.metrics->wasted_bytes += prediction > degree ? (prediction - degree) * sizeof(NodeId) : 0;
      context.metrics->cache_invalidations += cache.evictions() - before_put;
    }
    return result;
}
} // namespace lab
