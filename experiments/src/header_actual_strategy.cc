#include "read_strategies.h"
#include "strategy_common.h"
#include <algorithm>
#include <cstring>
#include <stdexcept>

namespace lab {
using namespace rdma_hnsw;
namespace {
class ValidatedFixed final : public NeighborReadStrategy {
public:
  explicit ValidatedFixed(std::shared_ptr<StrategyState> state)
      : state_(std::move(state)), inner_(make_read_strategy(ReadPolicy::Fixed)) {}
  NeighborReadResult read(const RemoteNodeView &view, ReadContext &context) override {
    auto result = inner_->read(view, context);
    if (result.ok && !valid_neighbors(result.neighbors, state_->valid_node_count))
      return {{}, false, "neighbor ID is outside the current snapshot"};
    return result;
  }
  const char *name() const override { return "fixed"; }
private:
  std::shared_ptr<StrategyState> state_;
  std::unique_ptr<NeighborReadStrategy> inner_;
};
class HeaderActual final : public NeighborReadStrategy {
public:
  explicit HeaderActual(std::shared_ptr<StrategyState> state) : state_(std::move(state)) {}
  NeighborReadResult read(const RemoteNodeView &view, ReadContext &context) override {
    if (!bounded_layer(view)) return {{}, false, "neighbor layer range overflow"};
    if (context.metrics) ++context.metrics->header_reads;
    const auto header = read_exact(context, view.layer_offset, sizeof(std::uint32_t));
    if (!header.ok) return {{}, false, header.error};
    std::uint32_t degree{};
    std::memcpy(&degree, header.bytes.data(), sizeof(degree));
    if (degree > view.capacity && context.metrics) ++context.metrics->capacity_clamps;
    const auto count = std::min<std::size_t>(degree, view.capacity);
    Bytes body;
    if (count) {
      if (context.metrics) ++context.metrics->dependent_reads;
      auto read = read_exact(context, view.layer_offset + sizeof(degree), count * sizeof(NodeId));
      if (!read.ok) return {{}, false, read.error};
      body = std::move(read.bytes);
    }
    auto result = decode_neighbors(body, count, state_->valid_node_count);
    if (result.ok && context.metrics)
      context.metrics->useful_bytes += sizeof(degree) + count * sizeof(NodeId);
    return result;
  }
  const char *name() const override { return "header_actual"; }
private:
  std::shared_ptr<StrategyState> state_;
};
} // namespace
std::unique_ptr<rdma_hnsw::NeighborReadStrategy> make_lab_strategy(
    ReadPolicy policy, std::shared_ptr<StrategyState> state) {
  if (!state) throw std::invalid_argument("strategy state cannot be null");
  if (policy == ReadPolicy::Fixed) return std::make_unique<ValidatedFixed>(std::move(state));
  if (policy == ReadPolicy::HeaderActual) return std::make_unique<HeaderActual>(std::move(state));
  if (policy == ReadPolicy::CachedLength) return make_cached_length_strategy(std::move(state));
  if (policy == ReadPolicy::Prefix) return make_prefix_strategy(std::move(state));
  throw std::invalid_argument("lab strategy is not implemented: " + to_string(policy));
}
} // namespace lab
