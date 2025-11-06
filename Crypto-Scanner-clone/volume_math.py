"""Volume math module for calculating percentage volume change across kline blocks."""



def calculate_volume_change(klines: list, block_size: int) -> float:
    """Calculate % volume change for the latest block vs. previous 20 blocks."""
    try:
        sorted_klines = sorted(klines, key=lambda k: int(k[0]))

        blocks = [
            sorted_klines[i:i + block_size]
            for i in range(0, len(sorted_klines) - (block_size - 1), block_size)
            if len(sorted_klines[i:i + block_size]) == block_size
        ]
        if len(blocks) < 21:
            return 0.0

        latest_block = blocks[-1]
        previous_blocks = blocks[-21:-1]

        sum_latest = sum(float(k[5]) for k in latest_block)
        avg_previous = sum(
            sum(float(k[5]) for k in block) for block in previous_blocks
        ) / len(previous_blocks)

        if avg_previous == 0:
            return 0.0

        return ((sum_latest - avg_previous) / avg_previous) * 100
    except (ValueError, IndexError, TypeError):
        return 0.0
