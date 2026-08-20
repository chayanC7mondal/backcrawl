from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    stats: dict[str, float]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    renamed = {column: column.strip().lower() for column in df.columns}
    df = df.rename(columns=renamed).copy()

    if "date" in df.columns and "datetime" not in df.columns:
        df = df.rename(columns={"date": "datetime"})

    if "datetime" not in df.columns:
        raise ValueError("Input data must contain a 'date' or 'datetime' column.")

    if "close" not in df.columns:
        raise ValueError("Input data must contain a 'close' column.")

    df["datetime"] = pd.to_datetime(df["datetime"], utc=False, errors="coerce")
    df = df.dropna(subset=["datetime", "close"]).sort_values("datetime")
    return df.reset_index(drop=True)


def load_upload(uploaded_file) -> pd.DataFrame:
    raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    return normalize_columns(pd.read_csv(StringIO(raw_text)))


def load_yfinance_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, start=start_date, end=end_date, progress=False)
    if df.empty:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()
    df.columns = [column.lower() for column in df.columns]
    if "date" in df.columns:
        df = df.rename(columns={"date": "datetime"})

    df["ticker"] = ticker.upper()
    return normalize_columns(df)


def compute_sharpe_ratio(returns: pd.Series) -> float:
    if len(returns) < 2 or returns.std(ddof=0) == 0:
        return 0.0
    return float(np.sqrt(252) * returns.mean() / returns.std(ddof=0))


def compute_max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    high_water_mark = equity.cummax()
    drawdown = equity / high_water_mark - 1.0
    return float(drawdown.min())


def run_mean_reversion_backtest(
    prices: pd.DataFrame,
    lookback_period: int,
    z_threshold: float,
    initial_capital: float,
    position_fraction: float,
) -> BacktestResult:
    data = prices.copy()
    data["datetime"] = pd.to_datetime(data["datetime"])
    data = data.sort_values("datetime").reset_index(drop=True)

    closes = data["close"].astype(float).reset_index(drop=True)
    dates = data["datetime"].reset_index(drop=True)

    cash = float(initial_capital)
    position = 0.0
    entry_price = np.nan
    records: list[dict[str, float | pd.Timestamp]] = []
    trades: list[dict[str, float | pd.Timestamp | str]] = []

    for index in range(len(data)):
        price = float(closes.iloc[index])
        timestamp = dates.iloc[index]

        if index >= lookback_period:
            window = closes.iloc[index - lookback_period : index]
            mean = float(window.mean())
            std = float(window.std(ddof=1))
            z_score = 0.0 if std == 0 else (price - mean) / std
        else:
            z_score = np.nan

        if not np.isnan(z_score):
            if position == 0 and z_score <= -z_threshold:
                quantity = max(0.0, np.floor((cash * position_fraction) / price))
                if quantity > 0:
                    cash -= quantity * price
                    position += quantity
                    entry_price = price
                    trades.append(
                        {
                            "datetime": timestamp,
                            "action": "BUY",
                            "price": price,
                            "quantity": quantity,
                            "z_score": z_score,
                        }
                    )
            elif position > 0 and z_score >= 0:
                cash += position * price
                trades.append(
                    {
                        "datetime": timestamp,
                        "action": "SELL",
                        "price": price,
                        "quantity": position,
                        "z_score": z_score,
                    }
                )
                position = 0.0
                entry_price = np.nan

        equity = cash + position * price
        records.append(
            {
                "datetime": timestamp,
                "close": price,
                "position": position,
                "cash": cash,
                "total": equity,
                "returns": 0.0,
                "z_score": z_score,
                "entry_price": entry_price,
            }
        )

    equity_df = pd.DataFrame(records).set_index("datetime")
    equity_df["returns"] = equity_df["total"].pct_change().fillna(0.0)
    equity_df["equity_curve"] = (1.0 + equity_df["returns"]).cumprod()

    trades_df = pd.DataFrame(trades)
    total_return = float(equity_df["total"].iloc[-1] / equity_df["total"].iloc[0] - 1.0)
    stats = {
        "Total Return": total_return,
        "Max Drawdown": compute_max_drawdown(equity_df["total"]),
        "Sharpe Ratio": compute_sharpe_ratio(equity_df["returns"]),
        "Final Equity": float(equity_df["total"].iloc[-1]),
        "Trades": float(len(trades_df)),
    }

    return BacktestResult(equity=equity_df, trades=trades_df, stats=stats)


def infer_tickers(df: pd.DataFrame) -> list[str]:
    if "ticker" not in df.columns:
        return ["SINGLE"]
    tickers = sorted(df["ticker"].dropna().astype(str).str.upper().unique().tolist())
    return tickers or ["SINGLE"]


