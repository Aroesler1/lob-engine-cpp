#pragma once

#include "analytics_record.h"

#include <filesystem>
#include <fstream>

class CsvExporter {
public:
    explicit CsvExporter(const std::filesystem::path& path);

    void write(const AnalyticsRecord& record);
    const std::filesystem::path& path() const;

private:
    void writeHeader();

    std::filesystem::path path_;
    std::ofstream output_;
};
