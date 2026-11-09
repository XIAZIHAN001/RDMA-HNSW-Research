#include "strategy_common.h"
namespace lab {
using namespace rdma_hnsw;
namespace {
class Prefix final : public NeighborReadStrategy {
  std::shared_ptr<StrategyState> state_;
public:
  explicit Prefix(std::shared_ptr<StrategyState> state):state_(std::move(state)){}
  const char *name() const override {return "prefix";}
  NeighborReadResult read(const RemoteNodeView &view,ReadContext &context) override {
    return read_prediction(view,context,state_,true);
  }
};
}
std::unique_ptr<NeighborReadStrategy> make_prefix_strategy(std::shared_ptr<StrategyState> state){
  return std::make_unique<Prefix>(std::move(state));
}
} // namespace lab
