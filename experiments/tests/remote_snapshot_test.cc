#include "remote_snapshot.h"
#include <cstring>
#include <functional>
#include <iostream>
using namespace rdma_hnsw;
struct MemoryTransport:RemoteTransport {
 Bytes memory=Bytes(184);TransportStats counters;bool false_full=false,short_read=false;
 ReadResult read(RemoteAddress a,std::span<byte> b) override {
  if(a.offset>memory.size()||b.size()>memory.size()-a.offset)return {false,0,"outside"};
  std::memcpy(b.data(),memory.data()+a.offset,b.size());++counters.read_ops;counters.read_bytes+=b.size();
  return {!false_full,b.size()-(short_read?1:0),"injected"};
 }
 WriteResult write(RemoteAddress,std::span<const byte>) override{return {};}
 UpdateResult submit_update(const UpdateRequest&) override{return {};}
 const TransportStats &stats()const override{return counters;}
 template<typename T>void put(std::size_t off,const T&x){std::memcpy(memory.data()+off,&x,sizeof(x));}
};
void require(bool b){if(!b)throw std::runtime_error("remote snapshot mismatch");}
int main(){try{
 IndexCatalog c;c.path="MUST_NOT_OPEN_LOCAL_FILE";c.header.dim=2;c.header.M=2;c.header.max_level=0;c.header.node_count=2;c.header.generation=1;c.offsets={80,132};
 MemoryTransport t;
 for(NodeId i=0;i<2;++i){NodeDiskHeader h{};h.id=i;h.generation=1;h.level=0;h.deleted=i;auto off=c.offsets[i];t.put(off,h);float x=static_cast<float>(i);t.put(off+24,x);t.put(off+28,x);std::uint32_t d=1;t.put(off+32,d);NodeId id=1-i;t.put(off+36,id);}
 auto nodes=lab::remote_snapshot(c,t);require(nodes.size()==2&&nodes[0].id==0&&nodes[1].deleted&&nodes[0].vector==Vector({0,0})&&nodes[1].vector==Vector({1,1})&&nodes[0].neighbors[0]==std::vector<NodeId>({1})&&nodes[1].neighbors[0]==std::vector<NodeId>({0}));require(t.counters.read_ops==4);
 for(int failure=0;failure<4;++failure){t.false_full=failure==0;t.short_read=failure==1;std::uint32_t degree=failure==2?5:1;t.put(112,degree);NodeId id=failure==3?2:1;t.put(116,id);bool rejected=false;try{lab::remote_snapshot(c,t);}catch(const std::exception&){rejected=true;}require(rejected);}
 std::cout<<"remote-only snapshot and failure cases passed\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
