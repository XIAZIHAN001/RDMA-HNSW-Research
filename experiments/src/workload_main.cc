#include "experiment_io.h"
#include "workload.h"
#include <iostream>
#include <set>
int main(int argc, char **argv) {
 try {
  std::map<std::string,std::string> args;
  const std::set<std::string> allowed{"--out","--operations","--update-ratio","--seed","--nodes","--queries","--vectors","--epoch-size","--distribution"};
  for (int i=1;i<argc;++i) {
    const std::string key=argv[i];
    if(key=="--help") {std::cout << "lab_workload --out CSV --operations N --update-ratio R --nodes N --queries N --vectors N [--seed N] [--epoch-size N] [--distribution random|hotspot]\n";return 0;}
    if(!allowed.contains(key)||i+1>=argc)throw std::invalid_argument("unknown workload option");
    args[key]=argv[++i];
  }
  for(const auto *key:{"--out","--operations","--update-ratio","--nodes","--queries","--vectors"})
    if(!args.contains(key))throw std::invalid_argument(std::string(key)+" is required");
  std::size_t end{};
  const double ratio=std::stod(args.at("--update-ratio"),&end);
  if(end!=args.at("--update-ratio").size())throw std::invalid_argument("invalid update-ratio");
  const auto distribution=args.contains("--distribution")?args.at("--distribution"):"random";
  if(distribution!="random"&&distribution!="hotspot")throw std::invalid_argument("invalid distribution");
  const auto ops=lab::make_workload(lab::positive_number(args.at("--operations")),ratio,
    args.contains("--seed")?lab::nonnegative_number(args.at("--seed")):42,
    lab::positive_number(args.at("--nodes")),lab::positive_number(args.at("--queries")),
    lab::positive_number(args.at("--vectors")),distribution=="hotspot",
    args.contains("--epoch-size")?lab::positive_number(args.at("--epoch-size")):100);
  auto out=lab::output_file(args.at("--out"));
  out << "seq,type,vector_ref,node_id,epoch\n";
  for(const auto &op:ops)out << op.seq << ',' << op.type << ',' << op.vector_ref << ',' << op.node_id << ',' << op.epoch << '\n';
  out.flush();if(!out)throw std::runtime_error("workload write failed");
  std::cout << "operations=" << ops.size() << " seed=" << (args.contains("--seed")?args.at("--seed"):"42") << '\n';return 0;
 } catch(const std::exception &error) { std::cerr << "lab_workload: " << error.what() << '\n';return 1; }
}
