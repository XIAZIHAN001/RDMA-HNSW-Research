#include "transport_options.h"
#include <iostream>
#include <functional>
using namespace rdma_hnsw;
void require(bool ok){if(!ok)throw std::runtime_error("transport configuration mismatch");}
int main(){try{
 ComputeNodeConfig c;lab::configure_transport({{"--index","local.bin"}},c);require(c.transport==TransportKind::Mmap&&c.index_path=="local.bin");
 lab::configure_transport({{"--transport","rdma"},{"--host","10.1.2.3"},{"--port","18519"},{"--ib-device","erdma_0"},{"--ib-port","2"},{"--rdma-timeout-ms","1234"}},c);
 require(c.transport==TransportKind::Rdma&&c.rdma_connection==RdmaConnection::Cm&&c.index_path.empty()&&c.host=="10.1.2.3"&&c.port==18519&&c.ib_device=="erdma_0"&&c.ib_port==2&&c.rdma_timeout_ms==1234);
 lab::configure_transport({{"--transport","rdma"},{"--host","10.1.2.3"},{"--rdma-connection","ib"}},c);require(c.rdma_connection==RdmaConnection::Ib);
 for(const auto &args:std::vector<std::map<std::string,std::string>>{{},{ {"--index","x"},{"--host","10.1.2.3"} },{{"--transport","tcp"}},{{"--transport","rdma"}},{{"--transport","rdma"},{"--host",""}},{{"--transport","rdma"},{"--host","x"},{"--index","local.bin"}},{{"--transport","rdma"},{"--host","x"},{"--port","65536"}},{{"--transport","rdma"},{"--host","x"},{"--rdma-connection","bad"}},{{"--transport","rdma"},{"--host","x"},{"--rdma-timeout-ms","600001"}}}){
  bool rejected=false;try{lab::configure_transport(args,c);}catch(const std::exception&){rejected=true;}require(rejected);
 }
 std::cout<<"transport configuration cases passed\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
