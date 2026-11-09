#include "strategy_common.h"
#include <algorithm>
#include <cstring>

namespace lab {
using namespace rdma_hnsw;
namespace {
class CachedLength final : public NeighborReadStrategy {
  std::shared_ptr<StrategyState> state_;
public:
  explicit CachedLength(std::shared_ptr<StrategyState> state) : state_(std::move(state)) {}
  const char *name() const override { return "cached_length"; }
  NeighborReadResult read(const RemoteNodeView &view, ReadContext &context) override {
    return read_prediction(view, context, state_, false);
  }
};
} // namespace
std::unique_ptr<NeighborReadStrategy> make_cached_length_strategy(std::shared_ptr<StrategyState> state) {
  return std::make_unique<CachedLength>(std::move(state));
}
} // namespace lab
