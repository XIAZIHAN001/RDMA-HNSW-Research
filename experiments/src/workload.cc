#include "workload.h"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <numeric>
#include <random>
#include <sstream>
#include <stdexcept>

namespace lab {
std::vector<Operation> make_workload(std::size_t count, double ratio, std::uint64_t seed,
                                    std::size_t nodes, std::size_t queries, std::size_t vectors,
                                    bool hotspot, std::size_t epoch_size) {
  if (!count || !nodes || !queries || !vectors || !epoch_size ||
      nodes > std::numeric_limits<rdma_hnsw::NodeId>::max() ||
      !std::isfinite(ratio) || ratio < 0 || ratio > 1)
    throw std::invalid_argument("invalid workload parameters");
  const auto updates = static_cast<std::size_t>(std::round(static_cast<double>(count) * ratio));
  std::vector<unsigned char> types(count, 0);
  std::fill_n(types.begin(), updates, 1);
  std::mt19937_64 generator(seed);
  std::shuffle(types.begin(), types.end(), generator);
  const auto targets = hotspot ? std::max<std::size_t>(1, nodes / 100) : nodes;
  std::uniform_int_distribution<rdma_hnsw::NodeId> pick(0, targets - 1);
  std::uniform_int_distribution<std::size_t> query(0, queries - 1), vector(0, vectors - 1);
  std::vector<Operation> result;
  result.reserve(count);
  for (std::size_t seq = 0; seq < count; ++seq) {
    const bool update = types[seq];
    result.push_back({seq, update ? vector(generator) : query(generator), seq / epoch_size,
                      update ? "upsert" : "query", update ? pick(generator) : 0});
  }
  return result;
}

void validate_workload(const std::vector<Operation> &operations, std::size_t nodes,
                       std::size_t queries, std::size_t vectors) {
  if (operations.empty()) throw std::invalid_argument("workload must not be empty");
  for (std::size_t seq = 0; seq < operations.size(); ++seq) {
    const auto &op = operations[seq];
    if (op.seq != seq || (seq && op.epoch < operations[seq - 1].epoch))
      throw std::invalid_argument("invalid workload sequence or epoch");
    if (op.type == "query") {
      if (op.vector_ref >= queries) throw std::invalid_argument("workload query vector out of range");
    } else if (op.type == "insert") {
      if (op.node_id != nodes || nodes == std::numeric_limits<rdma_hnsw::NodeId>::max() || op.vector_ref >= vectors)
        throw std::invalid_argument("workload insert ID or vector out of range");
      ++nodes;
    } else if (op.type == "upsert" || op.type == "erase") {
      if (op.node_id >= nodes || (op.type == "upsert" && op.vector_ref >= vectors))
        throw std::invalid_argument("workload update ID or vector out of range");
    } else throw std::invalid_argument("unsupported workload operation");
  }
}

std::vector<Operation> load_workload(const std::string &path) {
  std::ifstream input(path);
  if (!input) throw std::invalid_argument("cannot open workload");
  std::string line;
  std::getline(input, line);
  if (!line.empty() && line.back() == '\r') line.pop_back();
  if (line != "seq,type,vector_ref,node_id,epoch") throw std::invalid_argument("invalid workload header");
  std::vector<Operation> result;
  while (std::getline(input, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    std::stringstream fields(line);
    std::vector<std::string> values;
    std::string field;
    while (std::getline(fields, field, ',')) values.push_back(field);
    if (values.size() != 5) throw std::invalid_argument("invalid workload row");
    const auto number = [](const std::string &text) {
      if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument("invalid workload integer");
      return std::stoull(text);
    };
    const auto id = number(values[3]);
    if (id > std::numeric_limits<rdma_hnsw::NodeId>::max()) throw std::invalid_argument("workload ID overflow");
    result.push_back({number(values[0]), number(values[2]), number(values[4]), values[1],
                      static_cast<rdma_hnsw::NodeId>(id)});
  }
  if (!input.eof()) throw std::runtime_error("workload read failed");
  return result;
}
} // namespace lab
