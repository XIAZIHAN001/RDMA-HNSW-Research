#include "read_strategies.h"
#include "rdma_hnsw/runtime/compute_node.h"
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
using namespace rdma_hnsw;
void require(bool value,const char *message){if(!value)throw std::runtime_error(message);}
int main(){
 const auto path=(std::filesystem::temp_directory_path()/
   ("hnsw_dynamic_"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())+".bin")).string();
 struct Cleanup {std::string path;~Cleanup(){std::filesystem::remove(path);std::filesystem::remove(path+".lock");}} cleanup{path};
 try {
  IndexHeader header{};header.dim=2;header.M=4;header.max_level=0;header.entrypoint=0;
  std::vector<NodeRecord> records(6);
  for(NodeId i=0;i<6;++i){records[i].id=i;records[i].vector={static_cast<float>(i),0};records[i].neighbors.resize(1);
    for(NodeId j=0;j<6;++j)if(j!=i)records[i].neighbors[0].push_back(j);}
  GraphStore::write(path,header,records);
  ComputeNodeConfig config;config.index_path=path;config.k=3;config.ef_search=32;config.cache_capacity=10000;
  ComputeNode fixed(config),cached(config);
  auto state=std::make_shared<lab::StrategyState>();
  const auto inject=[&]{state->valid_node_count=cached.query_engine().catalog().header.node_count;
    cached.query_engine().set_read_strategy(lab::make_lab_strategy(ReadPolicy::CachedLength,state));};
  inject();
  const Vector query{5.125F,0};
  require(fixed.query(query)==cached.query(query),"initial policy equivalence");
  require(state->lengths.size()==6,"warm hints populated");
  StorageEngine writer(path);
  const auto erased=writer.apply({update_op::Erase,5,{}});require(erased.ok,"erase successful");
  require(fixed.query_engine().catalog().header.generation==1 && fixed.query(query).front()==5,
          "old mmap snapshot remains stable until explicit reload");
  fixed.reload();cached.reload();inject();
  require(cached.query_engine().catalog().header.generation==erased.generation,"new catalog visible");
  require(cached.query(query)==fixed.query(query) && cached.query(query).front()==4,"deleted ID excluded after reload");
  require(cached.query_engine().stats().reads.cache_hits>0,"reload retained old history");
  require(writer.apply({update_op::Upsert,5,{5,0}}).ok,"upsert restores deleted node");
  fixed.reload();cached.reload();inject();
  require(fixed.query(query)==cached.query(query) && cached.query(query).front()==5,"restored vector and adjacency visible");
  require(cached.query_engine().stats().reads.fallbacks>0,"real degree growth repairs old hints");
  require(writer.apply({update_op::Insert,6,{6,0}}).ok,"insert successful");
  fixed.reload();cached.reload();inject();
  require(state->valid_node_count==7,"ID bound follows new snapshot");
  require(fixed.query({6.125F,0})==cached.query({6.125F,0}),"inserted snapshot complete neighbors");
  require(cached.query({6.125F,0}).front()==6,"inserted vector searchable");
  require(cached.query_engine().stats().reads.fallbacks>0,"insert degree growth repairs hints");
  std::cout << "PASS old/new snapshots, erase/upsert/insert, retained hints and repair\n";return 0;
 }catch(const std::exception &error){std::cerr << error.what() << '\n';return 1;}
}
