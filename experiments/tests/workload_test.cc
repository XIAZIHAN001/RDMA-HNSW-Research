#include "workload.h"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace lab;
void require(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
int main() {
 try {
  for (double ratio : {0.0, 0.1, 0.3, 0.5}) for (bool hot : {false, true}) {
    auto a = make_workload(200, ratio, 42, 1000, 7, 9, hot, 20);
    auto b = make_workload(200, ratio, 42, 1000, 7, 9, hot, 20);
    require(a.size() == 200 && a == b, "seeded workload must be complete and reproducible");
    const auto updates = std::count_if(a.begin(), a.end(), [](auto &op) { return op.type == "upsert"; });
    require(updates == std::lround(200 * ratio), "update ratio U/(Q+U)");
    for (std::size_t i = 0; i < a.size(); ++i) {
      require(a[i].seq == i && a[i].epoch == i / 20, "sequence and epochs");
      if (a[i].type == "upsert") require(a[i].node_id < (hot ? 10U : 1000U), "target distribution");
    }
    validate_workload(a, 1000, 7, 9);
  }
  for (double ratio : {-0.1, 1.1, std::numeric_limits<double>::quiet_NaN()}) {
    bool rejected = false;
    try { (void)make_workload(200, ratio, 42, 1000, 7, 9, false, 20); }
    catch (const std::invalid_argument &) { rejected = true; }
    require(rejected, "invalid update ratios rejected");
  }
  const std::vector<Operation> valid{{0, 0, 0, "insert", 3}, {1, 0, 0, "erase", 3},
                                     {2, 1, 1, "upsert", 3}, {3, 0, 1, "query", 0}};
  validate_workload(valid, 3, 2, 2);
  for (auto bad : {Operation{0, 0, 0, "insert", 2}, Operation{0, 0, 0, "erase", 3},
                   Operation{0, 2, 0, "query", 0}, Operation{1, 0, 0, "query", 0},
                   Operation{0, 0, 0, "bogus", 0}}) {
    bool rejected = false;
    try { validate_workload({bad}, 3, 2, 2); }
    catch (const std::invalid_argument &) { rejected = true; }
    require(rejected, "invalid workload rejected before any mutation");
  }
  std::cout << "PASS ratios, seed, targets, epochs, insert/erase and input validation\n";
  return 0;
 } catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
