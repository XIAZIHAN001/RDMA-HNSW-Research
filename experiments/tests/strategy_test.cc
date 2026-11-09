#include "rdma_hnsw/read/read_strategy.h"
#include "read_strategies.h"
#include <algorithm>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>

using namespace rdma_hnsw;
namespace {
void require(bool condition, const std::string &message) {
  if (!condition) throw std::runtime_error(message);
}
class FakeTransport final : public RemoteTransport {
public:
  FakeTransport(std::uint32_t degree, std::size_t capacity) {
    data.resize(64 + 4 + 4 * capacity);
    std::memcpy(data.data() + 64, &degree, 4);
    for (std::size_t i = 0; i < capacity; ++i) {
      const NodeId id = static_cast<NodeId>(i);
      std::memcpy(data.data() + 68 + i * 4, &id, 4);
    }
  }
  Bytes data;
  std::vector<std::pair<Offset, std::size_t>> requests;
  std::size_t fail_call{}, short_call{}, oversized_call{};
  TransportStats counters;
  ReadResult read(RemoteAddress address, std::span<byte> destination) override {
    requests.emplace_back(address.offset, destination.size());
    const auto call = requests.size();
    if (address.offset > data.size() || destination.size() > data.size() - address.offset)
      return {false, 0, "outside fake record"};
    std::memcpy(destination.data(), data.data() + address.offset, destination.size());
    if (call == fail_call) return {false, destination.size(), "injected failure"};
    if (call == short_call) return {true, destination.size() - 1, {}};
    if (call == oversized_call) return {true, destination.size() + 1, {}};
    return {true, destination.size(), {}};
  }
  WriteResult write(RemoteAddress, std::span<const byte>) override { return {false, 0, "unused"}; }
  UpdateResult submit_update(const UpdateRequest &) override { return {false, 0, "unused"}; }
  const TransportStats &stats() const override { return counters; }
};

void fixed_full_record_and_errors() {
  FakeTransport transport(10, 32);
  ReadMetrics metrics;
  ReadContext context{transport, &metrics, 8, 100, {}};
  auto strategy = make_read_strategy(ReadPolicy::Fixed);
  const auto result = strategy->read({1, 1, 0, 64, 32}, context);
  require(result.ok && result.neighbors.size() == 10 && result.neighbors.front() == 0,
          "fixed must preserve all ordered neighbors including ID zero");
  require(metrics.requested_bytes == 132 && metrics.useful_bytes == 44 &&
          metrics.wasted_bytes == 88 && metrics.read_ops == 1, "fixed hand count");
  bool rejected_all = true;
  for (int fault = 0; fault < 3; ++fault) {
    FakeTransport broken(10, 32);
    if (fault == 0) broken.fail_call = 1;
    if (fault == 1) broken.short_call = 1;
    if (fault == 2) broken.oversized_call = 1;
    ReadContext bad_context{broken, nullptr, 8, 100, {}};
    const auto failed = strategy->read({1, 1, 0, 64, 32}, bad_context);
    if (failed.ok || failed.error.empty()) {
      rejected_all = false;
      std::cerr << "incorrect success for fixed fault " << fault << '\n';
    }
  }
  require(rejected_all, "fixed must reject false/full, short, oversized reads");
}

void strategy_boundaries(ReadPolicy policy) {
  auto state = std::make_shared<lab::StrategyState>();
  state->valid_node_count = 100;
  auto strategy = lab::make_lab_strategy(policy, state);
  for (const auto &[degree, capacity] : std::vector<std::pair<std::uint32_t, std::size_t>>{
           {0, 32}, {10, 32}, {32, 32}, {33, 32}, {0, 0}, {3, 7}}) {
    FakeTransport transport(degree, capacity);
    ReadMetrics metrics;
    ReadContext context{transport, &metrics, 8, 100, {}};
    const auto result = strategy->read({1, 1, 0, 64, capacity}, context);
    const auto count = std::min<std::size_t>(degree, capacity);
    require(result.ok && result.neighbors.size() == count, "clamped full neighbor count");
    for (std::size_t i = 0; i < count; ++i)
      require(result.neighbors[i] == i, "preserve neighbor order and ID zero");
    const auto useful = 4 + count * 4;
    const auto requested = policy == ReadPolicy::Fixed ? 4 + capacity * 4 : useful;
    require(metrics.requested_bytes == requested && metrics.useful_bytes == useful &&
            metrics.wasted_bytes == requested - useful, "boundary byte counts");
    require(metrics.capacity_clamps == (degree > capacity ? 1U : 0U), "count invalid degree clamp");
    const auto operations = policy == ReadPolicy::Fixed || count == 0 ? 1 : 2;
    require(metrics.read_ops == static_cast<std::uint64_t>(operations), "boundary read count");
    require(metrics.dependent_reads == static_cast<std::uint64_t>(operations - 1), "dependent read count");
    require(transport.requests.front() == std::pair<Offset, std::size_t>{64,
               policy == ReadPolicy::Fixed ? 4 + capacity * 4 : 4}, "first request range");
    if (operations == 2)
      require(transport.requests.back() == std::pair<Offset, std::size_t>{68, count * 4},
              "exact second request after the header");
  }
  FakeTransport invalid(2, 4);
  const NodeId invalid_id = 100;
  std::memcpy(invalid.data.data() + 72, &invalid_id, 4);
  ReadContext invalid_context{invalid, nullptr, 8, 100, {}};
  require(!strategy->read({1, 1, 0, 64, 4}, invalid_context).ok, "reject invalid neighbor ID");
}

void reject_overflow_before_transport(ReadPolicy policy) {
  auto state = std::make_shared<lab::StrategyState>();
  state->valid_node_count = 100;
  auto strategy = lab::make_lab_strategy(policy, state);
  for (const auto &view : {RemoteNodeView{1, 1, 0, 64, std::numeric_limits<std::size_t>::max()},
                           RemoteNodeView{1, 1, 0, std::numeric_limits<Offset>::max() - 3, 32}}) {
    FakeTransport transport(10, 32);
    ReadContext context{transport, nullptr, 8, 100, {}};
    const auto result = strategy->read(view, context);
    require(!result.ok && transport.requests.empty(), "reject range overflow before requesting bytes");
  }
}

void header_transport_errors() {
  auto state = std::make_shared<lab::StrategyState>();
  state->valid_node_count = 100;
  auto strategy = lab::make_lab_strategy(ReadPolicy::HeaderActual, state);
  for (std::size_t call : {1U, 2U}) {
    for (int fault = 0; fault < 3; ++fault) {
      FakeTransport transport(10, 32);
      if (fault == 0) transport.fail_call = call;
      if (fault == 1) transport.short_call = call;
      if (fault == 2) transport.oversized_call = call;
      ReadContext context{transport, nullptr, 8, 100, {}};
      const auto result = strategy->read({1, 1, 0, 64, 32}, context);
      require(!result.ok && !result.error.empty(), "reject failed/short header or body");
      require(transport.requests.size() == call, "stop issuing reads after a failure");
    }
  }
}
void cached_history_repair_and_lru() {
  auto state = std::make_shared<lab::StrategyState>();
  state->valid_node_count = 100;
  auto strategy = lab::make_lab_strategy(ReadPolicy::CachedLength, state);
  const auto access = [&](std::uint32_t degree, NodeId id, std::uint32_t level,
                          std::size_t cache_capacity, std::size_t prediction,
                          bool hit, bool fail_body = false) {
    FakeTransport transport(degree, 32);
    if (fail_body) transport.fail_call = 2;
    ReadMetrics metrics;
    ReadContext context{transport, &metrics, 8, cache_capacity, {}};
    auto result = strategy->read({id, 999, level, 64, 32}, context);
    require(transport.requests.front().second == 4 + 4 * prediction,
            "cached first width must depend on history, not the current degree or generation");
    require(metrics.cache_hits == static_cast<unsigned>(hit) &&
            metrics.cache_misses == static_cast<unsigned>(!hit), "cache hit/miss accounting");
    if (fail_body) { require(!result.ok, "cached fallback failure rejected"); return; }
    require(result.ok && result.neighbors.size() == degree, "cached returns all neighbors after repair");
    for (std::size_t i = 0; i < degree; ++i)
      require(result.neighbors[i] == i, "cached ordered neighbors including zero");
    const bool fallback = degree > prediction;
    require(metrics.read_ops == 1U + fallback && metrics.dependent_reads == fallback &&
            metrics.fallbacks == fallback, "cached fallback read accounting");
    require(metrics.requested_bytes == 4 + 4 * std::max<std::size_t>(degree, prediction) &&
            metrics.useful_bytes == 4 + 4 * degree &&
            metrics.wasted_bytes == 4 * (prediction > degree ? prediction - degree : 0),
            "cached exact accounting including stale overestimate");
  };
  access(8, 1, 0, 2, 0, false); // cold
  access(8, 1, 0, 2, 8, true);  // warm
  access(10, 1, 0, 2, 8, true); // stale growth
  access(3, 1, 0, 2, 10, true); // shrink
  access(10, 1, 0, 2, 3, true, true); // fail: do not learn 10
  access(3, 1, 0, 2, 3, true);
  access(0, 1, 1, 2, 0, false); // same node, other layer
  access(3, 1, 0, 2, 3, true); // touch base as MRU
  access(2, 2, 0, 2, 0, false); // evict node1/layer1
  access(3, 1, 0, 2, 3, true);
  access(0, 1, 1, 2, 0, false);
  access(0, 1, 1, 0, 0, false); // disabled
  access(2, 1, 1, 0, 0, false);

  // Length hints cache only a count: IDs must always come from this read.
  FakeTransport replacement(3, 32);
  NodeId changed = 42;
  std::memcpy(replacement.data.data() + 68, &changed, 4);
  ReadContext context{replacement, nullptr, 8, 2, {}};
  auto changed_result = strategy->read({1, 1000, 0, 64, 32}, context);
  require(changed_result.ok && changed_result.neighbors.front() == 42,
          "same degree with changed IDs must read the current neighbors");
}

void cached_boundaries_and_failure() {
  strategy_boundaries(ReadPolicy::HeaderActual); // independent baseline retained
  auto state = std::make_shared<lab::StrategyState>();
  state->valid_node_count = 100;
  auto strategy = lab::make_lab_strategy(ReadPolicy::CachedLength, state);
  for (const auto &[degree, capacity] : std::vector<std::pair<std::uint32_t, std::size_t>>{
      {0, 0}, {0, 32}, {32, 32}, {33, 32}, {3, 7}}) {
    FakeTransport transport(degree, capacity);
    ReadMetrics metrics;
    ReadContext context{transport, &metrics, 8, 0, {}};
    auto result = strategy->read({1, 1, 0, 64, capacity}, context);
    require(result.ok && result.neighbors.size() == std::min<std::size_t>(degree, capacity),
            "cached zero/odd/full/clamped capacities");
    require(metrics.capacity_clamps == (degree > capacity ? 1U : 0U), "cached clamp count");
  }
  for (const auto call : {1U, 2U}) for (int fault = 0; fault < 3; ++fault) {
    FakeTransport transport(10, 32);
    if (fault == 0) transport.fail_call = call;
    if (fault == 1) transport.short_call = call;
    if (fault == 2) transport.oversized_call = call;
    ReadContext context{transport, nullptr, 8, 0, {}};
    require(!strategy->read({1, 1, 0, 64, 32}, context).ok, "cached rejects both-stage completion errors");
    require(transport.requests.size() == call, "cached stops on failure");
  }
  FakeTransport invalid(2, 4);
  NodeId bad_id = 100;
  std::memcpy(invalid.data.data() + 72, &bad_id, 4);
  ReadContext bad_context{invalid, nullptr, 8, 0, {}};
  require(!strategy->read({1, 1, 0, 64, 4}, bad_context).ok, "cached invalid IDs rejected");
}
void prefix_boundaries_and_history() {
  for (std::size_t granularity : {1U,2U,4U,8U,16U,64U}) {
    auto state=std::make_shared<lab::StrategyState>();state->valid_node_count=100;
    auto strategy=lab::make_lab_strategy(ReadPolicy::Prefix,state);
    for (std::size_t capacity : {0U,7U,32U}) {
      const std::vector<std::size_t> degrees{0,std::min(granularity-1,capacity),std::min(granularity,capacity),
                                           std::min(granularity+1,capacity),capacity};
      for (auto degree:degrees) {
        FakeTransport transport(degree,capacity);ReadMetrics metrics;
        ReadContext context{transport,&metrics,granularity,0,{}};
        auto result=strategy->read({1,1,0,64,capacity},context);
        const auto width=std::min(granularity,capacity);
        require(result.ok&&result.neighbors.size()==degree,"prefix all boundary neighbors");
        for(std::size_t i=0;i<degree;++i)require(result.neighbors[i]==i,"prefix order including zero");
        require(transport.requests.front().second==4+4*width,"prefix cold width never uses current degree");
        require(metrics.requested_bytes==4+4*std::max(width,degree)&&metrics.useful_bytes==4+4*degree,
                "prefix hand byte counts");
        require(metrics.read_ops==1U+(degree>width)&&metrics.fallbacks==(degree>width),"prefix dependency and fallback");
      }
    }
  }
  auto state=std::make_shared<lab::StrategyState>();state->valid_node_count=100;
  auto strategy=lab::make_lab_strategy(ReadPolicy::Prefix,state);
  const std::vector<std::uint32_t> degrees{3,7,8,9,10,3,0,10};
  const std::vector<std::size_t> widths{8,8,8,8,16,16,8,0};
  for(std::size_t i=0;i<degrees.size();++i){
    FakeTransport transport(degrees[i],32);ReadMetrics m;ReadContext context{transport,&m,8,100,{}};
    auto result=strategy->read({1,200+i,0,64,32},context);
    require(result.ok&&result.neighbors.size()==degrees[i],"prefix same/cross-class growth and shrink");
    require(transport.requests.front().second==4+4*widths[i],"prefix class learned only after previous success");
    require(m.fallbacks==(degrees[i]>widths[i]),"prefix exact class fallback behavior");
  }
  FakeTransport other_layer(2,7);ReadContext other{other_layer,nullptr,8,100,{}};
  require(strategy->read({1,1000,1,64,7},other).ok&&other_layer.requests.front().second==32,
          "prefix cache layer key isolation");
  // G>=C gives a full slot on cold or nonzero hints. A learned zero still uses header-only.
  auto large_state=std::make_shared<lab::StrategyState>();large_state->valid_node_count=100;
  auto large=lab::make_lab_strategy(ReadPolicy::Prefix,large_state);
  FakeTransport empty(0,32);ReadContext ce{empty,nullptr,64,100,{}};
  require(large->read({1,1,0,64,32},ce).ok&&empty.requests.front().second==132,"G>=C cold full slot");
  FakeTransport grown(3,32);ReadContext cg{grown,nullptr,64,100,{}};
  require(large->read({1,2,0,64,32},cg).ok&&grown.requests.front().second==4&&grown.requests.size()==2,
          "learned empty prefix stays zero and repairs later growth");
  FakeTransport clamped(33,32);ReadMetrics clamp_metrics;ReadContext cc{clamped,&clamp_metrics,8,0,{}};
  require(strategy->read({3,1,0,64,32},cc).neighbors.size()==32&&clamp_metrics.capacity_clamps==1,
          "prefix clamps and counts degree above capacity");
  FakeTransport saturated(32,32);ReadContext cs{saturated,nullptr,std::numeric_limits<std::size_t>::max(),100,{}};
  require(strategy->read({1,2000,0,64,32},cs).ok&&saturated.requests.front().second==132,
          "prefix multiplication saturates before overflow");
  FakeTransport invalid_g(2,7);ReadContext zero{invalid_g,nullptr,0,100,{}};
  require(!strategy->read({1,1000,0,64,7},zero).ok&&invalid_g.requests.empty(),"zero granularity rejected before transport");
}
void prefix_failure_and_g1_equivalence(){
  for(auto call:{1U,2U})for(int fault=0;fault<3;++fault){
    auto state=std::make_shared<lab::StrategyState>();state->valid_node_count=100;
    auto strategy=lab::make_lab_strategy(ReadPolicy::Prefix,state);
    FakeTransport transport(10,32);
    if(fault==0)transport.fail_call=call;
    if(fault==1)transport.short_call=call;
    if(fault==2)transport.oversized_call=call;
    ReadContext context{transport,nullptr,8,100,{}};
    require(!strategy->read({1,1,0,64,32},context).ok&&transport.requests.size()==call,"prefix stops on either-stage failure");
  }
  auto a=std::make_shared<lab::StrategyState>(),b=std::make_shared<lab::StrategyState>();
  a->valid_node_count=b->valid_node_count=100;
  auto exact=lab::make_lab_strategy(ReadPolicy::CachedLength,a),prefix=lab::make_lab_strategy(ReadPolicy::Prefix,b);
  for(auto degree:{8U,8U,10U,3U,0U,32U}){
    FakeTransport t1(degree,32),t2(degree,32);ReadMetrics m1,m2;
    ReadContext c1{t1,&m1,1,100,{}},c2{t2,&m2,1,100,{}};
    require(exact->read({1,1,0,64,32},c1).neighbors==prefix->read({1,1,0,64,32},c2).neighbors,"G1 neighbors match");
    if(m1.cache_hits)require(m1.requested_bytes==m2.requested_bytes&&m1.read_ops==m2.read_ops,"G1 warm equals exact hints");
  }
  FakeTransport invalid(2,7);NodeId bad=100;std::memcpy(invalid.data.data()+72,&bad,4);
  ReadContext bad_context{invalid,nullptr,8,0,{}};
  require(!prefix->read({2,1,0,64,7},bad_context).ok,"prefix invalid IDs rejected");
}
} // namespace

