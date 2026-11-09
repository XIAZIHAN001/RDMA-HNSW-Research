#pragma once
#include "rdma_hnsw/common/data.h"
#include <cmath>
#include <filesystem>
#include <stdexcept>

namespace lab {
inline std::vector<rdma_hnsw::Vector> validated_vectors(const std::string &path,
                                                       std::size_t dimension) {
  auto vectors = rdma_hnsw::read_fvecs(path, dimension);
  const auto expected_bytes = vectors.size() * (sizeof(std::int32_t) + dimension * sizeof(float));
  if (std::filesystem::file_size(path) != expected_bytes)
    throw std::invalid_argument("truncated fvecs record or trailing bytes");
  for (const auto &vector : vectors)
    for (const auto value : vector)
      if (!std::isfinite(value))
        throw std::invalid_argument("fvecs contains non-finite coordinates");
  return vectors;
}
} // namespace lab
