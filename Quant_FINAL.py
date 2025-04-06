import streamlit as st
import plotly.graph_objs as go
import pandas as pd
import ccxt
import requests
import google.generativeai as genai
import threading
import time
from datetime import datetime, timedelta

# Trading Parameters
ORDER_SIZE = 0.001
INITIAL_BALANCE = 1000000
EXCHANGES = ['binance', 'kucoin', 'bybit']
AUTO_TRADE_MIN_INTERVAL = 5  # Minimum seconds between trades
AUTO_TRADE_MAX_POSITIONS = 5  # Maximum number of concurrent positions

# API Configuration (Replace with your actual keys)
NEWS_API_KEY = "614e0cd03f9c45a2a2cd9e13aefe80e7"
GEMINI_API_KEY = "AIzaSyBk7Zhi2wdgVXXgR_KBnuXp9dW5RKmP1Lo"

# Custom CSS for dark theme
st.set_page_config(page_title="Crypto Trading Dashboard", layout="wide")

st.markdown("""
<style>
.stApp {
    background-color: #0E1117;
    color: white;
}
.stButton>button {
    color: white;
    background-color: #262730;
    border: none;
}
.stButton>button:hover {
    color: white;
    background-color: #3a3d46;
}
.stCard {
    background-color: #262730;
    border: none;
}
.big-font {
    font-size: 28px !important;
    font-weight: bold !important;
}
.medium-font {
    font-size: 22px !important;
    font-weight: bold !important;
}
.highlight-value {
    background-color: #2E3440;
    padding: 10px;
    border-radius: 5px;
    margin: 5px 0;
}
.sentiment-positive {
    background-color: rgba(0, 128, 0, 0.2);
    padding: 10px;
    border-radius: 5px;
    font-weight: bold;
}
.sentiment-negative {
    background-color: rgba(220, 20, 60, 0.2);
    color: #FF6961;
    padding: 10px;
    border-radius: 5px;
    font-weight: bold;
}
.sentiment-neutral {
    background-color: rgba(128, 128, 128, 0.2);
    color: #D3D3D3;
    padding: 10px;
    border-radius: 5px;
    font-weight: bold;
}
.buy-button {
    background-color: #4CAF50 !important;
    color: white !important;
    font-weight: bold !important;
    height: 50px !important;
}
.sell-button {
    background-color: #f44336 !important;
    color: white !important;
    font-weight: bold !important;
    height: 50px !important;
}
.section-divider {
    margin-top: 30px;
    margin-bottom: 30px;
    border-top: 1px solid #3a3d46;
}
</style>
""", unsafe_allow_html=True)

# Initialize trade confirmation flags if not already present
if 'trade_executed' not in st.session_state:
    st.session_state.trade_executed = False

# JS for trade confirmation popup
def inject_popup_js():
    st.markdown("""
    <script>
        // Function to create and show popup
        function showTradePopup(tradeType, symbol, price, amount) {
            // Create popup container
            const popup = document.createElement('div');
            popup.className = 'popup-container';

            // Create popup content
            const content = document.createElement('div');
            content.className = 'popup-content';

            // Create header
            const header = document.createElement('div');
            header.className = 'popup-header';
            header.textContent = tradeType + ' Order Confirmation';

            // Create message
            const message = document.createElement('div');
            message.innerHTML = `
                <p>Are you sure you want to ${tradeType.toLowerCase()} ${amount} ${symbol} at $${price}?</p>
                <p>Total: $${(price * amount).toFixed(2)}</p>
            `;

            // Create buttons
            const buttons = document.createElement('div');
            buttons.className = 'popup-buttons';

            const confirmBtn = document.createElement('button');
            confirmBtn.className = 'popup-confirm';
            confirmBtn.textContent = 'Confirm';
            confirmBtn.onclick = function() {
                // Submit the corresponding form to execute the trade
                document.getElementById(tradeType.toLowerCase() + '-form').submit();
                document.body.removeChild(popup);
            };

            const cancelBtn = document.createElement('button');
            cancelBtn.className = 'popup-cancel';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.onclick = function() {
                document.body.removeChild(popup);
            };

            // Assemble popup
            buttons.appendChild(confirmBtn);
            buttons.appendChild(cancelBtn);
            content.appendChild(header);
            content.appendChild(message);
            content.appendChild(buttons);
            popup.appendChild(content);

            // Add to body
            document.body.appendChild(popup);
        }
    </script>
    """, unsafe_allow_html=True)

# Fetch Current Price
@st.cache_data(ttl=10)
def fetch_current_bhaav(symbol):
    try:
        exchange = ccxt.binance()
        ticker = exchange.fetch_ticker(symbol)
        current_bhaav = ticker['last']
        return current_bhaav
    except Exception as e:
        st.error(f"Error fetching current price: {e}")
        return None

# Fetch Available Symbols for an Exchange
@st.cache_data(ttl=3600)
def fetch_available_symbols(exchange_id):
    try:
        # Initialize exchange
        exchange_class = getattr(ccxt, exchange_id)
        exchange = exchange_class({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'  # Use futures market
            }
        })

        # Load markets
        exchange.load_markets()

        # Filter USDT pairs
        usdt_pairs = [symbol for symbol in exchange.markets.keys() if symbol.endswith('/USDT')]

        return sorted(usdt_pairs)
    except Exception as e:
        st.error(f"Error fetching symbols for {exchange_id}: {e}")
        return []

# Fetch Live Market Data
@st.cache_data(ttl=5)  # 5 seconds for auto-update
def fetch_live_data(symbol, exchange_id='binance', timeframe='1h'):
    try:
        # Initialize exchange
        exchange_class = getattr(ccxt, exchange_id)
        exchange = exchange_class({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'  # Use futures market
            }
        })

        # Fetch OHLCV data
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe)

        # Convert to DataFrame
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        return df
    except Exception as e:
        st.error(f"Error fetching data from {exchange_id}: {e}")
        return None

