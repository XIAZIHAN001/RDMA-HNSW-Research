#pragma once
#include "rdma_hnsw/common/types.h"
#include <list>
#include <optional>
#include <unordered_map>
#include <cstdint>

namespace lab {
struct CacheKey {
  rdma_hnsw::NodeId node_id{};
  std::uint32_t level{};
  bool operator==(const CacheKey &) const = default;
};
struct CacheKeyHash {
  std::size_t operator()(CacheKey key) const noexcept {
    return std::hash<std::uint64_t>{}((static_cast<std::uint64_t>(key.node_id) << 32) | key.level);
  }
};
// Thread-local, count-only hints. Snapshot generations deliberately do not invalidate them.
class DegreeCache {
  using Entry = std::pair<CacheKey, std::size_t>;
  using List = std::list<Entry>;
  List recency_;
  std::unordered_map<CacheKey, List::iterator, CacheKeyHash> entries_;
  std::size_t capacity_{};
  std::uint64_t evictions_{};
  void trim() {
    while (entries_.size() > capacity_) {
      entries_.erase(recency_.back().first);
      recency_.pop_back();
      ++evictions_;
    }
  }
public:
  DegreeCache() = default;
  DegreeCache(const DegreeCache &) = delete;
  DegreeCache &operator=(const DegreeCache &) = delete;
  void capacity(std::size_t value) { capacity_ = value; trim(); }
  std::size_t size() const { return entries_.size(); }
  std::uint64_t evictions() const { return evictions_; }
  std::optional<std::size_t> get(CacheKey key) {
    const auto found = entries_.find(key);
    if (found == entries_.end()) return std::nullopt;
    recency_.splice(recency_.begin(), recency_, found->second);
    return found->second->second;
  }
  void put(CacheKey key, std::size_t value) {
    if (capacity_ == 0) return;
    const auto found = entries_.find(key);
    if (found != entries_.end()) {
      found->second->second = value;
      recency_.splice(recency_.begin(), recency_, found->second);
    } else {
      recency_.emplace_front(key, value);
      entries_[key] = recency_.begin();
      trim();
    }
  }
};
} // namespace lab
