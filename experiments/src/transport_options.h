#pragma once
#include "experiment_io.h"
#include "rdma_hnsw/runtime/compute_node.h"
#include <array>
namespace lab {
inline void configure_transport(const std::map<std::string,std::string> &args,rdma_hnsw::ComputeNodeConfig &config) {
 using namespace rdma_hnsw;
 const auto kind=args.contains("--transport")?args.at("--transport"):"mmap";
 if(kind=="mmap") {
  if(!args.contains("--index"))throw std::invalid_argument("--index is required for mmap");
  for(const auto *key:{"--host","--port","--ib-device","--ib-port","--rdma-connection","--rdma-timeout-ms"})
   if(args.contains(key))throw std::invalid_argument("RDMA connection options require --transport rdma");
  config.transport=TransportKind::Mmap;config.index_path=args.at("--index");return;
 }
 if(kind!="rdma")throw std::invalid_argument("transport must be mmap or rdma");
 if(args.contains("--index"))throw std::invalid_argument("RDMA must not use --index; the index resides on the storage server");
 if(!args.contains("--host")||args.at("--host").empty())throw std::invalid_argument("RDMA requires --host");
 config.transport=TransportKind::Rdma;config.index_path.clear();config.host=args.at("--host");
 const auto connection=args.contains("--rdma-connection")?args.at("--rdma-connection"):"cm";
 if(connection!="cm"&&connection!="ib")throw std::invalid_argument("rdma-connection must be cm or ib");
 config.rdma_connection=connection=="cm"?RdmaConnection::Cm:RdmaConnection::Ib;
 const auto value=[&](const char *key,std::size_t fallback,std::size_t maximum) {
  const auto number=args.contains(key)?nonnegative_number(args.at(key)):fallback;
  if(number==0||number>maximum)throw std::invalid_argument(std::string(key)+" is outside the supported range");
  return number;
 };
 config.port=value("--port",18515,65535);config.ib_port=value("--ib-port",1,255);
 config.rdma_timeout_ms=value("--rdma-timeout-ms",10000,600000);
 config.ib_device=args.contains("--ib-device")?args.at("--ib-device"):"";
}
inline const char *transport_name(const rdma_hnsw::ComputeNodeConfig &c){return c.transport==rdma_hnsw::TransportKind::Rdma?"rdma":"mmap";}
inline const char *connection_name(const rdma_hnsw::ComputeNodeConfig &c){return c.transport==rdma_hnsw::TransportKind::Mmap?"none":c.rdma_connection==rdma_hnsw::RdmaConnection::Cm?"cm":"ib";}
using TransportCounts=std::array<std::uint64_t,4>;
inline TransportCounts transport_counts(const rdma_hnsw::RemoteTransport &transport) {
 const auto &s=transport.stats();return {s.read_ops.load(),s.read_bytes.load(),s.write_ops.load(),s.write_bytes.load()};
}
inline TransportCounts transport_delta(const TransportCounts &after,const TransportCounts &before) {
 TransportCounts result{};for(std::size_t i=0;i<result.size();++i){if(after[i]<before[i])throw std::runtime_error("transport counter reset within operation");result[i]=after[i]-before[i];}return result;
}
inline void write_transport_counts(std::ostream &out,const TransportCounts &counts){for(const auto value:counts)out << ',' << value;}
}