# Fetch Cryptocurrency News
@st.cache_data(ttl=300)  # 5 minutes
def fetch_crypto_news(symbol, limit=10):
    try:
        # Extract base cryptocurrency (e.g., BTC from BTC/USDT)
        base_currency = symbol.split('/')[0]

        url = "https://newsapi.org/v2/everything"
        params = {
            "apiKey": NEWS_API_KEY,
            "q": f"{base_currency} cryptocurrency",
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": limit
        }

        response = requests.get(url, params=params)
        news_data = response.json()

        if news_data.get('status') == 'ok':
            articles = news_data.get('articles', [])
            return articles
        else:
            st.warning("Could not fetch news articles")
            return []
    except Exception as e:
        st.error(f"Error fetching news: {e}")
        return []

# Compute Technical Indicators
def compute_technical_indicators(df):
    try:
        # RSI Calculation
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        # MACD Calculation
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=9, adjust=False).mean()

        # Bollinger Bands
        middle_band = df['close'].rolling(window=20).mean()
        std_dev = df['close'].rolling(window=20).std()
        upper_band = middle_band + (std_dev * 2)
        lower_band = middle_band - (std_dev * 2)

        return {
            'rsi': rsi.tolist(),  # Ensure rsi is a list
            'macd': macd.tolist(),  # Ensure macd is a list
            'signal_line': signal_line.tolist(),  # Ensure signal_line is a list
            'upper_band': upper_band.tolist(),  # Ensure upper_band is a list
            'lower_band': lower_band.tolist()  # Ensure lower_band is a list
        }
    except Exception as e:
        st.error(f"Error computing technical indicators: {e}")
        # Return default values if calculation fails
        return {
            'rsi': [50.0],
            'macd': [0.0],
            'signal_line': [0.0],
            'upper_band': [df['close'].iloc[-1] * 1.02] if not df.empty else [0],
            'lower_band': [df['close'].iloc[-1] * 0.98] if not df.empty else [0]
        }

# Extract sentiment details
def extract_sentiment_details(sentiment_text):
    # Default values
    sentiment_type = "NEUTRAL"
    sentiment_score = 0

    try:
        # Extract Overall Market Sentiment
        if "Overall Market Sentiment:" in sentiment_text:
            sentiment_line = sentiment_text.split("Overall Market Sentiment:")[1].split("\n")[0].strip()
            if "BULLISH" in sentiment_line.upper():
                sentiment_type = "BULLISH"
            elif "BEARISH" in sentiment_line.upper():
                sentiment_type = "BEARISH"
            else:
                sentiment_type = "NEUTRAL"

        # Extract Sentiment Score
        if "Sentiment Score:" in sentiment_text:
            score_line = sentiment_text.split("Sentiment Score:")[1].split("\n")[0].strip()
            score_parts = score_line.split()
            for part in score_parts:
                try:
                    sentiment_score = float(part)
                    break
                except ValueError:
                    continue
    except Exception:
        pass

    return sentiment_type, sentiment_score

# Analyze News Sentiment with Gemini
@st.cache_data(ttl=300)  # 5 minutes
def analyze_news_sentiment(articles, current):
    try:
        # Configure Gemini API
        genai.configure(api_key=GEMINI_API_KEY)

        # Prepare news content for analysis (limit to 5 articles to avoid token limits)
        news_content = "\n\n".join([
            f"Title: {article['title']}\nDescription: {article.get('description', '')}"
            for article in articles[:5]
        ])

        # Create prompt for sentiment analysis
        prompt = f"""Analyze the sentiment of the following cryptocurrency news articles.
        Provide a comprehensive sentiment analysis with the following details:

        1. Overall Market Sentiment: [BULLISH/BEARISH/NEUTRAL]
        2. Sentiment Score: Rate from -10 to +10
           (-10 being extremely negative, 0 being neutral, +10 being extremely positive)
        3. Use the current market data to analyze further:{current}
        4. Key Themes: Identify 3-4 main themes driving the sentiment
        5. Potential Market Impact: Brief explanation of how these news might affect the market

        News Articles:
        {news_content}
        """

        # Use Gemini Pro model
        model = genai.GenerativeModel('gemini-1.5-pro')
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        st.error(f"Sentiment Analysis Error: {e}")
        return "Unable to analyze sentiment. Please check your API keys and network connection."

# Determine Trading Decision
@st.cache_data(ttl=300)  # 5 minutes
def decision(sentiment):
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        prompt = f"""
        Analyze the given report/analysis: {sentiment}
        and give me the summary in one of the three words: BUY/SELL/HOLD
        """
        model = genai.GenerativeModel('gemini-1.5-pro')
        response = model.generate_content(prompt)
        result = response.text.strip().upper()
        # Ensure the response is one of the expected values
        if result not in ["BUY", "SELL", "HOLD"]:
            return "HOLD"
        return result
    except Exception as e:
        st.error(f"Decision Analysis Error: {e}")
        return "HOLD"

