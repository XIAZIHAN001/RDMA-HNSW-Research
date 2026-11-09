#pragma once
#include "trace_strategy.h"
#include <ostream>
namespace lab {
inline void trace_header(std::ostream &out) {
  out << "query_id,access_seq,node_id,graph_level,degree,capacity,snapshot_id,phase,"
         "requested_bytes,useful_bytes,wasted_bytes,read_ops,dependent_reads,header_reads,"
         "capacity_clamps,policy,cache_hits,cache_misses,cache_evictions,fallbacks,"
         "cold_miss_fallbacks,hint_underestimates,prefix_reads,thread_id\n";
}
inline void trace_rows(std::ostream &out, const TraceState &state, const char *policy, const char *phase) {
  for (const auto &r : state.rows) {
    const auto &m = r.reads;
    out << r.query_id << ',' << r.access_seq << ',' << r.node_id << ',' << r.graph_level << ','
        << r.degree << ',' << r.capacity << ',' << r.snapshot_id << ',' << phase << ','
        << m.requested_bytes << ',' << m.useful_bytes << ',' << m.wasted_bytes << ','
        << m.read_ops << ',' << m.dependent_reads << ',' << m.header_reads << ','
        << m.capacity_clamps << ',' << policy << ',' << m.cache_hits << ',' << m.cache_misses << ','
        << m.cache_invalidations << ',' << m.fallbacks << ','
        << (m.fallbacks && m.cache_misses) << ',' << (m.fallbacks && m.cache_hits) << ','
        << m.prefix_reads << ',' << state.thread_id << '\n';
  }
}
} // namespace lab