def render_equity_chart(equity: pd.DataFrame):
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=equity.index,
            y=equity["total"],
            name="Equity",
            line=dict(color="#2563eb", width=2),
        )
    )
    fig.update_layout(
        title="Equity Curve",
        height=420,
        margin=dict(l=20, r=20, t=50, b=20),
        template="plotly_white",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_drawdown_chart(equity: pd.DataFrame):
    drawdown = equity["total"] / equity["total"].cummax() - 1.0
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=equity.index,
            y=drawdown * 100.0,
            name="Drawdown %",
            fill="tozeroy",
            line=dict(color="#dc2626", width=2),
        )
    )
    fig.update_layout(
        title="Drawdown",
        height=320,
        margin=dict(l=20, r=20, t=50, b=20),
        template="plotly_white",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_price_chart(data: pd.DataFrame):
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=data["datetime"],
            y=data["close"],
            name="Close",
            line=dict(color="#0f766e", width=2),
        )
    )
    fig.update_layout(
        title="Price Data",
        height=320,
        margin=dict(l=20, r=20, t=50, b=20),
        template="plotly_white",
    )
    st.plotly_chart(fig, use_container_width=True)


def main():
    st.set_page_config(page_title="Backcrawl Backtest Lab", layout="wide")
    st.title("Backcrawl Backtest Lab")
    st.caption("Upload new market data or pull it from Yahoo Finance, then run a mean-reversion test immediately.")

    today = date.today()
    default_start = today - timedelta(days=365)

    with st.sidebar:
        st.header("Data Source")
        source = st.radio("Choose input", ["Upload CSV", "YFinance"], index=0)
        st.header("Strategy")
        lookback_period = st.slider("Lookback period", min_value=5, max_value=200, value=20, step=1)
        z_threshold = st.slider("Z-score threshold", min_value=0.5, max_value=4.0, value=1.5, step=0.1)
        position_fraction = st.slider("Capital allocation", min_value=0.05, max_value=1.0, value=0.25, step=0.05)
        initial_capital = st.number_input("Initial capital", min_value=1000.0, value=100000.0, step=1000.0)

        uploaded_file = None
        ticker = "AAPL"
        start_date = default_start.isoformat()
        end_date = today.isoformat()

        if source == "Upload CSV":
            uploaded_file = st.file_uploader("CSV with date/datetime and close columns", type=["csv"])
        else:
            ticker = st.text_input("Ticker", value="AAPL").upper().strip()
            start_date = st.date_input("Start date", value=default_start).isoformat()
            end_date = st.date_input("End date", value=today).isoformat()

    data = None
    error_message = None

    try:
        if source == "Upload CSV" and uploaded_file is not None:
            data = load_upload(uploaded_file)
        elif source == "YFinance" and ticker:
            data = load_yfinance_data(ticker, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        error_message = str(exc)

    if error_message:
        st.error(error_message)
    elif data is None or data.empty:
        st.info("Provide a CSV or a ticker to load data.")
    else:
        tickers = infer_tickers(data)
        if "ticker" in data.columns and len(tickers) > 1:
            selected_ticker = st.selectbox("Ticker", tickers, index=0)
            data = data[data["ticker"].astype(str).str.upper() == selected_ticker].copy()

        if data.empty:
            st.warning("No rows left after filtering the selected ticker.")
        else:
            result = run_mean_reversion_backtest(
                data,
                lookback_period=lookback_period,
                z_threshold=z_threshold,
                initial_capital=initial_capital,
                position_fraction=position_fraction,
            )

            metric_cols = st.columns(5)
            metric_cols[0].metric("Final Equity", f"${result.stats['Final Equity']:,.2f}")
            metric_cols[1].metric("Total Return", f"{result.stats['Total Return'] * 100:.2f}%")
            metric_cols[2].metric("Max Drawdown", f"{result.stats['Max Drawdown'] * 100:.2f}%")
            metric_cols[3].metric("Sharpe Ratio", f"{result.stats['Sharpe Ratio']:.2f}")
            metric_cols[4].metric("Trades", f"{int(result.stats['Trades'])}")

            left, right = st.columns([1.4, 1.0])
            with left:
                render_equity_chart(result.equity)
                render_drawdown_chart(result.equity)
            with right:
                render_price_chart(data)
                st.subheader("Trade Log")
                if result.trades.empty:
                    st.write("No trades were generated for the selected data and parameters.")
                else:
                    st.dataframe(result.trades, use_container_width=True, hide_index=True)

            st.subheader("Equity Series")
            st.dataframe(result.equity.tail(100), use_container_width=True)

            st.subheader("Suggested Modifications")
            suggestions: Iterable[str] = [
                "Add transaction costs and slippage to match the notebook's simulated execution more closely.",
                "Make the strategy selector configurable so you can switch between mean reversion and momentum from the UI.",
                "Export the results DataFrame to CSV for comparison runs.",
                "Validate that the uploaded file has a sorted datetime index and no missing close values before running.",
            ]
            for suggestion in suggestions:
                st.write(f"- {suggestion}")


if __name__ == "__main__":
    main()