# Paper Trading Class
class PaperTrading:
    def __init__(self, initial_balance=1000000):
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.positions = {}
        self.trade_history = []

    def buy(self, symbol, price, amount):
        if price <= 0 or amount <= 0:
            return False, "Invalid price or amount"

        total_cost = price * amount

        if total_cost > self.current_balance:
            return False, "Insufficient balance"

        self.current_balance -= total_cost

        if symbol in self.positions:
            avg_price = ((self.positions[symbol]['amount'] * self.positions[symbol]['avg_price']) +
                         (amount * price)) / (self.positions[symbol]['amount'] + amount)
            self.positions[symbol]['amount'] += amount
            self.positions[symbol]['avg_price'] = avg_price
        else:
            self.positions[symbol] = {
                'amount': amount,
                'avg_price': price
            }

        trade_entry = {
            'type': 'BUY',
            'symbol': symbol,
            'price': price,
            'amount': amount,
            'timestamp': datetime.now()
        }
        self.trade_history.append(trade_entry)

        return True, "Buy order executed successfully"

    def sell(self, symbol, price, amount):
        if price <= 0 or amount <= 0:
            return False, "Invalid price or amount"

        if symbol not in self.positions or self.positions[symbol]['amount'] < amount:
            return False, "Insufficient position"

        position = self.positions[symbol]
        total_sale = price * amount
        avg_buy_price = position['avg_price']
        profit_loss = (price - avg_buy_price) * amount

        self.current_balance += total_sale

        position['amount'] -= amount
        if position['amount'] <= 0.00001:  # Small tolerance for floating point errors
            del self.positions[symbol]

        trade_entry = {
            'type': 'SELL',
            'symbol': symbol,
            'price': price,
            'amount': amount,
            'profit_loss': profit_loss,
            'timestamp': datetime.now()
        }
        self.trade_history.append(trade_entry)

        return True, f"Sell order executed. Profit/Loss: ${profit_loss:.2f}"

class AutoTrader:
    def __init__(self, paper_trading, symbol="BTC/USDT", exchange_id="binance",
                 capital_limit=None, time_limit=None, trade_interval=15,
                 position_size=0.001, stop_loss_pct=5, take_profit_pct=10):
        self.paper_trading = paper_trading
        self.symbol = symbol
        self.exchange_id = exchange_id
        self.capital_limit = capital_limit  # Maximum capital to use
        self.time_limit = time_limit  # Time to run in minutes
        self.trade_interval = max(trade_interval, AUTO_TRADE_MIN_INTERVAL)  # Minimum seconds between trade decisions
        self.position_size = position_size
        self.stop_loss_pct = stop_loss_pct / 100
        self.take_profit_pct = take_profit_pct / 100

        # Status tracking
        self.is_running = False
        self.start_time = None
        self.end_time = None
        self.initial_balance = paper_trading.current_balance
        self.capital_used = 0
        self.total_trades = 0
        self.profitable_trades = 0
        self.trade_thread = None
        self.status_message = "Ready"
        self.last_decision = "NONE"
        self.positions_managed = {}

    def start(self):
        if self.is_running:
            return False, "Auto-trader is already running"

        self.is_running = True
        self.start_time = datetime.now()
        if self.time_limit:
            self.end_time = self.start_time + timedelta(minutes=self.time_limit)
        else:
            self.end_time = None

        # Reset stats
        self.capital_used = 0
        self.total_trades = 0
        self.profitable_trades = 0
        self.status_message = "Running"
        self.positions_managed = {}

        # Start trading thread
        self.trade_thread = threading.Thread(target=self._trading_loop)
        self.trade_thread.daemon = True
        self.trade_thread.start()

        # Start the background update thread
        self.update_thread = threading.Thread(target=self._update_status)
        self.update_thread.daemon = True
        self.update_thread.start()

        return True, f"Auto-trader started for {self.symbol}"

    def stop(self):
        if not self.is_running:
            return False, "Auto-trader is not running"

        self.is_running = False
        self.status_message = "Stopped"

        # Wait for thread to finish
        if self.trade_thread and self.trade_thread.is_alive():
            self.trade_thread.join(2)

        return True, "Auto-trader stopped"

    def get_status(self):
        current_balance = self.paper_trading.current_balance
        profit_loss = current_balance - self.initial_balance
        profit_pct = (profit_loss / self.initial_balance) * 100 if self.initial_balance > 0 else 0

        win_rate = (self.profitable_trades / self.total_trades * 100) if self.total_trades > 0 else 0

        time_elapsed = None
        time_remaining = None

        if self.start_time:
            time_elapsed = (datetime.now() - self.start_time).total_seconds() / 60  # in minutes

            if self.end_time:
                time_remaining = (self.end_time - datetime.now()).total_seconds() / 60  # in minutes
                if time_remaining < 0:
                    time_remaining = 0

        return {
            "status": self.status_message,
            "symbol": self.symbol,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "time_elapsed": time_elapsed,
            "time_remaining": time_remaining,
            "initial_balance": self.initial_balance,
            "current_balance": current_balance,
            "profit_loss": profit_loss,
            "profit_loss_pct": profit_pct,
            "total_trades": self.total_trades,
            "win_rate": win_rate,
            "last_decision": self.last_decision,
            "positions": self.positions_managed
        }

    def _trading_loop(self):
        try:
            while self.is_running:
                # Check if time limit reached
                if self.end_time and datetime.now() >= self.end_time:
                    self.is_running = False
                    self.status_message = "Completed (Time Limit)"
                    break

                # Check if capital limit reached
                current_position_value = 0
                if self.symbol in self.paper_trading.positions:
                    position = self.paper_trading.positions[self.symbol]
                    current_price = fetch_current_bhaav(self.symbol)
                    if current_price:
                        current_position_value = position['amount'] * current_price

                capital_committed = self.capital_used + current_position_value
                if self.capital_limit and capital_committed >= self.capital_limit:
                    self.is_running = False
                    self.status_message = "Completed (Capital Limit)"
                    break

                # Manage existing positions (check stop loss/take profit)
                self._manage_positions()

                # Make trading decision
                self._make_trading_decision()

                # Wait for next interval
                time.sleep(self.trade_interval)

        except Exception as e:
            self.status_message = f"Error: {str(e)}"
            self.is_running = False

    def _update_status(self):
        while self.is_running:
            # Update the session state with the latest status
            st.session_state.auto_trader_status = self.get_status()
            time.sleep(5)  # Update every 5 seconds

    def _manage_positions(self):
        """Check existing positions for stop loss or take profit conditions"""
        if self.symbol in self.paper_trading.positions:
            position = self.paper_trading.positions[self.symbol]
            current_price = fetch_current_bhaav(self.symbol)

            if not current_price:
                return

            entry_price = position['avg_price']
            current_pnl_pct = (current_price / entry_price - 1) * 100

            # Check stop loss
            if current_pnl_pct <= -self.stop_loss_pct * 100:
                self.status_message = f"Executing stop loss at {current_pnl_pct:.2f}%"
                success, message = self.paper_trading.sell(
                    self.symbol,
                    current_price,
                    position['amount']
                )
                if success:
                    self.total_trades += 1
                    if current_price > entry_price:
                        self.profitable_trades += 1

                    # Record position result
                    self.positions_managed[len(self.positions_managed) + 1] = {
                        "type": "STOP_LOSS",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl_pct": current_pnl_pct,
                        "time": datetime.now()
                    }

            # Check take profit
            elif current_pnl_pct >= self.take_profit_pct * 100:
                self.status_message = f"Taking profit at {current_pnl_pct:.2f}%"
                success, message = self.paper_trading.sell(
                    self.symbol,
                    current_price,
                    position['amount']
                )
                if success:
                    self.total_trades += 1
                    self.profitable_trades += 1

                    # Record position result
                    self.positions_managed[len(self.positions_managed) + 1] = {
                        "type": "TAKE_PROFIT",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl_pct": current_pnl_pct,
                        "time": datetime.now()
                    }

    def _make_trading_decision(self):
        """Use AI to make trading decisions"""
        try:
            # Fetch data
            df = fetch_live_data(self.symbol, self.exchange_id)
            current_price = fetch_current_bhaav(self.symbol)

            if df is None or current_price is None:
                return

            # Fetch news and analyze sentiment
            news_articles = fetch_crypto_news(self.symbol)
            if not news_articles:
                return

            # Get sentiment analysis and trading decision
            news_sentiment = analyze_news_sentiment(news_articles, current=current_price)
            trading_decision = decision(news_sentiment)
            self.last_decision = trading_decision

            # Get technical indicators
            indicators = compute_technical_indicators(df)

            # Make decision based on AI recommendation and indicators
            has_position = self.symbol in self.paper_trading.positions

            if trading_decision == "BUY" and not has_position:
                # Check technical confirmations (RSI < 70 and MACD > Signal Line)
                if indicators['rsi'][-1] < 70 and indicators['macd'][-1] > indicators['signal_line'][-1]:
                    self.status_message = "Opening position based on AI recommendation"
                    success, message = self.paper_trading.buy(
                        self.symbol,
                        current_price,
                        self.position_size
                    )
                    if success:
                        self.capital_used += current_price * self.position_size

            elif trading_decision == "SELL" and has_position:
                # Check technical confirmations (RSI > 30 or MACD < Signal Line)
                if indicators['rsi'][-1] > 30 or indicators['macd'][-1] < indicators['signal_line'][-1]:
                    self.status_message = "Closing position based on AI recommendation"
                    position = self.paper_trading.positions[self.symbol]
                    success, message = self.paper_trading.sell(
                        self.symbol,
                        current_price,
                        position['amount']
                    )
                    if success:
                        self.total_trades += 1
                        if current_price > position['avg_price']:
                            self.profitable_trades += 1

                        # Record position result
                        self.positions_managed[len(self.positions_managed) + 1] = {
                            "type": "AI_SIGNAL",
                            "entry": position['avg_price'],
                            "exit": current_price,
                            "pnl_pct": (current_price / position['avg_price'] - 1) * 100,
                            "time": datetime.now()
                    }

        except Exception as e:
            self.status_message = f"Decision error: {str(e)}"