int main() {
  int failed = 0;
  const auto run = [&](const char *name, auto test) {
    try { test(); std::cout << "PASS " << name << '\n'; }
    catch (const std::exception &error) {
      ++failed; std::cerr << "FAIL " << name << ": " << error.what() << '\n';
    }
  };
  run("fixed transport completion", fixed_full_record_and_errors);
  run("fixed boundary and IDs", [] { strategy_boundaries(ReadPolicy::Fixed); });
  run("header_actual boundary and IDs", [] { strategy_boundaries(ReadPolicy::HeaderActual); });
  run("header_actual transport completion", header_transport_errors);
  run("fixed request range overflow", [] { reject_overflow_before_transport(ReadPolicy::Fixed); });
  run("header_actual request range overflow", [] { reject_overflow_before_transport(ReadPolicy::HeaderActual); });
  run("cached historical repair, layers and LRU", cached_history_repair_and_lru);
  run("cached boundaries and transport", cached_boundaries_and_failure);
  run("cached request range overflow", [] { reject_overflow_before_transport(ReadPolicy::CachedLength); });
  run("prefix levels and capacity boundaries",prefix_boundaries_and_history);
  run("prefix failure and G1 equivalence",prefix_failure_and_g1_equivalence);
  run("prefix range overflow",[] {reject_overflow_before_transport(ReadPolicy::Prefix);});
  return failed ? 1 : 0;
}
