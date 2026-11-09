#pragma once
#include "rdma_hnsw/runtime/compute_node.h"
#include <cmath>
#include <cstring>
#include <limits>
namespace lab {
inline std::vector<rdma_hnsw::NodeRecord> remote_snapshot(const rdma_hnsw::IndexCatalog &catalog,rdma_hnsw::RemoteTransport &transport) {
 using namespace rdma_hnsw;
 if(catalog.offsets.size()!=catalog.header.node_count||catalog.header.dim==0||catalog.header.M==0)
  throw std::runtime_error("invalid remote snapshot catalog");
 const auto read=[&](Offset offset,std::size_t size) {
  if(size==0||offset>std::numeric_limits<Offset>::max()-size)throw std::runtime_error("remote snapshot range overflow");
  Bytes bytes(size);const auto result=transport.read({offset,0},bytes);
  if(!result.ok||result.bytes!=size)throw std::runtime_error("remote snapshot read failed or was short: "+result.error);
  return bytes;
 };
 if(catalog.header.dim>(std::numeric_limits<std::size_t>::max()-sizeof(NodeDiskHeader))/sizeof(float))throw std::runtime_error("remote vector size overflow");
 std::vector<NodeRecord> nodes;nodes.reserve(catalog.offsets.size());
 for(std::size_t i=0;i<catalog.offsets.size();++i) {
  auto bytes=read(catalog.offsets[i],sizeof(NodeDiskHeader)+catalog.header.dim*sizeof(float));NodeDiskHeader h{};
  std::memcpy(&h,bytes.data(),sizeof(h));
  if(h.id!=i||h.level>catalog.header.max_level||h.deleted>1)throw std::runtime_error("invalid remote node header");
  NodeRecord n;n.id=h.id;n.generation=h.generation;n.level=h.level;n.deleted=h.deleted!=0;n.vector.resize(catalog.header.dim);
  std::memcpy(n.vector.data(),bytes.data()+sizeof(h),n.vector.size()*sizeof(float));
  for(float x:n.vector)if(!std::isfinite(x))throw std::runtime_error("non-finite remote vector");
  n.neighbors.resize(static_cast<std::size_t>(h.level)+1);
  for(std::uint32_t level=0;level<=h.level;++level) {
   const auto capacity=catalog.layer_capacity(level);
   if(capacity>(std::numeric_limits<std::size_t>::max()-sizeof(std::uint32_t))/sizeof(NodeId))throw std::runtime_error("remote layer size overflow");
   bytes=read(node_layer_offset(catalog,static_cast<NodeId>(i),level),sizeof(std::uint32_t)+capacity*sizeof(NodeId));
   std::uint32_t degree{};std::memcpy(&degree,bytes.data(),sizeof(degree));
   if(degree>capacity)throw std::runtime_error("remote degree exceeds capacity");
   auto &ids=n.neighbors[level];ids.resize(degree);if(degree)std::memcpy(ids.data(),bytes.data()+sizeof(degree),degree*sizeof(NodeId));
   for(auto id:ids)if(id>=catalog.header.node_count)throw std::runtime_error("invalid remote neighbor id");
  }
  nodes.push_back(std::move(n));
 }
 return nodes;
}
}