class Graph:
    def __init__(self, symbol, exchange_id='binance'):
        self.symbol = symbol
        self.exchange_id = exchange_id
        self.df = fetch_live_data(self.symbol, self.exchange_id)
        self.fig = create_figure_with_indicators(self.df)

    def update_figure(self):
        self.df = fetch_live_data(self.symbol, self.exchange_id)
        self.fig = create_figure_with_indicators(self.df)

    def render(self):
        st.plotly_chart(self.fig, use_container_width=True)

def create_figure_with_indicators(df):
    fig = go.Figure(data=[go.Candlestick(
        x=df['timestamp'],
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        increasing_line_color='green',
        decreasing_line_color='red'
    )])

    # Add Bollinger Bands
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=df['close'].rolling(window=20).mean().tolist(),  # Ensure y is a list
        mode='lines',
        name='Middle Band',
        line=dict(color='gray', width=1)
    ))

    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=(df['close'].rolling(window=20).mean() + 2 * df['close'].rolling(window=20).std()).tolist(),  # Ensure y is a list
        mode='lines',
        name='Upper Band',
        line=dict(color='red', width=1)
    ))

    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=(df['close'].rolling(window=20).mean() - 2 * df['close'].rolling(window=20).std()).tolist(),  # Ensure y is a list
        mode='lines',
        name='Lower Band',
        line=dict(color='green', width=1)
    ))

    # Add RSI
    rsi = compute_technical_indicators(df)['rsi']
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=rsi,  # Ensure y is a list
        mode='lines',
        name='RSI',
        line=dict(color='purple', width=1),
        yaxis='y2'  # Use secondary y-axis
    ))

    # Add MACD and Signal Line
    indicators = compute_technical_indicators(df)
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=indicators['macd'],
        mode='lines',
        name='MACD',
        line=dict(color='blue', width=1),
        yaxis='y3'  # Use tertiary y-axis
    ))

    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=indicators['signal_line'],
        mode='lines',
        name='Signal Line',
        line=dict(color='orange', width=1),
        yaxis='y3'  # Use tertiary y-axis
    ))

    # layout to improve aesthetics and functionality
    fig.update_layout(
        title="Price Chart with Indicators",
        xaxis_title="Time",
        yaxis_title="Price",
        yaxis2=dict(
            title="RSI",
            overlaying="y",
            side="right",
            tickformat=".0%",
            showgrid=False
        ),
        yaxis3=dict(
            title="MACD",
            overlaying="y",
            side="right",
            anchor="free",
            position=0.9
        ),
        template='plotly_dark',
        height=600,
        margin=dict(l=40, r=40, t=60, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        plot_bgcolor='#1E2130',
        paper_bgcolor='#1E2130',
        font=dict(color='white'),
        dragmode='pan',
        hovermode='x unified'
    )

    # Add annotations for significant events (example)
    annotations = [
        dict(
            x=df['timestamp'].iloc[-1],
            y=df['close'].iloc[-1],
            xref="x",
            yref="y",
            text="Latest Price",
            showarrow=True,
            arrowhead=3,
            ax=0,
            ay=-40,
            font=dict(color="white")
        )
    ]

    fig.update_layout(annotations=annotations)

    return fig

def create_auto_trade_ui():
    st.markdown('<h2>AI Auto-Trading</h2>', unsafe_allow_html=True)

    # Create a container for the auto-trading panel
    st.markdown("""
    <div style="background-color: #262730; border-radius: 10px; padding: 20px; margin-bottom: 20px;">
        <h3 style="margin-bottom: 15px;">Auto-Trading System</h3>
    """, unsafe_allow_html=True)

    # Initialize auto trader if not in session state
    if 'auto_trader' not in st.session_state:
        st.session_state.auto_trader = AutoTrader(st.session_state.paper_trading)

    # Configuration and control in tabs
    tab1, tab2 = st.tabs(["Control Panel", "Statistics"])

    with tab1:
        col1, col2 = st.columns(2)

        with col1:
            symbol = st.text_input("Trading Symbol", value=st.session_state.auto_trader.symbol,
                                  key="autotrader_symbol")

            exchange = st.selectbox("Exchange", EXCHANGES,
                                   index=EXCHANGES.index("binance"),
                                   key="autotrader_exchange")

            position_size = st.number_input("Position Size",
                                          value=st.session_state.auto_trader.position_size,
                                          step=0.001, format="%.4f",
                                          key="autotrader_position_size")

        with col2:
            time_limit = st.number_input("Time Limit (minutes, 0 for no limit)",
                                        value=60, min_value=0, step=5,
                                        key="autotrader_time_limit")

            capital_limit = st.number_input("Capital Limit (USDT, 0 for no limit)",
                                          value=1000, min_value=0, step=100,
                                          key="autotrader_capital_limit")

            trade_interval = st.number_input("Trade Interval (seconds)",
                                           value=30, min_value=AUTO_TRADE_MIN_INTERVAL, step=5,
                                           key="autotrader_interval")

        col3, col4 = st.columns(2)

        with col3:
            stop_loss = st.slider("Stop Loss %",
                                min_value=1, max_value=20, value=5,
                                key="autotrader_stop_loss")

        with col4:
            take_profit = st.slider("Take Profit %",
                                  min_value=1, max_value=50, value=10,
                                  key="autotrader_take_profit")

        # Check if running
        is_running = st.session_state.auto_trader.is_running

        # Apply button to update settings
        if not is_running and st.button("Apply Settings", use_container_width=True):
            # Update auto trader with new settings
            st.session_state.auto_trader = AutoTrader(
                paper_trading=st.session_state.paper_trading,
                symbol=symbol,
                exchange_id=exchange,
                capital_limit=capital_limit if capital_limit > 0 else None,
                time_limit=time_limit if time_limit > 0 else None,
                trade_interval=trade_interval,
                position_size=position_size,
                stop_loss_pct=stop_loss,
                take_profit_pct=take_profit
            )
            st.success("Settings applied.")

        # Start/Stop buttons
        col5, col6 = st.columns(2)

        with col5:
            if not is_running:
                if st.button("▶️ Start Auto-Trading",
                           type="primary", use_container_width=True,
                           key="start_autotrader"):
                    success, message = st.session_state.auto_trader.start()
                    if success:
                        st.success(message)
                    else:
                        st.error(message)

        with col6:
            if is_running:
                if st.button("⏹️ Stop Auto-Trading",
                           type="primary", use_container_width=True,
                           key="stop_autotrader"):
                    success, message = st.session_state.auto_trader.stop()
                    if success:
                        st.success(message)
                    else:
                        st.error(message)

    with tab2:
        # Get current status
        status = st.session_state.get('auto_trader_status', st.session_state.auto_trader.get_status())

        # Top stats row
        status_color = "green" if status["status"] == "Running" else "red" if "Error" in status["status"] else "white"

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">Status</p>
                <p style="font-size: 20px; font-weight: bold; color: {status_color};">{status["status"]}</p>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            elapsed = status["time_elapsed"]
            elapsed_str = f"{int(elapsed) if elapsed else 0} min" if elapsed is not None else "N/A"

            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">Time Elapsed</p>
                <p style="font-size: 20px; font-weight: bold;">{elapsed_str}</p>
            </div>
            """, unsafe_allow_html=True)

        with col3:
            remaining = status["time_remaining"]
            remaining_str = f"{int(remaining) if remaining else 0} min" if remaining is not None else "No Limit"

            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">Time Remaining</p>
                <p style="font-size: 20px; font-weight: bold;">{remaining_str}</p>
            </div>
            """, unsafe_allow_html=True)

        # Performance stats
        st.markdown("### Performance")

        col4, col5, col6, col7 = st.columns(4)

        with col4:
            pnl_color = "green" if status["profit_loss"] >= 0 else "red"

            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">Total P/L</p>
                <p style="font-size: 20px; font-weight: bold; color: {pnl_color};">
                    ${status["profit_loss"]:.2f} ({status["profit_loss_pct"]:.2f}%)
                </p>
            </div>
            """, unsafe_allow_html=True)

        with col5:
            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">Total Trades</p>
                <p style="font-size: 20px; font-weight: bold;">{status["total_trades"]}</p>
            </div>
            """, unsafe_allow_html=True)

        with col6:
            win_rate_color = "green" if status["win_rate"] >= 50 else "yellow" if status["win_rate"] >= 30 else "red"

            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">Win Rate</p>
                <p style="font-size: 20px; font-weight: bold; color: {win_rate_color};">
                    {status["win_rate"]:.1f}%
                </p>
            </div>
            """, unsafe_allow_html=True)

        with col7:
            decision_color = "green" if status["last_decision"] == "BUY" else "red" if status["last_decision"] == "SELL" else "gray"

            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">Last Signal</p>
                <p style="font-size: 20px; font-weight: bold; color: {decision_color};">
                    {status["last_decision"]}
                </p>
            </div>
            """, unsafe_allow_html=True)

        # Trade history table
        if status["positions"]:
            st.markdown("### Position History")

            # Convert positions to DataFrame
            positions_data = []
            for pos_id, pos in status["positions"].items():
                positions_data.append({
                    "ID": pos_id,
                    "Type": pos["type"],
                    "Entry": f"${pos['entry']:.2f}",
                    "Exit": f"${pos['exit']:.2f}",
                    "P/L %": f"{pos['pnl_pct']:.2f}%",
                    "Time": pos["time"].strftime("%H:%M:%S")
                })

            pos_df = pd.DataFrame(positions_data)
            st.dataframe(pos_df, use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

def plot_performance_metrics(trade_history):
    # Check if 'profit_loss' column exists
    if 'profit_loss' in trade_history.columns:
        # Calculate cumulative profit/loss
        trade_history['cumulative_pnl'] = trade_history['profit_loss'].cumsum()
    else:
        # Handle the case where 'profit_loss' column is missing
        st.warning("Profit/Loss data is not available for performance metrics.")
        return

    # Create a line chart for cumulative P/L
    fig = go.Figure(data=[go.Scatter(
        x=trade_history['timestamp'],
        y=trade_history['cumulative_pnl'],
        mode='lines',
        name='Cumulative P/L',
        line=dict(color='blue', width=2)
    )])

    fig.update_layout(
        title="Cumulative Profit/Loss Over Time",
        xaxis_title="Time",
        yaxis_title="Cumulative P/L (USDT)",
        template='plotly_dark',
        height=400,
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor='#1E2130',
        paper_bgcolor='#1E2130',
        font=dict(color='white')
    )

    st.plotly_chart(fig, use_container_width=True)

def main():
    # Initialize paper trading
    if 'paper_trading' not in st.session_state:
        st.session_state.paper_trading = PaperTrading(INITIAL_BALANCE)

    # Initialize analysis_results
    if 'analysis_results' not in st.session_state:
        st.session_state.analysis_results = None

    # Inject JavaScript for popups
    inject_popup_js()

    # Title
    st.title("Crypto Trading Dashboard")

    # Exchange Selection
    col1, col2, col3 = st.columns(3)

    with col1:
        selected_exchange = st.selectbox("Select Exchange", EXCHANGES, index=0)

    with col2:
        # Fetch available symbols for the selected exchange
        available_symbols = fetch_available_symbols(selected_exchange)
        default_index = 0
        if available_symbols and "BTC/USDT" in available_symbols:
            default_index = available_symbols.index("BTC/USDT")
        selected_symbol = st.selectbox("Select Trading Pair", available_symbols,
                                       index=min(default_index, len(available_symbols) - 1) if available_symbols else 0)

    with col3:
        # Manual Symbol Input (Fallback option)
        manual_symbol = st.text_input("Or Enter Custom Symbol", placeholder="e.g., ETH/USDT")
        if manual_symbol:
            selected_symbol = manual_symbol

    # Fetch Live Data
    df = fetch_live_data(selected_symbol, selected_exchange)
    current_price = fetch_current_bhaav(symbol=selected_symbol)

    if df is not None and current_price is not None:
        # Display current price prominently at the top
        price_col1, price_col2 = st.columns([1, 3])
        with price_col1:
            st.markdown("### Current Price")
        with price_col2:
            st.markdown(f"""
            <div class="highlight-value">
                <p class="big-font">${current_price:.2f}</p>
            </div>
            """, unsafe_allow_html=True)

        # CHART SECTION
        st.markdown('<h2>Price Chart</h2>', unsafe_allow_html=True)

        # Create a Graph instance with the selected timeframe
        graph = Graph(symbol=selected_symbol, exchange_id=selected_exchange)

        # Display the graph
        graph.render()

        # Update the graph every 5 seconds
        if 'last_update_time' not in st.session_state:
            st.session_state.last_update_time = time.time()

        if time.time() - st.session_state.last_update_time >= 5:
            graph.update_figure()
            st.session_state.last_update_time = time.time()
            st.rerun()

        # Divider
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

        # ANALYSIS SECTION
        st.markdown('<h2>Market Analysis</h2>', unsafe_allow_html=True)

        # Always perform sentiment analysis automatically when page loads
        if 'analysis_results' not in st.session_state or st.session_state.analysis_results is None:
            with st.spinner('Analyzing market sentiment...'):
                # Fetch News
                news_articles = fetch_crypto_news(selected_symbol)

                if news_articles:
                    # Analyze Sentiment
                    news_sentiment = analyze_news_sentiment(news_articles, current=current_price)

                    # Get Trading Decision
                    trading_decision = decision(news_sentiment)

                    # Extract sentiment details
                    sentiment_type, sentiment_score = extract_sentiment_details(news_sentiment)

                    # Store results
                    st.session_state.analysis_results = {
                        'timestamp': datetime.now(),
                        'sentiment': news_sentiment,
                        'sentiment_type': sentiment_type,
                        'sentiment_score': sentiment_score,
                        'decision': trading_decision,
                        'news': news_articles
                    }
                else:
                    st.warning("Unable to fetch news articles. Analysis may be limited.")
                    # Create fallback analysis results
                    st.session_state.analysis_results = {
                        'timestamp': datetime.now(),
                        'sentiment': "No news articles available for analysis.",
                        'sentiment_type': "NEUTRAL",
                        'sentiment_score': 0,
                        'decision': "HOLD",
                        'news': []
                    }

        # Display Analysis Results
        if st.session_state.analysis_results:
            analysis = st.session_state.analysis_results

            # Determine sentiment class based on sentiment type
            sentiment_class = "sentiment-positive" if analysis['sentiment_type'] == "BULLISH" else (
                "sentiment-negative" if analysis['sentiment_type'] == "BEARISH" else "sentiment-neutral"
            )

            # Determine decision class based on decision type
            decision_class = "sentiment-positive" if analysis['decision'] == "BUY" else (
                "sentiment-negative" if analysis['decision'] == "SELL" else "sentiment-neutral"
            )

            # Create a card-like container for analysis results
            st.markdown(f"""
            <div style="background-color: #262730; border-radius: 10px; padding: 20px; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 15px;">
                    <div style="width: 48%;">
                        <div class="{sentiment_class}">
                            <p class="medium-font">Market Sentiment: {analysis['sentiment_type']}</p>
                        </div>
                    </div>
                    <div style="width: 48%;">
                        <div class="{decision_class}">
                            <p class="medium-font">Recommendation: {analysis['decision']}</p>
                        </div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Sentiment score slider visualization
            score = analysis['sentiment_score']
            st.markdown(f"### Sentiment Score: {score:.1f}")

            # Create a custom visualization for the sentiment score
            score_percentage = (score + 10) / 20  # Convert -10 to +10 scale to 0-1 for progress bar
            score_color = "green" if score > 3 else "red" if score < -3 else "gray"
            st.markdown(f"""
            <div style="width:100%; background-color:#333; height:20px; border-radius:10px;">
                <div style="width:{score_percentage * 100}%; background-color:{score_color}; height:20px; border-radius:10px;"></div>
            </div>
            <div style="display:flex; justify-content:space-between; margin-top:5px;">
                <span>-10</span>
                <span>0</span>
                <span>+10</span>
            </div>
            """, unsafe_allow_html=True)

            # Full sentiment analysis
            st.subheader("Detailed Analysis")
            st.write(analysis['sentiment'])

            # Display Recent News
            st.subheader("Recent News")

            for i, article in enumerate(analysis['news'][:5]):
                # Create a cleaner card for each news item
                st.markdown(f"""
                <div style="background-color: #2E3440; border-radius: 5px; padding: 15px; margin-bottom: 10px;">
                    <h4 style="margin-top: 0;">{article['title']}</h4>
                    <p style="color: #D8DEE9; font-size: 14px;">{article.get('description', '')[:150]}...</p>
                    <div style="display: flex; justify-content: space-between; font-size: 12px; color: #81A1C1;">
                        <span>{article['source']['name']}</span>
                        <span>{article['publishedAt'][:10]}</span>
                    </div>
                    <a href="{article['url']}" target="_blank" style="display: inline-block; margin-top: 10px;
                    background-color: #3B4252; color: white; padding: 5px 10px; border-radius: 5px; text-decoration: none;">
                    Read More</a>
                </div>
                """, unsafe_allow_html=True)

        # Divider
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

        # TRADING SECTION
        st.markdown('<h2>Trading Panel</h2>', unsafe_allow_html=True)

        # Account and Position Summary
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Account Balance")
            # Highlight current balance with bigger font
            st.markdown(f"""
            <div class="highlight-value">
                <p class="big-font">${st.session_state.paper_trading.current_balance:.2f}</p>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            st.subheader("Current Position")
            if selected_symbol in st.session_state.paper_trading.positions:
                position = st.session_state.paper_trading.positions[selected_symbol]
                st.markdown(f"""
                <div class="highlight-value">
                    <p class="medium-font">{position['amount']:.4f} @ ${position['avg_price']:.2f}</p>
                </div>
                """, unsafe_allow_html=True)

                # Calculate and display P&L
                unrealized_pnl = (current_price - position['avg_price']) * position['amount']
                pnl_percentage = (current_price / position['avg_price'] - 1) * 100

                pnl_color = "green" if unrealized_pnl > 0 else "red"

                st.markdown(f"""
                <div class="highlight-value">
                    <p class="medium-font">P&L: <span style='color:{pnl_color}'>${unrealized_pnl:.2f} ({pnl_percentage:.2f}%)</span></p>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.info("No current position")

            #Account and Position Summary section
            if st.session_state.paper_trading.positions:
                # Create data for portfolio pie chart
                position_values = []
                labels = []

                for symbol, position in st.session_state.paper_trading.positions.items():
                    try:
                        price = fetch_current_bhaav(symbol)
                        if price is None:
                            price = position['avg_price']  # Fallback to avg price
                    except:
                        price = position['avg_price']

                    position_value = position['amount'] * price
                    position_values.append(position_value)
                    labels.append(symbol)

                # Add cash balance
                position_values.append(st.session_state.paper_trading.current_balance)
                labels.append("Cash")

                # Create pie chart
                fig_portfolio = go.Figure(data=[go.Pie(
                    labels=labels,
                    values=position_values,
                    hole=.4,
                    marker=dict(colors=['#5E81AC', '#81A1C1', '#88C0D0', '#8FBCBB', '#A3BE8C', '#EBCB8B'])
                )])

                fig_portfolio.update_layout(
                    title="Portfolio Allocation",
                    template='plotly_dark',
                    height=300,
                    margin=dict(l=20, r=20, t=40, b=20),
                    plot_bgcolor='#1E2130',
                    paper_bgcolor='#1E2130',
                )

                st.plotly_chart(fig_portfolio, use_container_width=True)

        #Trading Controls
        st.subheader("Trading Controls")

        # Create a nice container for trading panel
        st.markdown("""
        <div style="background-color: #262730; border-radius: 10px; padding: 20px; margin-bottom: 20px;">
        """, unsafe_allow_html=True)

        # Define indicators by calculating technical indicators
        indicators = compute_technical_indicators(df)

        # Correct the unpacking to match the number of columns created
        indicators_col1, indicators_col2, indicators_col3, indicators_col4 = st.columns(4)

        with indicators_col1:
            rsi_color = "green" if indicators['rsi'][-1] < 30 else "red" if indicators['rsi'][-1] > 70 else "white"
            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">RSI</p>
                <p style="font-size: 24px; font-weight: bold; color: {rsi_color};">{indicators['rsi'][-1]:.2f}</p>
            </div>
            """, unsafe_allow_html=True)

        with indicators_col2:
            macd_color = "green" if indicators['macd'][-1] > indicators['signal_line'][-1] else "red"
            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">MACD</p>
                <p style="font-size: 24px; font-weight: bold; color: {macd_color};">{indicators['macd'][-1]:.4f}</p>
            </div>
            """, unsafe_allow_html=True)

        with indicators_col3:
            bb_position = (current_price - indicators['lower_band'][-1]) / (
                        indicators['upper_band'][-1] - indicators['lower_band'][-1])
            bb_color = "green" if bb_position < 0.2 else "red" if bb_position > 0.8 else "white"
            st.markdown(f"""
            <div style="text-align: center;">
                <p style="margin-bottom: 5px; color: gray;">BB Position</p>
                <p style="font-size: 24px; font-weight: bold; color: {bb_color};">{bb_position:.2f}</p>
            </div>
            """, unsafe_allow_html=True)

        with indicators_col4:
            # Display trading decision from analysis
            if 'analysis_results' in st.session_state and st.session_state.analysis_results:
                decision_color = "green" if st.session_state.analysis_results['decision'] == "BUY" else "red" if \
                st.session_state.analysis_results['decision'] == "SELL" else "white"
                st.markdown(f"""
                <div style="text-align: center;">
                    <p style="margin-bottom: 5px; color: gray;">AI Recommendation</p>
                    <p style="font-size: 24px; font-weight: bold; color: {decision_color};">{st.session_state.analysis_results['decision']}</p>
                </div>
                """, unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

        # Single-click buy/sell buttons with amount input
        single_buy_col, single_sell_col = st.columns(2)

        with single_buy_col:
            amount_usdt = st.number_input("Amount (USDT)", min_value=0.0, format="%.2f", key="buy_amount_usdt")
            amount_symbol = amount_usdt / current_price
            if st.button("🔄 Buy at Market", use_container_width=True, key="single_buy_button"):
                success, message = st.session_state.paper_trading.buy(selected_symbol, current_price, amount_symbol)
                if success:
                    st.session_state.trade_executed = True
                    st.success(message)
                    st.toast('Market buy order executed successfully!', icon='✅')
                else:
                    st.error(message)

        with single_sell_col:
            amount_symbol_sell = st.number_input("Amount (Symbol)", min_value=0.0, format="%.4f", key="sell_amount_symbol")
            amount_usdt_sell = amount_symbol_sell * current_price
            if st.button("🔄 Sell at Market", use_container_width=True, key="single_sell_button"):
                success, message = st.session_state.paper_trading.sell(selected_symbol, current_price, amount_symbol_sell)
                if success:
                    st.session_state.trade_executed = True
                    st.success(message)
                    st.toast('Market sell order executed successfully!', icon='✅')
                else:
                    st.error(message)

        st.markdown("</div>", unsafe_allow_html=True)

        # Divider
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

        # TRADE HISTORY SECTION
        st.markdown('<h2>Trade History</h2>', unsafe_allow_html=True)

        if st.session_state.paper_trading.trade_history:
            # Create a DataFrame from trade history
            history_df = pd.DataFrame(st.session_state.paper_trading.trade_history)

            # Format the DataFrame
            history_df['amount'] = history_df['amount'].apply(lambda x: f"{x:.4f}")
            history_df['price'] = history_df['price'].apply(lambda x: f"${x:.2f}")

            # Add profit/loss column if available
            if 'profit_loss' in history_df.columns:
                history_df['profit_loss'] = history_df['profit_loss'].apply(
                    lambda x: f"${x:.2f}" if pd.notnull(x) else "")
            else:
                history_df['profit_loss'] = ""

            # Format timestamp
            history_df['timestamp'] = history_df['timestamp'].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S"))

            # Reorder columns
            columns = ['timestamp', 'type', 'symbol', 'price', 'amount', 'profit_loss']
            history_df = history_df[columns]

            # Rename columns
            history_df.columns = ['Time', 'Type', 'Symbol', 'Price', 'Amount', 'P/L']

            # Show the table
            st.dataframe(history_df, use_container_width=True)

            # Add export option
            csv = history_df.to_csv(index=False)
            st.download_button(
                label="Export Trade History",
                data=csv,
                file_name="trade_history.csv",
                mime="text/csv"
            )

            # Plot performance metrics
            plot_performance_metrics(history_df)
        else:
            st.info("No trade history yet. Make your first trade to see it here.")

        # Add the Auto-Trading section here
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        create_auto_trade_ui()

    else:
        st.error("Error fetching market data. Please check your connection and try again.")

if __name__ == "__main__":
    main()