#include "rdma_hnsw/read/read_strategy.h"
#include <algorithm>
#include <cstring>
#include <memory>
#include <limits>
#include <stdexcept>

namespace rdma_hnsw {
namespace {
NeighborReadResult decode(const std::vector<byte> &b, std::size_t count) {
  if (b.size() < count * sizeof(NodeId))
    return {{}, false, "short neighbor read"};
  std::vector<NodeId> out(count);
  if (count)
    std::memcpy(out.data(), b.data(), count * sizeof(NodeId));
  return {std::move(out), true, {}};
}
std::vector<byte> read_bytes(RemoteTransport &t, Offset o, std::size_t n,
                             ReadMetrics *m) {
  std::vector<byte> b(n);
  const auto r = t.read({o, 0}, b);
  if (m) {
    m->read_ops++;
    m->requested_bytes += n;
  }
  if (!r.ok || r.bytes != n) {
    // An error or any non-exact completion invalidates the whole fixed read.
    // Never trust a provider's byte count as a buffer length on failure.
    b.clear();
  }
  return b;
}
class Fixed final : public NeighborReadStrategy {
public:
  NeighborReadResult read(const RemoteNodeView &v, ReadContext &c) override {
    if (v.capacity > (std::numeric_limits<std::size_t>::max() - sizeof(std::uint32_t)) / sizeof(NodeId))
      return {{}, false, "fixed read size overflow"};
    const auto n = sizeof(std::uint32_t) + v.capacity * sizeof(NodeId);
    if (v.layer_offset > std::numeric_limits<Offset>::max() - n)
      return {{}, false, "fixed read offset overflow"};
    auto b = read_bytes(c.transport, v.layer_offset, n, c.metrics);
    if (b.size() != n)
      return {{}, false, "failed or short fixed read"};
    std::uint32_t degree{};
    std::memcpy(&degree, b.data(), sizeof(degree));
    if (degree > v.capacity && c.metrics) ++c.metrics->capacity_clamps;
    degree = static_cast<std::uint32_t>(std::min<std::size_t>(degree, v.capacity));
    if (c.metrics) {
      c.metrics->useful_bytes += sizeof(degree) + degree * sizeof(NodeId);
      c.metrics->wasted_bytes += (v.capacity - degree) * sizeof(NodeId);
    }
    return decode(std::vector<byte>(b.begin() + sizeof(degree), b.end()),
                  degree);
  }
  const char *name() const override { return "fixed"; }
};
} // namespace
void ReadMetrics::merge(const ReadMetrics &o) {
  requested_bytes += o.requested_bytes;
  useful_bytes += o.useful_bytes;
  wasted_bytes += o.wasted_bytes;
  read_ops += o.read_ops;
  header_reads += o.header_reads;
  dependent_reads += o.dependent_reads;
  cache_hits += o.cache_hits;
  cache_misses += o.cache_misses;
  cache_invalidations += o.cache_invalidations;
  prefix_reads += o.prefix_reads;
  fallbacks += o.fallbacks;
  capacity_clamps += o.capacity_clamps;
}
std::unique_ptr<NeighborReadStrategy> make_read_strategy(ReadPolicy p) {
  if (p != ReadPolicy::Fixed)
    throw std::invalid_argument("read strategy is a student extension; "
                                "implement and inject it before selecting " +
                                to_string(p));
  return std::make_unique<Fixed>();
}
} // namespace rdma_hnsw
