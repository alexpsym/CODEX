"""Interactive application to display live IV bands."""

# pylint: disable=invalid-name,global-statement
from datetime import datetime, timezone
import os
import re
import threading

import matplotlib
matplotlib.use('TkAgg')  # pylint: disable=wrong-import-position
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.animation import FuncAnimation

from ivlog import get_logger
from ivcore import (
    LOCAL_TZ,
    fetch_spot_price,
    fetch_options,
    select_nearest_expiry_group,
    compute_skew,
    compute_volumes,
    compute_open_interest,
    update_scaled_iv,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
logger = get_logger(__name__)

TIMEFRAME_KEYS = {
    "1": "1m", "2": "5m", "3": "15m", "4": "30m",
    "5": "1h", "6": "4h", "7": "1d", "8": "1w", "9": "1mo",
}

current_symbol = "BTCUSDT"
current_expiry = None  # None uses nearest expiry
current_timeframe = "1h"
current_metrics = {}

x_data, spot_data, upper_data, lower_data = [], [], [], []
fig = ax = line_upper = line_lower = line_spot = None
texts = []
hotkey_table = None


def reset_axes():
    """Reset axis limits when switching symbols."""
    if ax:
        ax.set_ylim(None, None)
        ax.relim()
        ax.autoscale_view()


def on_key(event):
    """Handle keyboard input for timeframe and symbol."""
    global current_timeframe

    key = event.key
    if key in TIMEFRAME_KEYS:
        current_timeframe = TIMEFRAME_KEYS[key]
        logger.info("Timeframe switched to: %s", current_timeframe)
        return

    if key == "enter":
        update_title(current_symbol)
        return


def update_title(symbol):
    """Update plot title when symbol changes."""
    ax.set_title(f"Live IV Bands ({symbol})")
    logger.info("Symbol switched to: %s", symbol)


def update(_):
    """Refresh plot data."""
    spot = fetch_spot_price(current_symbol)
    if spot is None:
        logger.warning("Spot fetch failed.")
        return

    options = fetch_options(current_symbol)
    group = select_nearest_expiry_group(options, current_expiry)
    if not group:
        logger.warning("No options found for symbol %s", current_symbol)
        return

    scaled_iv = update_scaled_iv(current_timeframe)
    move = spot * scaled_iv
    now = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    expiry_dt = group[0]['expiry'].astimezone(LOCAL_TZ)
    expiry_str = expiry_dt.strftime("%Y-%m-%d")

    x_data.append(now)
    spot_data.append(spot)
    upper_data.append(spot + move)
    lower_data.append(spot - move)

    line_spot.set_data(x_data, spot_data)
    line_upper.set_data(x_data, upper_data)
    line_lower.set_data(x_data, lower_data)
    ax.relim()
    ax.autoscale_view()

    texts[0].set_text(f"TF: {current_timeframe} | IV: {scaled_iv*100:.2f}%")
    texts[1].set_text(f"Spot: {spot:.2f}")
    texts[2].set_text(f"+1σ: {spot+move:.2f} / -1σ: {spot-move:.2f}")
    skew = compute_skew(group)
    texts[3].set_text(f"25Δ Skew: {skew:.2f}%" if skew else "25Δ Skew: n/a")
    call_vol, put_vol = compute_volumes(group)
    texts[4].set_text(f"Call Vol: {call_vol:,}")
    texts[5].set_text(f"Put Vol: {put_vol:,}")
    call_oi, put_oi = compute_open_interest(group)
    texts[6].set_text(f"Call OI: {call_oi:,}")
    texts[7].set_text(f"Put OI: {put_oi:,}")
    texts[8].set_text(f"Move: {move:.2f} USDT")
    texts[9].set_text(f"Expiry: {expiry_str}")

    current_metrics.update({
        "Time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "Timeframe": current_timeframe,
        "IV": scaled_iv * 100,
        "Spot": spot,
        "Upper": spot + move,
        "Lower": spot - move,
        "Skew": skew,
        "CallVol": call_vol,
        "PutVol": put_vol,
        "CallOI": call_oi,
        "PutOI": put_oi,
        "Move": move,
        "Expiry": expiry_str,
    })


def save_metrics():
    """Write current metrics to a file and save a chart screenshot."""
    if not current_metrics or fig is None:
        return

    timestamp = current_metrics["Time"].replace(" ", "_").replace(":", "-")
    base_name = f"iv_metrics_{timestamp}"

    txt_path = os.path.join(SCRIPT_DIR, f"{base_name}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        for key, value in current_metrics.items():
            f.write(f"{key}: {value}\n")

    img_path = os.path.join(SCRIPT_DIR, f"{base_name}.png")
    fig.savefig(img_path, bbox_inches="tight")

    logger.info("Metrics saved to %s", txt_path)
    logger.info("Chart screenshot saved to %s", img_path)


def symbol_input_listener():
    """Listen for symbol or expiry changes on stdin."""
    global current_symbol, current_expiry
    while True:
        raw_input = input("Enter symbol (BTCUSDT/ETHUSDT/SOLUSDT) or expiry (yy-mm-dd) or 'log': ").strip()
        if raw_input.lower() == "log":
            save_metrics()
            continue
        if re.fullmatch(r"\d{2}-\d{2}-\d{2}", raw_input):
            try:
                current_expiry = datetime.strptime(raw_input, "%y-%m-%d").replace(tzinfo=timezone.utc)
                logger.info("Expiry switched to: %s", raw_input)
            except ValueError:
                logger.warning("Invalid expiry format: %s", raw_input)
            continue
        if raw_input.upper() in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}:
            current_symbol = raw_input.upper()
            plt.close(fig)
        else:
            logger.warning("Invalid input: %s", raw_input)


def init_plot():
    """Create a fresh plot for the current symbol."""
    global fig, ax, line_upper, line_lower, line_spot, texts, hotkey_table

    fig, ax = plt.subplots()
    fig.set_size_inches(14, 8)
    plt.subplots_adjust(right=0.75)
    plt.style.use("ggplot")

    line_upper, = ax.plot([], [], "k-", label="+1σ")
    line_lower, = ax.plot([], [], "k-", label="-1σ")
    line_spot, = ax.plot([], [], "b--", label="Spot")

    texts = [
        ax.text(1.01, y, "", transform=ax.transAxes, fontsize=9, verticalalignment="top")
        for y in [0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45, 0.40]
    ]

    hotkey_table = ax.table(
        cellText=[[k, v] for k, v in TIMEFRAME_KEYS.items()],
        colLabels=["Key", "TF"],
        cellLoc="center",
        bbox=[1.01, 0.0, 0.15, 0.35],
    )
    hotkey_table.auto_set_font_size(False)
    hotkey_table.set_fontsize(8)

    ax.set_ylabel("Price (USDT)")
    ax.set_xlabel("Time (Brisbane)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%I:%M %p', tz=LOCAL_TZ))
    update_title(current_symbol)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.02), fontsize="small", frameon=True)

    fig.canvas.mpl_connect("key_press_event", on_key)
    ani = FuncAnimation(fig, update, interval=4000, cache_frame_data=False)
    globals()["_ani"] = ani  # prevent lint warning about unused variable


def main():
    """Start the interactive plot loop."""
    threading.Thread(target=symbol_input_listener, daemon=True).start()
    try:
        while True:
            x_data.clear()
            spot_data.clear()
            upper_data.clear()
            lower_data.clear()
            init_plot()
            plt.show()
    except KeyboardInterrupt:
        plt.close("all")
        logger.info("Exiting application.")


if __name__ == "__main__":
    main()
