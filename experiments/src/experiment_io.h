#pragma once
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <initializer_list>
#include <map>
#include <stdexcept>
#include <string>

namespace lab {
inline std::size_t positive_number(const std::string &text) {
  std::size_t end{};
  const auto value = std::stoull(text, &end);
  if (text.empty() || text.front() == '-' || end != text.size() || value == 0)
    throw std::invalid_argument("expected a positive integer");
  return value;
}
inline std::size_t nonnegative_number(const std::string &text) {
  if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
    throw std::invalid_argument("expected a nonnegative integer");
  return std::stoull(text);
}
inline std::ofstream output_file(const std::string &path) {
  const auto parent = std::filesystem::path(path).parent_path();
  if (!parent.empty()) std::filesystem::create_directories(parent);
  std::ofstream output(path);
  if (!output) throw std::runtime_error("cannot open output: " + path);
  output << std::setprecision(12);
  return output;
}
inline bool same_file(const std::string &a, const std::string &b) {
  namespace fs = std::filesystem;
  return fs::weakly_canonical(a) == fs::weakly_canonical(b) ||
      (fs::exists(a) && fs::exists(b) && fs::equivalent(a, b));
}
inline void check_outputs(const std::map<std::string, std::string> &args,
                          std::initializer_list<const char *> inputs,
                          std::initializer_list<const char *> outputs) {
  for (const auto *output : outputs) {
    if (!args.contains(output)) continue;
    for (const auto *input : inputs)
      if (args.contains(input) && same_file(args.at(output), args.at(input)))
        throw std::invalid_argument("output path collision: " + args.at(output));
    for (const auto *other : outputs)
      if (std::string(output) != other && args.contains(other) &&
          same_file(args.at(output), args.at(other)))
        throw std::invalid_argument("output path collision: " + args.at(output));
  }
}
} // namespace lab
