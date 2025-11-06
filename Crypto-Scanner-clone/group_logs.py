import argparse
import os
import re
from collections import defaultdict


def group_log_by_symbol(logfile: str, output: str | None = None) -> str:
    """Group debug log lines by symbol and write to output file."""
    symbol_re = re.compile(r"\[(\w+)\]")
    groups: defaultdict[str, list[str]] = defaultdict(list)

    with open(logfile, "r", encoding="utf-8") as f:
        for line in f:
            match = symbol_re.search(line)
            if match:
                symbol = match.group(1)
                # skip timestamp group - the first [ ] is timestamp
                if re.match(r"\d{4}-\d{2}-\d{2}", symbol):
                    # timestamp captured; look for next symbol
                    rest = line[match.end():]
                    m2 = symbol_re.search(rest)
                    symbol = m2.group(1) if m2 else "UNKNOWN"
            else:
                symbol = "UNKNOWN"
            groups[symbol].append(line.rstrip())

    if output is None:
        base, ext = os.path.splitext(logfile)
        output = f"{base}_grouped{ext}"

    with open(output, "w", encoding="utf-8") as f:
        for symbol in sorted(groups):
            f.write(f"### {symbol} ###\n")
            for row in groups[symbol]:
                f.write(row + "\n")
            f.write("\n")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Group kline debug logs by symbol")
    parser.add_argument("logfile", help="Path to scanlog.txt")
    parser.add_argument("-o", "--output", help="Output file path")
    args = parser.parse_args()

    output = group_log_by_symbol(args.logfile, args.output)
    print(f"Grouped log written to {output}")


if __name__ == "__main__":
    main()
