import yfinance as yf
import pandas as pd
import numpy as np
import time
import json
import os
import requests
import schedule
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

class UltimateFNOTrader:
    def __init__(self):
        # COMPLETE LIST OF ALL NSE F&O STOCKS (200+ stocks)
        self.nse_fo_stocks = [
            # NIFTY 50
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
            "HINDUNILVR.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "ITC.NS",
            "LT.NS", "ASIANPAINT.NS", "HCLTECH.NS", "AXISBANK.NS", "MARUTI.NS",
            "SUNPHARMA.NS", "TITAN.NS", "ULTRACEMCO.NS", "TATAMOTORS.NS", "BAJFINANCE.NS",
            "DMART.NS", "BAJAJFINSV.NS", "WIPRO.NS", "ADANIPORTS.NS", "POWERGRID.NS",
            "NTPC.NS", "HINDALCO.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "BRITANNIA.NS",
            "ONGC.NS", "CIPLA.NS", "TECHM.NS", "INDUSINDBK.NS", "NESTLEIND.NS",
            "COALINDIA.NS", "GRASIM.NS", "SBILIFE.NS", "DRREDDY.NS", "M&M.NS",
            "UPL.NS", "BAJAJ-AUTO.NS", "APOLLOHOSP.NS", "EICHERMOT.NS", "ADANIENT.NS",
            "TATACONSUM.NS", "BPCL.NS", "HEROMOTOCO.NS", "DIVISLAB.NS",
            
            # NIFTY NEXT 50
            "VEDL.NS", "SHREECEM.NS", "PIDILITIND.NS", "ADANIGREEN.NS", "HDFCLIFE.NS",
            "ICICIPRULI.NS", "BERGEPAINT.NS", "HAVELLS.NS", "GODREJCP.NS", "DABUR.NS",
            "SIEMENS.NS", "BAJAJHLDNG.NS", "TORNTPHARM.NS", "AMBUJACEM.NS", "INDIGO.NS",
            "MARICO.NS", "AUROPHARMA.NS", "BIOCON.NS", "TIINDIA.NS", "IRCTC.NS",
            "MOTHERSON.NS", "NAUKRI.NS", "PEL.NS", "ACC.NS", "COLPAL.NS",
            "SRF.NS", "GAIL.NS", "GLAND.NS", "CONCOR.NS", "LODHA.NS",
            "PAGEIND.NS", "HINDPETRO.NS", "ABCAPITAL.NS", "BOSCHLTD.NS", "ASHOKLEY.NS",
            "BANKBARODA.NS", "HAL.NS", "CANBK.NS", "PNB.NS", "UNIONBANK.NS",
            "IDFCFIRSTB.NS",
            
            # OTHER ACTIVE F&O STOCKS
            "BHEL.NS", "IOC.NS", "VOLTAS.NS", "TATAPOWER.NS", "NHPC.NS",
            "SAIL.NS", "HUDCO.NS", "RECLTD.NS", "PFC.NS", "IRFC.NS",
            "BANDHANBNK.NS", "FEDERALBNK.NS", "IDBI.NS", "RBLBANK.NS", "YESBANK.NS",
            "MPHASIS.NS", "PERSISTENT.NS", "COFORGE.NS", "LTTS.NS", "TATAELXSI.NS",
            "NIACL.NS", "GICRE.NS", "HDFCAMC.NS", "ICICIGI.NS", "CHOLAFIN.NS",
            "SBICARD.NS", "BAJAJELEC.NS", "CROMPTON.NS", "WHIRLPOOL.NS", "BLUESTARCO.NS",
            "AMBER.NS", "VGUARD.NS", "SUPREMEIND.NS", "DIXON.NS", "RADICO.NS",
            "JUBLFOOD.NS", "WESTLIFE.NS", "KAJARIACER.NS", "HNDFDS.NS", "SONACOMS.NS",
            "ASTRAZEN.NS", "ALKEM.NS", "LAURUSLABS.NS", "GRANULES.NS", "AJANTPHARM.NS",
            "FORTIS.NS", "MAXHEALTH.NS", "METROPOLIS.NS", "LALPATHLAB.NS", "ASTRAL.NS",
            "KANSAINER.NS", "AKZOINDIA.NS", "DALBHARAT.NS", "RAMCOCEM.NS", "JKCEMENT.NS",
            "HEIDELBERG.NS", "MANAPPURAM.NS", "MUTHOOTFIN.NS", "AUBANK.NS",
        ]
        
        # Remove duplicates and ensure unique list
        self.nse_fo_stocks = list(set(self.nse_fo_stocks))
        
        # ULTIMATE Parameters
        self.min_price = 50
        self.min_volume = 100000
        self.required_move = 4.0
        self.min_confidence = 7.0
        self.min_rr_ratio = 1.5
        self.max_signals_per_side = 8
        self.scan_batch_size = 15
        
        # ULTIMATE Holding Periods - Adaptive based on market conditions
        self.base_holding_days = 2  # Default for F&O
        self.adaptive_holding = {
            'HIGH_VOLATILITY': 2,
            'MEDIUM_VOLATILITY': 3, 
            'LOW_VOLATILITY': 4
        }
        
        # INTRADAY Parameters
        self.intraday_required_move = 2.0  # Smaller moves for intraday
        self.intraday_min_confidence = 6.5  # Slightly lower confidence for intraday
        self.intraday_max_signals = 12  # More signals for intraday
        
        # Telegram Configuration
        self.telegram_token = "8427863153:AAE2QL09KYMuF-xlsNuhdF7j050qW15NJv8"
        self.telegram_chat_id = None  # Will be auto-detected
        self.telegram_enabled = True
        
        # Scheduling Configuration
        self.scheduler_running = False
        self.auto_trading_enabled = False
        
        # Monitoring & Paper Trading
        self.trade_log = []
        self.performance_data = []
        self.paper_portfolio = {'cash': 100000, 'positions': {}, 'total_value': 100000}
        
        # Statistics
        self.scan_stats = {
            'total_scanned': 0, 'successful_data': 0, 'passed_filters': 0, 
            'high_confidence': 0, 'bullish_signals': 0, 'bearish_signals': 0,
            'failed_stocks': [], 'market_volatility': 'MEDIUM'
        }
        
        # Initialize Telegram
        self.init_telegram()
        self.load_trade_history()
        
        print(f"📊 Loaded {len(self.nse_fo_stocks)} unique F&O stocks")
        if self.telegram_enabled:
            print("🤖 Telegram alerts ENABLED")
            print(f"📢 Telegram Channel: {self.telegram_chat_id}")

    # ----------------- TELEGRAM METHODS -----------------
    def init_telegram(self):
        """Initialize Telegram bot"""
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data['ok'] and data['result']:
                    self.telegram_chat_id = data['result'][-1]['message']['chat']['id']
                    self.send_telegram_alert("🤖 Ultimate F&O Trader Started! 🚀")
                else:
                    print("❌ Please send a message to your bot first")
                    self.telegram_enabled = False
            else:
                print("❌ Failed to connect to Telegram")
                self.telegram_enabled = False
                
        except Exception as e:
            print(f"❌ Telegram init failed: {e}")
            self.telegram_enabled = False

    def send_telegram_alert(self, message: str):
        """Send alert to Telegram"""
        if not self.telegram_enabled or not self.telegram_chat_id:
            return False
            
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"Telegram send failed: {e}")
            return False

    def test_telegram_immediately(self):
        """Test Telegram connection immediately"""
        print("\n" + "="*50)
        print("🔧 TESTING TELEGRAM CONNECTION")
        print("="*50)
        
        # Test configuration
        print(f"🤖 Bot Token: {self.telegram_token[:10]}...{self.telegram_token[-10:]}")
        print(f"📢 Chat ID: {self.telegram_chat_id}")
        
        # Send test message
        test_message = "🚀 **ULTIMATE F&O TRADER - CONNECTION TEST**\n\n" \
                      "✅ If you see this message, your Telegram setup is working perfectly!\n\n" \
                      "🤖 Bot Features:\n" \
                      "• Real-time Trading Alerts\n" \
                      "• Auto Scheduling\n" \
                      "• Swing & Intraday Signals\n" \
                      "• Paper Trading\n\n" \
                      "⚡ Ready to start automated trading!"
        
        success = self.send_telegram_alert(test_message)
        
        if success:
            print("🎉 TELEGRAM TEST SUCCESSFUL! Alerts will work during scanning.")
        else:
            print("❌ TELEGRAM TEST FAILED! Check your configuration.")
            
        return success

    # ----------------- SINGLE STOCK ANALYSIS FEATURE -----------------
    def analyze_specific_stock(self, symbol: str, analysis_type: str = "COMPREHENSIVE"):
        """
        Comprehensive analysis for a specific stock
        analysis_type: "COMPREHENSIVE", "TECHNICAL", "INTRADAY", "SWING"
        """
        print(f"\n{'🔍 SINGLE STOCK ANALYSIS ':═^80}")
        print(f"📈 Analyzing: {symbol}")
        print(f"📊 Type: {analysis_type}")
        print(f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        if not symbol.endswith('.NS'):
            symbol += '.NS'
            
        try:
            # Get stock data based on analysis type
            if analysis_type in ["COMPREHENSIVE", "TECHNICAL", "SWING"]:
                # Swing/Technical analysis - longer time frame
                data = self.get_detailed_stock_data(symbol, period="2mo", interval="1d")
            else:
                # Intraday analysis - shorter time frame
                data = self.get_intraday_data(symbol)
                
            if data is None or data.empty:
                print(f"❌ No data available for {symbol}")
                return None
                
            # Calculate comprehensive indicators
            indicators = self.calculate_comprehensive_indicators(data, analysis_type)
            if not indicators:
                print(f"❌ Could not calculate indicators for {symbol}")
                return None
                
            # Generate analysis report
            analysis_report = self.generate_analysis_report(symbol, indicators, analysis_type)
            
            # Display the analysis
            self.display_stock_analysis(analysis_report, analysis_type)
            
            # Send Telegram alert for high-confidence signals
            if self.telegram_enabled and analysis_report.get('trade_signal', {}).get('confidence', 0) >= 7.0:
                self.send_stock_analysis_alert(analysis_report)
                
            return analysis_report
            
        except Exception as e:
            print(f"❌ Error analyzing {symbol}: {e}")
            return None

    def get_detailed_stock_data(self, symbol: str, period: str = "2mo", interval: str = "1d"):
        """Get detailed stock data with multiple timeframes"""
        try:
            stock = yf.Ticker(symbol)
            
            # Main data
            main_data = stock.history(period=period, interval=interval)
            
            if main_data.empty:
                return None
                
            return main_data
        except Exception as e:
            print(f"❌ Error getting data for {symbol}: {e}")
            return None

    def calculate_comprehensive_indicators(self, data, analysis_type: str) -> Dict:
        """Calculate comprehensive technical indicators"""
        try:
            df = data
            
            if df.empty or len(df) < 20:
                return None
                
            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']
            
            current_price = close.iloc[-1]
            prev_close = close.iloc[-2] if len(close) > 1 else close.iloc[-1]
            price_change = ((current_price - prev_close) / prev_close) * 100
            price_change_5d = ((current_price - close.iloc[-5]) / close.iloc[-5]) * 100 if len(close) >= 5 else 0
            
            # Volume Analysis
            avg_volume_20 = volume.tail(20).mean()
            volume_ratio = volume.iloc[-1] / avg_volume_20 if avg_volume_20 > 0 else 1
            
            # RSI
            rsi = self.calculate_rsi(close, 14)
            
            # Moving Averages
            sma_20 = close.rolling(20).mean().iloc[-1]
            sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else current_price
            ema_20 = close.ewm(span=20).mean().iloc[-1]
            
            # Support and Resistance
            support_1 = low.tail(20).min()
            resistance_1 = high.tail(20).max()
            support_2 = low.tail(50).min() if len(low) >= 50 else support_1
            resistance_2 = high.tail(50).max() if len(high) >= 50 else resistance_1
            
            # Volatility
            returns = close.pct_change().dropna()
            volatility_20 = returns.tail(20).std() * np.sqrt(252) * 100
            
            # ATR
            atr = self.calculate_atr(high, low, close, 14)
            
            # MACD
            macd, macd_signal, macd_histogram = self.calculate_macd(close)
            
            # Bollinger Bands
            bb_upper, bb_lower, bb_middle = self.calculate_bollinger_bands(close, 20)
            
            # Price position relative to indicators
            price_vs_sma20 = ((current_price - sma_20) / sma_20) * 100
            price_vs_sma50 = ((current_price - sma_50) / sma_50) * 100 if sma_50 else 0
            
            # Trend Analysis
            trend_short = "BULLISH" if current_price > ema_20 else "BEARISH"
            trend_medium = "BULLISH" if sma_20 > sma_50 else "BEARISH" if sma_20 < sma_50 else "SIDEWAYS"
            
            # Volume trend
            volume_trend = "INCREASING" if volume_ratio > 1.2 else "DECREASING" if volume_ratio < 0.8 else "STABLE"
            
            return {
                'current_price': current_price,
                'prev_close': prev_close,
                'price_change': price_change,
                'price_change_5d': price_change_5d,
                'volume_ratio': volume_ratio,
                'volume_trend': volume_trend,
                'rsi': rsi,
                'sma_20': sma_20,
                'sma_50': sma_50,
                'ema_20': ema_20,
                'support_1': support_1,
                'support_2': support_2,
                'resistance_1': resistance_1,
                'resistance_2': resistance_2,
                'volatility_20': volatility_20,
                'atr': atr,
                'atr_percent': (atr / current_price) * 100,
                'macd': macd,
                'macd_signal': macd_signal,
                'macd_histogram': macd_histogram,
                'bb_upper': bb_upper.iloc[-1],
                'bb_lower': bb_lower.iloc[-1],
                'bb_middle': bb_middle.iloc[-1],
                'price_vs_sma20': price_vs_sma20,
                'price_vs_sma50': price_vs_sma50,
                'trend_short': trend_short,
                'trend_medium': trend_medium,
                'analysis_type': analysis_type,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
        except Exception as e:
            print(f"❌ Error calculating indicators: {e}")
            return None

    def calculate_rsi(self, prices, period=14):
        """Calculate RSI"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if not np.isnan(rsi.iloc[-1]) else 50

    def calculate_atr(self, high, low, close, period=14):
        """Calculate Average True Range"""
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        return atr.iloc[-1] if not np.isnan(atr.iloc[-1]) else 0

    def calculate_macd(self, prices, fast=12, slow=26, signal=9):
        """Calculate MACD"""
        exp1 = prices.ewm(span=fast).mean()
        exp2 = prices.ewm(span=slow).mean()
        macd = exp1 - exp2
        macd_signal = macd.ewm(span=signal).mean()
        macd_histogram = macd - macd_signal
        return macd.iloc[-1], macd_signal.iloc[-1], macd_histogram.iloc[-1]

    def calculate_bollinger_bands(self, prices, period=20, std_dev=2):
        """Calculate Bollinger Bands"""
        middle = prices.rolling(period).mean()
        std = prices.rolling(period).std()
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        return upper, lower, middle

    def generate_analysis_report(self, symbol: str, indicators: Dict, analysis_type: str) -> Dict:
        """Generate comprehensive analysis report"""
        
        # Calculate signal strength
        bullish_score = self.calculate_bullish_score_single(indicators)
        bearish_score = self.calculate_bearish_score_single(indicators)
        
        # Determine primary signal
        if bullish_score > bearish_score + 1.0:
            primary_signal = "BULLISH"
            confidence = bullish_score
        elif bearish_score > bullish_score + 1.0:
            primary_signal = "BEARISH"
            confidence = bearish_score
        else:
            primary_signal = "NEUTRAL"
            confidence = max(bullish_score, bearish_score)
            
        # Generate trade levels
        trade_levels = self.generate_trade_levels(indicators, primary_signal)
        
        # Risk assessment
        risk_level = self.assess_risk_level(indicators)
        
        # Market condition
        market_condition = self.assess_market_condition(indicators)
        
        # Trading recommendations
        recommendations = self.generate_recommendations(indicators, primary_signal, analysis_type)
        
        return {
            'symbol': symbol.replace('.NS', ''),
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'analysis_type': analysis_type,
            'indicators': indicators,
            'trade_signal': {
                'direction': primary_signal,
                'confidence': confidence,
                'bullish_score': bullish_score,
                'bearish_score': bearish_score
            },
            'trade_levels': trade_levels,
            'risk_assessment': risk_level,
            'market_condition': market_condition,
            'recommendations': recommendations,
            'key_observations': self.get_key_observations(indicators)
        }

    def calculate_bullish_score_single(self, indicators: Dict) -> float:
        """Calculate bullish score for single stock analysis"""
        score = 0.0
        
        # Price above moving averages
        if indicators['current_price'] > indicators['sma_20']:
            score += 1.5
        if indicators['current_price'] > indicators['sma_50']:
            score += 1.5
            
        # RSI conditions
        if 30 <= indicators['rsi'] <= 70:
            score += 1.0
        if 40 <= indicators['rsi'] <= 60:
            score += 0.5
            
        # Volume confirmation
        if indicators['volume_ratio'] > 1.2:
            score += 1.0
            
        # MACD bullish
        if indicators['macd'] > indicators['macd_signal']:
            score += 1.0
            
        # Trend alignment
        if indicators['trend_short'] == "BULLISH":
            score += 1.0
        if indicators['trend_medium'] == "BULLISH":
            score += 1.0
            
        # Price momentum
        if indicators['price_change'] > 0:
            score += 0.5
        if indicators['price_change_5d'] > 0:
            score += 0.5
            
        # Support test
        support_distance = ((indicators['current_price'] - indicators['support_1']) / indicators['current_price']) * 100
        if 0 < support_distance <= 3:
            score += 1.0
            
        return min(score, 10.0)

    def calculate_bearish_score_single(self, indicators: Dict) -> float:
        """Calculate bearish score for single stock analysis"""
        score = 0.0
        
        # Price below moving averages
        if indicators['current_price'] < indicators['sma_20']:
            score += 1.5
        if indicators['current_price'] < indicators['sma_50']:
            score += 1.5
            
        # RSI conditions
        if indicators['rsi'] > 70:
            score += 1.5
        if indicators['rsi'] > 80:
            score += 1.0
            
        # Volume confirmation
        if indicators['volume_ratio'] > 1.2 and indicators['price_change'] < 0:
            score += 1.0
            
        # MACD bearish
        if indicators['macd'] < indicators['macd_signal']:
            score += 1.0
            
        # Trend alignment
        if indicators['trend_short'] == "BEARISH":
            score += 1.0
        if indicators['trend_medium'] == "BEARISH":
            score += 1.0
            
        # Price momentum
        if indicators['price_change'] < 0:
            score += 0.5
        if indicators['price_change_5d'] < 0:
            score += 0.5
            
        # Resistance test
        resistance_distance = ((indicators['resistance_1'] - indicators['current_price']) / indicators['current_price']) * 100
        if 0 < resistance_distance <= 3:
            score += 1.0
            
        return min(score, 10.0)

    def generate_trade_levels(self, indicators: Dict, direction: str) -> Dict:
        """Generate trade entry, target, stop loss levels"""
        current_price = indicators['current_price']
        atr_percent = indicators['atr_percent']
        
        if direction == "BULLISH":
            entry = current_price
            target_1 = current_price * (1 + (atr_percent * 1.5) / 100)
            target_2 = current_price * (1 + (atr_percent * 2.5) / 100)
            stop_loss = current_price * (1 - (atr_percent * 1.2) / 100)
        elif direction == "BEARISH":
            entry = current_price
            target_1 = current_price * (1 - (atr_percent * 1.5) / 100)
            target_2 = current_price * (1 - (atr_percent * 2.5) / 100)
            stop_loss = current_price * (1 + (atr_percent * 1.2) / 100)
        else:  # NEUTRAL
            entry = current_price
            target_1 = current_price * (1 + (atr_percent * 1.0) / 100)
            target_2 = current_price * (1 - (atr_percent * 1.0) / 100)
            stop_loss = None
            
        return {
            'entry': round(entry, 2),
            'target_1': round(target_1, 2),
            'target_2': round(target_2, 2),
            'stop_loss': round(stop_loss, 2) if stop_loss else None,
            'support_1': round(indicators['support_1'], 2),
            'support_2': round(indicators['support_2'], 2),
            'resistance_1': round(indicators['resistance_1'], 2),
            'resistance_2': round(indicators['resistance_2'], 2)
        }

    def assess_risk_level(self, indicators: Dict) -> Dict:
        """Assess risk level for the stock"""
        volatility = indicators['volatility_20']
        rsi = indicators['rsi']
        volume_ratio = indicators['volume_ratio']
        
        if volatility > 30:
            risk = "HIGH"
        elif volatility > 20:
            risk = "MEDIUM"
        else:
            risk = "LOW"
            
        # Additional risk factors
        risk_factors = []
        if rsi > 80 or rsi < 20:
            risk_factors.append("Extreme RSI")
        if volume_ratio < 0.7:
            risk_factors.append("Low Volume")
        if abs(indicators['price_change']) > 5:
            risk_factors.append("High Daily Move")
            
        return {
            'level': risk,
            'volatility': f"{volatility:.1f}%",
            'risk_factors': risk_factors,
            'atr_percent': f"{indicators['atr_percent']:.2f}%"
        }

    def assess_market_condition(self, indicators: Dict) -> Dict:
        """Assess overall market condition for the stock"""
        condition = ""
        
        if indicators['trend_medium'] == "BULLISH" and indicators['trend_short'] == "BULLISH":
            condition = "STRONG UPTREND"
        elif indicators['trend_medium'] == "BEARISH" and indicators['trend_short'] == "BEARISH":
            condition = "STRONG DOWNTREND"
        elif indicators['trend_medium'] == "BULLISH" and indicators['trend_short'] == "BEARISH":
            condition = "UPTREND PULLBACK"
        elif indicators['trend_medium'] == "BEARISH" and indicators['trend_short'] == "BULLISH":
            condition = "DOWNTREND BOUNCE"
        else:
            condition = "SIDEWAYS"
            
        return {
            'condition': condition,
            'trend_short': indicators['trend_short'],
            'trend_medium': indicators['trend_medium'],
            'volume_trend': indicators['volume_trend']
        }

    def generate_recommendations(self, indicators: Dict, signal: str, analysis_type: str) -> List[str]:
        """Generate trading recommendations"""
        recommendations = []
        
        if analysis_type in ["COMPREHENSIVE", "SWING"]:
            if signal == "BULLISH":
                recommendations.extend([
                    "Consider long position on dips to support",
                    "Use stop loss below recent swing low",
                    "Target resistance levels for profit booking",
                    "Monitor volume for confirmation"
                ])
            elif signal == "BEARISH":
                recommendations.extend([
                    "Consider short position on rallies to resistance",
                    "Use stop loss above recent swing high",
                    "Target support levels for profit booking",
                    "Watch for trend reversal signals"
                ])
            else:
                recommendations.extend([
                    "Wait for clearer direction",
                    "Consider range-bound strategies",
                    "Monitor breakout above resistance or below support",
                    "Reduce position size due to uncertainty"
                ])
                
        elif analysis_type == "INTRADAY":
            recommendations.extend([
                "Use tighter stop losses for intraday",
                "Monitor 5-minute charts for entry timing",
                "Book profits at first target",
                "Exit all positions before market close"
            ])
            
        # Add risk management recommendations
        recommendations.extend([
            f"Position size: {self.get_position_size(indicators['volatility_20'])}",
            "Diversify across sectors",
            "Monitor market news and events"
        ])
        
        return recommendations

    def get_position_size(self, volatility: float) -> str:
        """Recommend position size based on volatility"""
        if volatility > 25:
            return "1-2% of capital"
        elif volatility > 15:
            return "2-3% of capital"
        else:
            return "3-4% of capital"

    def get_key_observations(self, indicators: Dict) -> List[str]:
        """Get key technical observations"""
        observations = []
        
        # RSI observations
        if indicators['rsi'] > 70:
            observations.append("RSI indicates overbought conditions")
        elif indicators['rsi'] < 30:
            observations.append("RSI indicates oversold conditions")
        else:
            observations.append("RSI in neutral territory")
            
        # Volume observations
        if indicators['volume_ratio'] > 1.5:
            observations.append("High volume indicates strong interest")
        elif indicators['volume_ratio'] < 0.7:
            observations.append("Low volume suggests lack of conviction")
            
        # Trend observations
        if indicators['trend_short'] == indicators['trend_medium']:
            observations.append(f"Trend alignment: {indicators['trend_medium']} trend confirmed")
        else:
            observations.append(f"Trend divergence: Short-term {indicators['trend_short']}, Medium-term {indicators['trend_medium']}")
            
        # Price vs MA observations
        if indicators['price_vs_sma20'] > 5:
            observations.append("Price significantly above 20-day average")
        elif indicators['price_vs_sma20'] < -5:
            observations.append("Price significantly below 20-day average")
            
        return observations

    def display_stock_analysis(self, analysis: Dict, analysis_type: str):
        """Display the stock analysis in formatted output"""
        print(f"\n{'📊 STOCK ANALYSIS REPORT ':═^80}")
        print(f"📈 Symbol: {analysis['symbol']}")
        print(f"🕒 Time: {analysis['timestamp']}")
        print(f"🔍 Type: {analysis_type}")
        print("="*80)
        
        indicators = analysis['indicators']
        signal = analysis['trade_signal']
        levels = analysis['trade_levels']
        risk = analysis['risk_assessment']
        market = analysis['market_condition']
        
        # Price and Basic Info
        print(f"\n💰 PRICE ACTION:")
        print(f"   Current Price: ₹{indicators['current_price']:.2f}")
        print(f"   Previous Close: ₹{indicators['prev_close']:.2f}")
        print(f"   Daily Change: {indicators['price_change']:+.2f}%")
        print(f"   5-Day Change: {indicators['price_change_5d']:+.2f}%")
        print(f"   Volume Ratio: {indicators['volume_ratio']:.2f}x")
        
        # Technical Indicators
        print(f"\n📈 TECHNICAL INDICATORS:")
        print(f"   RSI (14): {indicators['rsi']:.1f}")
        print(f"   SMA 20: ₹{indicators['sma_20']:.2f}")
        print(f"   SMA 50: ₹{indicators['sma_50']:.2f}")
        print(f"   EMA 20: ₹{indicators['ema_20']:.2f}")
        print(f"   MACD: {indicators['macd']:.3f}")
        print(f"   ATR: {indicators['atr_percent']:.2f}%")
        print(f"   Volatility (20d): {indicators['volatility_20']:.1f}%")
        
        # Trade Signal
        print(f"\n🎯 TRADE SIGNAL:")
        signal_emoji = "🟢" if signal['direction'] == 'BULLISH' else "🔴" if signal['direction'] == 'BEARISH' else "⚪"
        print(f"   Direction: {signal_emoji} {signal['direction']}")
        print(f"   Confidence: {signal['confidence']:.1f}/10")
        print(f"   Bullish Score: {signal['bullish_score']:.1f}/10")
        print(f"   Bearish Score: {signal['bearish_score']:.1f}/10")
        
        # Trade Levels
        print(f"\n🎯 TRADE LEVELS:")
        print(f"   Entry: ₹{levels['entry']:.2f}")
        if levels['stop_loss']:
            print(f"   Stop Loss: ₹{levels['stop_loss']:.2f}")
        print(f"   Target 1: ₹{levels['target_1']:.2f}")
        print(f"   Target 2: ₹{levels['target_2']:.2f}")
        print(f"   Support 1: ₹{levels['support_1']:.2f}")
        print(f"   Support 2: ₹{levels['support_2']:.2f}")
        print(f"   Resistance 1: ₹{levels['resistance_1']:.2f}")
        print(f"   Resistance 2: ₹{levels['resistance_2']:.2f}")
        
        # Market Condition
        print(f"\n🌡️ MARKET CONDITION:")
        print(f"   Overall: {market['condition']}")
        print(f"   Short Trend: {market['trend_short']}")
        print(f"   Medium Trend: {market['trend_medium']}")
        print(f"   Volume Trend: {market['volume_trend']}")
        
        # Risk Assessment
        print(f"\n⚠️ RISK ASSESSMENT:")
        print(f"   Level: {risk['level']}")
        print(f"   Volatility: {risk['volatility']}")
        print(f"   ATR: {risk['atr_percent']}")
        if risk['risk_factors']:
            print(f"   Risk Factors: {', '.join(risk['risk_factors'])}")
        
        # Key Observations
        print(f"\n🔍 KEY OBSERVATIONS:")
        for obs in analysis['key_observations']:
            print(f"   • {obs}")
        
        # Recommendations
        print(f"\n💡 TRADING RECOMMENDATIONS:")
        for i, rec in enumerate(analysis['recommendations'], 1):
            print(f"   {i}. {rec}")
            
        print("="*80)

    def send_stock_analysis_alert(self, analysis: Dict):
        """Send stock analysis alert to Telegram"""
        if not self.telegram_enabled:
            return
            
        signal = analysis['trade_signal']
        levels = analysis['trade_levels']
        indicators = analysis['indicators']
        
        direction_emoji = "🟢" if signal['direction'] == 'BULLISH' else "🔴" if signal['direction'] == 'BEARISH' else "⚪"
        
        message = f"""
{direction_emoji} <b>STOCK ANALYSIS ALERT</b>

<b>{analysis['symbol']} - {signal['direction']} SIGNAL</b>
📊 Confidence: {signal['confidence']:.1f}/10

💰 <b>Price:</b> ₹{indicators['current_price']:.2f}
📈 <b>Change:</b> {indicators['price_change']:+.2f}%

🎯 <b>Trade Levels:</b>
Entry: ₹{levels['entry']:.2f}
Target 1: ₹{levels['target_1']:.2f}
Target 2: ₹{levels['target_2']:.2f}
Stop: ₹{levels['stop_loss']:.2f}

📊 <b>Key Indicators:</b>
RSI: {indicators['rsi']:.1f}
Volume: {indicators['volume_ratio']:.2f}x
Trend: {analysis['market_condition']['trend_short']}

⚠️ <b>Risk:</b> {analysis['risk_assessment']['level']}
💡 <b>Action:</b> {analysis['recommendations'][0]}

<i>Full analysis available in terminal</i>
        """
        
        self.send_telegram_alert(message)

    def analyze_multiple_stocks(self, symbols: List[str], analysis_type: str = "COMPREHENSIVE"):
        """Analyze multiple stocks and compare"""
        print(f"\n{'🔍 MULTI-STOCK ANALYSIS ':═^80}")
        print(f"Analyzing {len(symbols)} stocks...")
        
        results = []
        for symbol in symbols:
            print(f"📊 Analyzing {symbol}...")
            analysis = self.analyze_specific_stock(symbol, analysis_type)
            if analysis:
                results.append(analysis)
            time.sleep(1)  # Rate limiting
            
        # Sort by confidence
        results.sort(key=lambda x: x['trade_signal']['confidence'], reverse=True)
        
        # Display summary
        print(f"\n{'📈 ANALYSIS SUMMARY ':═^80}")
        print(f"{'Stock':<15} {'Signal':<10} {'Confidence':<12} {'Price':<10} {'Change':<8}")
        print("-" * 80)
        
        for result in results[:10]:  # Top 10
            signal = result['trade_signal']
            indicators = result['indicators']
            direction_emoji = "🟢" if signal['direction'] == 'BULLISH' else "🔴" if signal['direction'] == 'BEARISH' else "⚪"
            
            print(f"{result['symbol']:<15} {direction_emoji} {signal['direction']:<8} {signal['confidence']:<10.1f} ₹{indicators['current_price']:<8.2f} {indicators['price_change']:+.2f}%")
            
        return results

    # ----------------- AUTOMATIC SCHEDULING METHODS -----------------
    def setup_automatic_schedule(self):
        """Setup automatic scanning schedule as per strategy"""
        print("🕐 Setting up automatic schedule...")
        
        # Clear existing schedules
        schedule.clear()
        
        # ULTIMATE STRATEGY SCHEDULE
        # Intraday Scans
        schedule.every().day.at("09:15").do(self.run_intraday_scan_job).tag('intraday', 'morning')
        schedule.every().day.at("11:00").do(self.run_intraday_scan_job).tag('intraday', 'midday')
        schedule.every().day.at("13:30").do(self.run_intraday_scan_job).tag('intraday', 'afternoon')
        
        # Swing Scans
        schedule.every().day.at("09:20").do(self.run_swing_scan_job).tag('swing', 'morning')
        schedule.every().day.at("14:00").do(self.run_swing_scan_job).tag('swing', 'afternoon')
        
        # Monitoring & Alerts
        schedule.every().day.at("14:30").do(self.send_intraday_exit_alert).tag('alerts', 'exit')
        schedule.every().day.at("15:00").do(self.send_daily_summary).tag('alerts', 'summary')
        schedule.every().day.at("15:15").do(self.auto_close_intraday_positions).tag('trading', 'close')
        
        print("✅ Automatic schedule setup complete:")
        print("   🕘 9:15 AM - Intraday Scan 1")
        print("   🕘 9:20 AM - Swing Scan 1")
        print("   🕚 11:00 AM - Intraday Scan 2")
        print("   🕐 1:30 PM - Intraday Scan 3")
        print("   🕑 2:00 PM - Swing Scan 2")
        print("   🕑 2:30 PM - Intraday Exit Alert")
        print("   🕒 3:00 PM - Daily Summary")
        print("   🕒 3:15 PM - Auto Close Positions")

    def run_intraday_scan_job(self):
        """Scheduled intraday scan job"""
        if self.telegram_enabled:
            current_time = datetime.now().strftime("%H:%M")
            self.send_telegram_alert(f"⚡ <b>INTRADAY SCAN STARTED</b>\n⏰ {current_time}")
        
        print(f"\n{'='*60}")
        print(f"⚡ RUNNING SCHEDULED INTRADAY SCAN - {datetime.now().strftime('%H:%M')}")
        print(f"{'='*60}")
        
        try:
            bullish, bearish = self.intraday_scan()
            if bullish or bearish:
                # Auto-execute top 2 intraday trades
                top_trades = (bullish[:1] + bearish[:1])[:2]
                executed = 0
                for trade in top_trades:
                    if self.execute_paper_trade(trade):
                        executed += 1
                if executed > 0 and self.telegram_enabled:
                    self.send_telegram_alert(f"✅ Auto-executed {executed} intraday trades")
        except Exception as e:
            print(f"❌ Intraday scan error: {e}")
            if self.telegram_enabled:
                self.send_telegram_alert(f"❌ Intraday scan failed: {e}")

    def run_swing_scan_job(self):
        """Scheduled swing scan job"""
        if self.telegram_enabled:
            current_time = datetime.now().strftime("%H:%M")
            self.send_telegram_alert(f"🎯 <b>SWING SCAN STARTED</b>\n⏰ {current_time}")
        
        print(f"\n{'='*60}")
        print(f"🎯 RUNNING SCHEDULED SWING SCAN - {datetime.now().strftime('%H:%M')}")
        print(f"{'='*60}")
        
        try:
            bullish, bearish = self.ultimate_dual_side_scan()
            if bullish or bearish:
                # Auto-execute top swing trade
                top_trade = (bullish[:1] + bearish[:1])[:1]
                if top_trade and self.execute_paper_trade(top_trade[0]):
                    if self.telegram_enabled:
                        self.send_telegram_alert("✅ Auto-executed top swing trade")
        except Exception as e:
            print(f"❌ Swing scan error: {e}")
            if self.telegram_enabled:
                self.send_telegram_alert(f"❌ Swing scan failed: {e}")

    def send_intraday_exit_alert(self):
        """Send intraday exit reminder"""
        if self.telegram_enabled:
            message = "🚨 <b>INTRADAY EXIT REMINDER</b>\n\n" \
                     "⏰ 2:30 PM - Start exiting intraday positions\n" \
                     "🎯 Book profits on achieving targets\n" \
                     "🛑 Respect stop losses\n" \
                     "⚡ All intraday positions MUST be closed by 3:15 PM"
            self.send_telegram_alert(message)

    def send_daily_summary(self):
        """Send daily trading summary"""
        self.update_paper_positions()
        
        open_positions = [p for p in self.paper_portfolio['positions'].values() 
                         if p.get('trade_mode') == 'INTRADAY']
        
        if self.telegram_enabled:
            message = f"📊 <b>DAILY TRADING SUMMARY</b>\n\n" \
                     f"📅 {datetime.now().strftime('%d %b %Y')}\n" \
                     f"💼 Portfolio: ₹{self.paper_portfolio['total_value']:.2f}\n" \
                     f"⚡ Open Intraday: {len(open_positions)}\n" \
                     f"📈 Market: {self.scan_stats['market_volatility']}\n\n" \
                     f"<i>Review today's performance and plan for tomorrow!</i>"
            self.send_telegram_alert(message)

    def auto_close_intraday_positions(self):
        """Automatically close all intraday positions"""
        positions_closed = 0
        for position_key, position in list(self.paper_portfolio['positions'].items()):
            if position.get('trade_mode') == 'INTRADAY':
                try:
                    symbol = position['symbol'] + '.NS'
                    stock = yf.Ticker(symbol)
                    current_data = stock.history(period='1d', interval='1m')
                    
                    if not current_data.empty:
                        current_price = current_data['Close'].iloc[-1]
                        self.close_paper_position(position_key, current_price, "AUTO CLOSE - MARKET HOURS END")
                        positions_closed += 1
                except Exception as e:
                    print(f"❌ Error auto-closing {position['symbol']}: {e}")
        
        if positions_closed > 0 and self.telegram_enabled:
            self.send_telegram_alert(f"🔒 Auto-closed {positions_closed} intraday positions")

    def start_automatic_scheduler(self):
        """Start the automatic scheduler"""
        if self.scheduler_running:
            print("✅ Scheduler is already running")
            return
        
        self.setup_automatic_schedule()
        self.scheduler_running = True
        
        print("🚀 Starting automatic scheduler...")
        if self.telegram_enabled:
            self.send_telegram_alert("🤖 <b>AUTOMATIC SCHEDULER STARTED</b>\n\n" \
                                   "⚡ Trading bot is now running automatically!\n" \
                                   "📊 Scans will run at scheduled times\n" \
                                   "🔔 You will receive real-time alerts\n" \
                                   "💎 Bot: @AnvikSOD2026_bot")
        
        # Run scheduler in a separate thread
        def run_scheduler():
            while self.scheduler_running:
                try:
                    schedule.run_pending()
                    time.sleep(1)
                except Exception as e:
                    print(f"❌ Scheduler error: {e}")
                    time.sleep(10)
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        
        print("✅ Automatic scheduler started successfully!")
        print("   The bot will now run 24/7 according to the schedule")
        print("   Check your Telegram channel for real-time alerts")

    def stop_automatic_scheduler(self):
        """Stop the automatic scheduler"""
        self.scheduler_running = False
        schedule.clear()
        print("🛑 Automatic scheduler stopped")
        if self.telegram_enabled:
            self.send_telegram_alert("🛑 <b>AUTOMATIC SCHEDULER STOPPED</b>")

    # ----------------- LOAD/SAVE METHODS -----------------
    def load_trade_history(self):
        """Load previous trade history from file"""
        try:
            if os.path.exists('trade_history.json'):
                with open('trade_history.json', 'r') as f:
                    data = json.load(f)
                    self.trade_log = data.get('trade_log', [])
                    self.performance_data = data.get('performance_data', [])
                    self.paper_portfolio = data.get('paper_portfolio', 
                        {'cash': 100000, 'positions': {}, 'total_value': 100000})
                print("📊 Loaded previous trade history")
        except Exception as e:
            print(f"❌ Error loading trade history: {e}")
            # Initialize with defaults
            self.trade_log = []
            self.performance_data = []
            self.paper_portfolio = {'cash': 100000, 'positions': {}, 'total_value': 100000}

    def save_trade_history(self):
        """Save trade history to file"""
        try:
            data = {
                'trade_log': self.trade_log,
                'performance_data': self.performance_data,
                'paper_portfolio': self.paper_portfolio,
                'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open('trade_history.json', 'w') as f:
                json.dump(data, f, indent=2)
            print("💾 Trade history saved")
        except Exception as e:
            print(f"❌ Error saving trade history: {e}")

    # ----------------- SWING TRADING METHODS -----------------
    def get_stock_data_batch(self, symbols: List[str]) -> Dict:
        """Get batch data for multiple symbols"""
        data = {}
        for symbol in symbols:
            try:
                stock = yf.Ticker(symbol)
                hist = stock.history(period="1mo", interval="1d")
                if not hist.empty and len(hist) > 20:
                    data[symbol] = hist
            except Exception as e:
                continue
        return data

    def calculate_indicators(self, df: pd.DataFrame) -> Dict:
        """Calculate technical indicators for swing trading"""
        if df is None or len(df) < 20:
            return None
            
        try:
            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']
            
            current_price = close.iloc[-1]
            prev_close = close.iloc[-2] if len(close) > 1 else close.iloc[-1]
            price_change = ((current_price - prev_close) / prev_close) * 100
            
            # Volume analysis
            avg_volume = volume.tail(20).mean()
            volume_ratio = volume.iloc[-1] / avg_volume if avg_volume > 0 else 1
            
            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            avg_gain = gain.rolling(window=14, min_periods=1).mean()
            avg_loss = loss.rolling(window=14, min_periods=1).mean()
            
            rs = avg_gain / avg_loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            rsi = rsi.iloc[-1] if not np.isnan(rsi.iloc[-1]) else 50
            
            # Support and resistance
            recent_high = high.tail(20).max()
            recent_low = low.tail(20).min()
            
            # Volatility
            returns = close.pct_change().dropna()
            volatility = returns.std() * np.sqrt(252) * 100
            
            # ATR
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
            
            return {
                'current_price': current_price,
                'prev_close': prev_close,
                'price_change': price_change,
                'volume_ratio': volume_ratio,
                'rsi': rsi,
                'resistance': recent_high * 0.98,
                'support': recent_low * 1.02,
                'recent_high': recent_high,
                'recent_low': recent_low,
                'atr': atr,
                'volatility': volatility,
                'true_range': atr / current_price * 100
            }
        except Exception as e:
            return None

    def calculate_bullish_score(self, ind: Dict) -> float:
        """Calculate bullish score for swing trading"""
        score = 0.0
        
        # Volume strength
        if ind['volume_ratio'] >= 2.0: score += 3.0
        elif ind['volume_ratio'] >= 1.5: score += 2.0
        elif ind['volume_ratio'] >= 1.2: score += 1.0
        
        # RSI conditions
        if 30 <= ind['rsi'] <= 75: 
            score += 2.0
            if 40 <= ind['rsi'] <= 70: score += 1.0
        
        # Distance to resistance
        resistance_distance = ((ind['resistance'] - ind['current_price']) / ind['current_price']) * 100
        if 2 <= resistance_distance <= 8: score += 2.5
        elif resistance_distance < 2: score += 1.0
        elif 8 < resistance_distance <= 12: score += 1.0
        
        # Price momentum
        if ind['price_change'] > 2.0: score += 1.5
        elif ind['price_change'] > 1.0: score += 1.0
        
        return min(score, 10.0)

    def calculate_bearish_score(self, ind: Dict) -> float:
        """Calculate bearish score for swing trading"""
        score = 0.0
        
        # Volume + price confirmation
        if ind['volume_ratio'] >= 2.0 and ind['price_change'] < -2.0: score += 3.0
        elif ind['volume_ratio'] >= 1.5 and ind['price_change'] < -1.0: score += 2.0
        
        # RSI overbought
        if ind['rsi'] > 80: score += 2.5
        elif ind['rsi'] > 75: score += 1.5
        
        # Distance to support
        support_distance = ((ind['current_price'] - ind['support']) / ind['current_price']) * 100
        if 2 <= support_distance <= 8: score += 2.5
        elif support_distance < 2: score += 1.0
        elif 8 < support_distance <= 12: score += 1.0
        
        # Price momentum down
        if ind['price_change'] < -2.0: score += 1.5
        elif ind['price_change'] < -1.0: score += 1.0
        
        return min(score, 10.0)

    def detect_signals(self, ind: Dict) -> List[Tuple]:
        """Detect trading signals for swing trading"""
        if not ind: 
            return []
            
        # Filter extreme moves
        if abs(ind['price_change']) > 8:
            return []
            
        if ind['volatility'] > 60:
            return []
            
        bullish = self.calculate_bullish_score(ind)
        bearish = self.calculate_bearish_score(ind)
        
        bull_pot = self.calculate_potential(ind, 'BULLISH', bullish)
        bear_pot = self.calculate_potential(ind, 'BEARISH', bearish)
        
        signals = []
        
        min_directional_bias = 1.5
        
        if (bullish >= self.min_confidence and 
            bull_pot >= self.required_move and 
            bullish - bearish >= min_directional_bias):
            signals.append(('BULLISH', bull_pot, bullish))
            
        if (bearish >= self.min_confidence and 
            bear_pot >= self.required_move and 
            bearish - bullish >= min_directional_bias):
            signals.append(('BEARISH', bear_pot, bearish))
            
        return signals

    def calculate_potential(self, ind: Dict, direction: str, power: float) -> float:
        """Calculate potential move for swing trading"""
        base = 0.0
        
        # Volume strength
        vr = ind['volume_ratio']
        if vr >= 2.5: base += 2.5
        elif vr >= 2.0: base += 2.0
        elif vr >= 1.5: base += 1.5
        
        # Signal strength
        base += (power / 10) * 2.0
        
        # Distance to key levels
        if direction == 'BULLISH':
            distance = ((ind['resistance'] - ind['current_price']) / ind['current_price']) * 100
            if 3 <= distance <= 10: base += 2.0
            elif distance > 10: base += 1.0
        else:
            distance = ((ind['current_price'] - ind['support']) / ind['current_price']) * 100
            if 3 <= distance <= 10: base += 2.0
            elif distance > 10: base += 1.0
        
        # RSI extremes
        rsi = ind['rsi']
        if (direction == 'BULLISH' and rsi < 35) or (direction == 'BEARISH' and rsi > 85):
            base += 1.5
            
        # Price momentum
        if direction == 'BULLISH' and ind['price_change'] > 2.0:
            base += 1.0
        elif direction == 'BEARISH' and ind['price_change'] < -2.0:
            base += 1.0
            
        return min(base, 8.0)

    def trade_plan(self, symbol: str, direction: str, potential: float, confidence: float, 
                   price: float, indicators: Dict):
        """Create trade plan for swing trading"""
        
        # Adaptive holding period based on volatility
        volatility = indicators['volatility']
        if volatility > 25:
            holding_days = self.adaptive_holding['HIGH_VOLATILITY']
            trade_type = "SWING_HIGH_VOL"
        elif volatility > 15:
            holding_days = self.adaptive_holding['MEDIUM_VOLATILITY']
            trade_type = "SWING_MED_VOL"
        else:
            holding_days = self.adaptive_holding['LOW_VOLATILITY']
            trade_type = "SWING_LOW_VOL"
        
        daily_move = potential / holding_days
        
        # Smart stop loss calculation
        atr_stop = indicators['true_range'] * 1.5
        volatility_stop = min(max(indicators['volatility'] * 0.12, 2.0), 6.0)
        base_stop_pct = max(atr_stop, volatility_stop)
        
        if direction == 'BULLISH':
            target = round(price * (1 + potential/100), 1)
            stop = round(price * (1 - base_stop_pct/100), 1)
            rr = round((target - price) / (price - stop), 2) if price > stop else 1
        else:
            target = round(price * (1 - potential/100), 1)
            stop = round(price * (1 + base_stop_pct/100), 1)
            rr = round((price - target) / (stop - price), 2) if stop > price else 1
        
        # Position sizing based on confidence and RR
        if confidence >= 8.0 and rr >= 2.0: 
            size = "3-4%"
            paper_qty = 3
        elif confidence >= 7.5 and rr >= 1.8: 
            size = "2-3%"
            paper_qty = 2
        elif confidence >= 7.0 and rr >= 1.5: 
            size = "1-2%"
            paper_qty = 1
        else: 
            size = "0.5-1%"
            paper_qty = 1
            
        if rr < self.min_rr_ratio:
            return None
            
        return {
            'symbol': symbol,
            'direction': direction,
            'entry': price,
            'target': target,
            'stop_loss': stop,
            'risk_reward': rr,
            'holding_period': f"{holding_days} days",
            'daily_move': daily_move,
            'position_size': size,
            'trade_type': trade_type,
            'confidence': confidence,
            'potential': potential,
            'paper_quantity': paper_qty,
            'volatility': indicators['volatility'],
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'trade_mode': 'SWING'
        }

    def ultimate_dual_side_scan(self):
        """Main swing trading scan function"""
        print(f"🚀 ULTIMATE SWING SCANNING {len(self.nse_fo_stocks)} STOCKS...")
        
        market_volatility = self.analyze_market_volatility()
        print(f"📊 Market Volatility: {market_volatility}")
        
        if self.telegram_enabled:
            self.send_telegram_alert(
                f"🔍 <b>Starting ULTIMATE Swing Scan</b>\n"
                f"📈 Market: {market_volatility}\n"
                f"⏰ Time: {datetime.now().strftime('%H:%M')}\n"
                f"💎 Analyzing {len(self.nse_fo_stocks)} stocks..."
            )
        
        bullish_trades = []
        bearish_trades = []
        start = time.time()
        
        for batch_num in range(0, len(self.nse_fo_stocks), self.scan_batch_size):
            batch_symbols = self.nse_fo_stocks[batch_num:batch_num + self.scan_batch_size]
            
            print(f"🔄 Processing batch {batch_num//self.scan_batch_size + 1}...")
            
            batch_data = self.get_stock_data_batch(batch_symbols)
            
            for symbol, data in batch_data.items():
                try:
                    indicators = self.calculate_indicators(data)
                    if not indicators or indicators['current_price'] < self.min_price:
                        continue
                    
                    signals = self.detect_signals(indicators)
                    if signals:
                        for direction, potential, confidence in signals:
                            trade_plan = self.trade_plan(
                                symbol.replace('.NS', ''), 
                                direction, potential, confidence,
                                indicators['current_price'], indicators
                            )
                            
                            if trade_plan:
                                if direction == 'BULLISH':
                                    bullish_trades.append(trade_plan)
                                else:
                                    bearish_trades.append(trade_plan)
                
                except Exception as e:
                    continue
            
            if batch_num < len(self.nse_fo_stocks) - self.scan_batch_size:
                time.sleep(2)  # Rate limiting
        
        # Sort and filter top signals
        bullish_trades.sort(key=lambda x: (x['confidence'], x['potential']), reverse=True)
        bearish_trades.sort(key=lambda x: (x['confidence'], x['potential']), reverse=True)
        
        top_bullish = bullish_trades[:self.max_signals_per_side]
        top_bearish = bearish_trades[:self.max_signals_per_side]
        
        scan_time = time.time() - start
        
        # Update stats
        self.scan_stats.update({
            'total_scanned': len(self.nse_fo_stocks),
            'successful_data': len(batch_data),
            'bullish_signals': len(top_bullish),
            'bearish_signals': len(top_bearish),
            'market_volatility': market_volatility
        })
        
        # Send alerts
        if self.telegram_enabled:
            self.send_swing_alerts(top_bullish, top_bearish)
            self.send_scan_complete_alert(len(top_bullish), len(top_bearish), scan_time, market_volatility)
        
        print(f"🎯 ULTIMATE SCAN COMPLETE in {scan_time:.1f}s")
        print(f"📈 Found {len(top_bullish)} bullish calls and {len(top_bearish)} bearish puts")
        
        return top_bullish, top_bearish

    def send_swing_alerts(self, bullish_trades: List[Dict], bearish_trades: List[Dict]):
        """Send swing trading alerts"""
        if not self.telegram_enabled:
            return
        
        current_time = datetime.now().strftime("%H:%M")
        
        if bullish_trades:
            calls_message = f"🟢 <b>SWING CALL OPPORTUNITIES</b> 🟢\n⏰ {current_time}\n\n"
            
            for i, trade in enumerate(bullish_trades, 1):
                calls_message += (
                    f"<b>{i}. {trade['symbol']} - SWING CALL</b>\n"
                    f"💰 <b>Entry:</b> ₹{trade['entry']:.1f} | Target: ₹{trade['target']:.1f} (+{trade['potential']:.1f}%)\n"
                    f"🛑 <b>Stop:</b> ₹{trade['stop_loss']:.1f} | R:R: 1:{trade['risk_reward']:.2f}\n"
                    f"📊 Conf: {trade['confidence']:.1f}/10 | Size: {trade['position_size']}\n"
                    f"⏳ Hold: {trade['holding_period']} | Vol: {trade['volatility']:.1f}%\n\n"
                )
            
            self.send_telegram_alert(calls_message)
            
        if bearish_trades:
            puts_message = f"🔴 <b>SWING PUT OPPORTUNITIES</b> 🔴\n⏰ {current_time}\n\n"
            
            for i, trade in enumerate(bearish_trades, 1):
                puts_message += (
                    f"<b>{i}. {trade['symbol']} - SWING PUT</b>\n"
                    f"💰 <b>Entry:</b> ₹{trade['entry']:.1f} | Target: ₹{trade['target']:.1f} (-{trade['potential']:.1f}%)\n"
                    f"🛑 <b>Stop:</b> ₹{trade['stop_loss']:.1f} | R:R: 1:{trade['risk_reward']:.2f}\n"
                    f"📊 Conf: {trade['confidence']:.1f}/10 | Size: {trade['position_size']}\n"
                    f"⏳ Hold: {trade['holding_period']} | Vol: {trade['volatility']:.1f}%\n\n"
                )
            
            self.send_telegram_alert(puts_message)

    def send_scan_complete_alert(self, bullish_count: int, bearish_count: int, scan_time: float, volatility: str):
        """Send scan completion alert"""
        if not self.telegram_enabled:
            return
            
        total_signals = bullish_count + bearish_count
        
        if total_signals > 0:
            quality = "🔥 EXCELLENT" if total_signals >= 8 else "✅ GOOD" if total_signals >= 4 else "⚠️ MODERATE"
        else:
            quality = "❌ NO SIGNALS"
        
        message = f"""
📊 <b>ULTIMATE SCAN COMPLETE</b>

{quality} - Found {total_signals} high-quality signals:
🟢 {bullish_count} CALL opportunities  
🔴 {bearish_count} PUT opportunities

📈 Market: {volatility}
⏱️ Scan time: {scan_time:.1f}s

💎 <i>Next: Review positions and execute trades</i>
        """
        self.send_telegram_alert(message)

    # ----------------- INTRADAY SPECIFIC METHODS -----------------
    def get_intraday_data(self, symbol: str) -> pd.DataFrame:
        """Get intraday data for the current day"""
        try:
            stock = yf.Ticker(symbol)
            # Get 5-minute data for current day
            df = stock.history(period='1d', interval='5m', auto_adjust=True)
            
            if df.empty or len(df) < 10:
                return None
                
            # Filter for today's data only
            today = datetime.now().date()
            df = df[df.index.date == today]
            
            if len(df) < 5:  # Need at least 5 periods of data
                return None
                
            return df
        except Exception as e:
            return None

    def calculate_intraday_indicators(self, df: pd.DataFrame) -> Dict:
        """Calculate intraday specific indicators"""
        if df is None or len(df) < 5:
            return None
            
        try:
            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']
            
            current_price = close.iloc[-1]
            prev_close = close.iloc[-2] if len(close) > 1 else close.iloc[-1]
            price_change = ((current_price - prev_close) / prev_close) * 100
            
            # Intraday volume analysis
            avg_volume = volume.tail(10).mean()
            volume_ratio = volume.iloc[-1] / avg_volume if avg_volume > 0 else 1
            
            # Intraday RSI (shorter period)
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            avg_gain = gain.rolling(window=8, min_periods=1).mean()  # Shorter period for intraday
            avg_loss = loss.rolling(window=8, min_periods=1).mean()
            
            rs = avg_gain / avg_loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            rsi = rsi.iloc[-1] if not np.isnan(rsi.iloc[-1]) else 50
            
            # Intraday support/resistance
            recent_high = high.tail(15).max()  # Shorter period
            recent_low = low.tail(15).min()
            
            # Intraday volatility
            returns = close.pct_change().dropna()
            intraday_volatility = returns.std() * np.sqrt(78) * 100  # 78 trading periods in day
            
            # Intraday ATR
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(8).mean().iloc[-1]  # Shorter period
            
            return {
                'current_price': current_price,
                'prev_close': prev_close,
                'price_change': price_change,
                'volume_ratio': volume_ratio,
                'rsi': rsi,
                'resistance': recent_high * 0.995,  # Tighter resistance
                'support': recent_low * 1.005,      # Tighter support
                'recent_high': recent_high,
                'recent_low': recent_low,
                'atr': atr,
                'volatility': intraday_volatility,
                'true_range': atr / current_price * 100,
                'intraday_high': high.max(),
                'intraday_low': low.min()
            }
        except Exception as e:
            return None

    def calculate_intraday_bullish_score(self, ind: Dict) -> float:
        """Intraday specific bullish scoring"""
        score = 0.0
        
        # Intraday volume is crucial
        if ind['volume_ratio'] >= 3.0: score += 4.0
        elif ind['volume_ratio'] >= 2.0: score += 2.5
        elif ind['volume_ratio'] >= 1.5: score += 1.5
        
        # Intraday RSI ranges
        if 25 <= ind['rsi'] <= 70: 
            score += 2.0
            if 35 <= ind['rsi'] <= 65: score += 1.0
        
        # Distance to intraday resistance
        resistance_distance = ((ind['resistance'] - ind['current_price']) / ind['current_price']) * 100
        if 0.5 <= resistance_distance <= 3.0: score += 2.5
        elif resistance_distance < 0.5: score += 1.0
        elif 3.0 < resistance_distance <= 5.0: score += 1.0
        
        # Price momentum
        if ind['price_change'] > 1.0: score += 1.5
        elif ind['price_change'] > 0.5: score += 1.0
        
        # Near intraday high
        high_distance = ((ind['intraday_high'] - ind['current_price']) / ind['current_price']) * 100
        if high_distance <= 1.0: score += 1.5
        
        return min(score, 10.0)

    def calculate_intraday_bearish_score(self, ind: Dict) -> float:
        """Intraday specific bearish scoring"""
        score = 0.0
        
        # Volume + price confirmation
        if ind['volume_ratio'] >= 2.5 and ind['price_change'] < -1.0: score += 4.0
        elif ind['volume_ratio'] >= 2.0 and ind['price_change'] < -0.5: score += 2.5
        
        # Intraday RSI overbought
        if ind['rsi'] > 75: score += 2.5
        elif ind['rsi'] > 70: score += 1.5
        
        # Distance to intraday support
        support_distance = ((ind['current_price'] - ind['support']) / ind['current_price']) * 100
        if 0.5 <= support_distance <= 3.0: score += 2.5
        elif support_distance < 0.5: score += 1.0
        elif 3.0 < support_distance <= 5.0: score += 1.0
        
        # Price momentum down
        if ind['price_change'] < -1.0: score += 1.5
        elif ind['price_change'] < -0.5: score += 1.0
        
        # Near intraday low
        low_distance = ((ind['current_price'] - ind['intraday_low']) / ind['current_price']) * 100
        if low_distance <= 1.0: score += 1.5
        
        return min(score, 10.0)

    def detect_intraday_signals(self, ind: Dict) -> List[Tuple]:
        """Intraday signal detection"""
        if not ind: 
            return []
            
        # Filter extreme intraday moves
        if abs(ind['price_change']) > 5:
            return []
            
        if ind['volatility'] > 40:
            return []
            
        bullish = self.calculate_intraday_bullish_score(ind)
        bearish = self.calculate_intraday_bearish_score(ind)
        
        bull_pot = self.calculate_intraday_potential(ind, 'BULLISH', bullish)
        bear_pot = self.calculate_intraday_potential(ind, 'BEARISH', bearish)
        
        signals = []
        
        min_directional_bias = 1.0  # Lower for intraday
        
        if (bullish >= self.intraday_min_confidence and 
            bull_pot >= self.intraday_required_move and 
            bullish - bearish >= min_directional_bias):
            signals.append(('BULLISH', bull_pot, bullish))
            
        if (bearish >= self.intraday_min_confidence and 
            bear_pot >= self.intraday_required_move and 
            bearish - bullish >= min_directional_bias):
            signals.append(('BEARISH', bear_pot, bearish))
            
        return signals

    def calculate_intraday_potential(self, ind: Dict, direction: str, power: float) -> float:
        """Calculate intraday potential"""
        base = 0.0
        
        # Volume strength
        vr = ind['volume_ratio']
        if vr >= 2.5: base += 2.0
        elif vr >= 2.0: base += 1.5
        elif vr >= 1.5: base += 1.0
        
        # Signal strength
        base += (power / 10) * 1.5
        
        # Distance to key levels
        if direction == 'BULLISH':
            distance = ((ind['resistance'] - ind['current_price']) / ind['current_price']) * 100
            if 1 <= distance <= 4: base += 1.5
            elif distance > 4: base += 0.5
        else:
            distance = ((ind['current_price'] - ind['support']) / ind['current_price']) * 100
            if 1 <= distance <= 4: base += 1.5
            elif distance > 4: base += 0.5
        
        # RSI extremes
        rsi = ind['rsi']
        if (direction == 'BULLISH' and rsi < 30) or (direction == 'BEARISH' and rsi > 80):
            base += 1.0
            
        # Price momentum
        if direction == 'BULLISH' and ind['price_change'] > 1.0:
            base += 0.5
        elif direction == 'BEARISH' and ind['price_change'] < -1.0:
            base += 0.5
            
        return min(base, 6.0)  # Lower max potential for intraday

    def intraday_trade_plan(self, symbol: str, direction: str, potential: float, confidence: float, 
                           price: float, indicators: Dict):
        """Intraday trade planning"""
        
        # Intraday specific parameters
        holding = "INTRADAY"  # Always intraday
        daily_move = potential
        
        # Tighter stops for intraday
        atr_stop = indicators['true_range'] * 1.2  # Tighter than swing
        volatility_stop = min(max(indicators['volatility'] * 0.08, 1.0), 3.0)  # Tighter stops
        base_stop_pct = max(atr_stop, volatility_stop)
        
        if direction == 'BULLISH':
            realistic_potential = min(potential, 5.0)  # Lower max for intraday
            target = round(price * (1 + realistic_potential/100), 1)
            stop = round(price * (1 - base_stop_pct/100), 1)
            rr = round((target - price) / (price - stop), 2) if price > stop else 1
        else:
            realistic_potential = min(potential, 5.0)
            target = round(price * (1 - realistic_potential/100), 1)
            stop = round(price * (1 + base_stop_pct/100), 1)
            rr = round((price - target) / (stop - price), 2) if stop > price else 1
        
        # Intraday position sizing (smaller)
        if confidence >= 7.5 and rr >= 1.8: 
            size = "2-3%"
            trade_type = "INTRADAY_CORE"
            paper_qty = 2
        elif confidence >= 6.5 and rr >= 1.5: 
            size = "1-2%"
            trade_type = "INTRADAY_SWING" 
            paper_qty = 1
        else: 
            size = "0.5-1%"
            trade_type = "INTRADAY_TACTICAL"
            paper_qty = 1
            
        if rr < 1.2:  # Lower RR requirement for intraday
            return None
            
        return {
            'symbol': symbol,
            'direction': direction,
            'entry': price,
            'target': target,
            'stop_loss': stop,
            'risk_reward': rr,
            'holding_period': holding,
            'daily_move': daily_move,
            'position_size': size,
            'trade_type': trade_type,
            'confidence': confidence,
            'potential': realistic_potential,
            'paper_quantity': paper_qty,
            'volatility': indicators['volatility'],
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'trade_mode': 'INTRADAY'
        }

    def intraday_scan(self):
        """Main intraday scanning function"""
        print(f"🚀 INTRADAY SCANNING {len(self.nse_fo_stocks)} STOCKS...")
        
        market_volatility = self.analyze_market_volatility()
        print(f"📊 Market Volatility: {market_volatility}")
        
        if self.telegram_enabled:
            self.send_telegram_alert(
                f"🔍 <b>Starting INTRADAY Scan</b>\n"
                f"📈 Market: {market_volatility}\n"
                f"⏰ Time: {datetime.now().strftime('%H:%M')}\n"
                f"💎 Analyzing {len(self.nse_fo_stocks)} stocks for intraday opportunities..."
            )
        
        bullish_trades = []
        bearish_trades = []
        start = time.time()
        
        batch_size = 10  # Smaller batches for faster intraday scanning
        total_batches = (len(self.nse_fo_stocks) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min((batch_num + 1) * batch_size, len(self.nse_fo_stocks))
            batch_symbols = self.nse_fo_stocks[start_idx:end_idx]
            
            print(f"🔄 Processing batch {batch_num + 1}/{total_batches} ({len(batch_symbols)} stocks)...")
            
            for symbol in batch_symbols:
                try:
                    # Get intraday data
                    df = self.get_intraday_data(symbol)
                    if df is None:
                        continue
                    
                    # Calculate intraday indicators
                    indicators = self.calculate_intraday_indicators(df)
                    if not indicators or indicators['current_price'] < self.min_price:
                        continue
                    
                    # Detect intraday signals
                    signals = self.detect_intraday_signals(indicators)
                    if signals:
                        for direction, potential, confidence in signals:
                            trade_plan = self.intraday_trade_plan(
                                symbol.replace('.NS', ''), 
                                direction, potential, confidence,
                                indicators['current_price'], indicators
                            )
                            
                            if trade_plan:
                                # Send quick intraday alert for high-confidence trades
                                if confidence >= 7.0:
                                    self.send_intraday_quick_alert(trade_plan)
                                
                                if direction == 'BULLISH':
                                    bullish_trades.append(trade_plan)
                                else:
                                    bearish_trades.append(trade_plan)
                
                except Exception as e:
                    continue
            
            if batch_num < total_batches - 1:
                time.sleep(1)  # Shorter delay for intraday
        
        # Sort and filter top signals
        bullish_trades.sort(key=lambda x: (x['confidence'], x['potential']), reverse=True)
        bearish_trades.sort(key=lambda x: (x['confidence'], x['potential']), reverse=True)
        
        top_bullish = bullish_trades[:self.intraday_max_signals]
        top_bearish = bearish_trades[:self.intraday_max_signals]
        
        scan_time = time.time() - start
        
        # Send intraday alerts
        if self.telegram_enabled:
            self.send_intraday_alerts(top_bullish, top_bearish)
            self.send_intraday_scan_complete_alert(len(top_bullish), len(top_bearish), scan_time, market_volatility)
        
        print(f"🎯 INTRADAY SCAN COMPLETE in {scan_time:.1f}s")
        print(f"📈 Found {len(top_bullish)} bullish calls and {len(top_bearish)} bearish puts")
        
        return top_bullish, top_bearish

    # ----------------- INTRADAY TELEGRAM ALERTS -----------------
    def send_intraday_alerts(self, bullish_trades: List[Dict], bearish_trades: List[Dict]):
        """Send intraday specific alerts"""
        if not self.telegram_enabled:
            return
        
        current_time = datetime.now().strftime("%H:%M")
        
        # Send INTRADAY CALLS alert
        if bullish_trades:
            calls_message = f"🟢 <b>INTRADAY CALL OPPORTUNITIES</b> 🟢\n⏰ {current_time}\n\n"
            
            for i, trade in enumerate(bullish_trades, 1):
                calls_message += (
                    f"<b>{i}. {trade['symbol']} - INTRADAY CALL</b>\n"
                    f"💰 <b>Entry:</b> ₹{trade['entry']:.1f} | Target: ₹{trade['target']:.1f} (+{trade['potential']:.1f}%)\n"
                    f"🛑 <b>Stop:</b> ₹{trade['stop_loss']:.1f} | R:R: 1:{trade['risk_reward']:.2f}\n"
                    f"📊 Conf: {trade['confidence']:.1f}/10 | Size: {trade['position_size']}\n"
                    f"⚡ Hold: {trade['holding_period']} | Vol: {trade['volatility']:.1f}%\n\n"
                )
            
            calls_message += "💡 <i>Intraday trades - Exit by 3:15 PM</i>"
            self.send_telegram_alert(calls_message)
            
        # Send INTRADAY PUTS alert  
        if bearish_trades:
            puts_message = f"🔴 <b>INTRADAY PUT OPPORTUNITIES</b> 🔴\n⏰ {current_time}\n\n"
            
            for i, trade in enumerate(bearish_trades, 1):
                puts_message += (
                    f"<b>{i}. {trade['symbol']} - INTRADAY PUT</b>\n"
                    f"💰 <b>Entry:</b> ₹{trade['entry']:.1f} | Target: ₹{trade['target']:.1f} (-{trade['potential']:.1f}%)\n"
                    f"🛑 <b>Stop:</b> ₹{trade['stop_loss']:.1f} | R:R: 1:{trade['risk_reward']:.2f}\n"
                    f"📊 Conf: {trade['confidence']:.1f}/10 | Size: {trade['position_size']}\n"
                    f"⚡ Hold: {trade['holding_period']} | Vol: {trade['volatility']:.1f}%\n\n"
                )
            
            puts_message += "💡 <i>Intraday trades - Exit by 3:15 PM</i>"
            self.send_telegram_alert(puts_message)

    def send_intraday_scan_complete_alert(self, bullish_count: int, bearish_count: int, scan_time: float, volatility: str):
        """Send intraday scan completion alert"""
        if not self.telegram_enabled:
            return
            
        total_signals = bullish_count + bearish_count
        current_time = datetime.now().strftime("%H:%M")
        
        if total_signals > 0:
            quality = "🔥 EXCELLENT" if total_signals >= 8 else "✅ GOOD" if total_signals >= 4 else "⚠️ MODERATE"
        else:
            quality = "❌ NO SIGNALS"
        
        message = f"""
📊 <b>INTRADAY SCAN COMPLETE</b>
⏰ {current_time}

{quality} - Found {total_signals} intraday signals:
🟢 {bullish_count} CALL opportunities  
🔴 {bearish_count} PUT opportunities

⚡ <b>INTRADAY TRADING RULES:</b>
• Entry: Immediate or on minor pullbacks
• Stoploss: Strict intraday stops
• Target: Book full/partial by 2:30 PM
• Exit: MUST exit by 3:15 PM
• No carry forward positions

📈 Market: {volatility}
⏱️ Scan time: {scan_time:.1f}s

💎 <i>Intraday signals - Higher frequency, smaller moves</i>
        """
        self.send_telegram_alert(message)

    def send_intraday_quick_alert(self, trade_plan: Dict):
        """Send quick intraday alert for high-confidence trades"""
        if not self.telegram_enabled:
            return
            
        if trade_plan['confidence'] >= 7.5:
            urgency = "🚨 URGENT INTRADAY"
            emoji = "⚡"
        elif trade_plan['confidence'] >= 7.0:
            urgency = "⚠️ INTRADAY SIGNAL"
            emoji = "🎯"
        else:
            return
            
        direction_icon = "🟢" if trade_plan['direction'] == 'BULLISH' else "🔴"
        direction_text = "CALL" if trade_plan['direction'] == 'BULLISH' else "PUT"
        current_time = datetime.now().strftime("%H:%M")
        
        message = f"""
{urgency} {emoji}
⏰ {current_time}

{direction_icon} <b>INTRADAY: {trade_plan['symbol']} {direction_text}</b>

💰 <b>Entry:</b> ₹{trade_plan['entry']:.1f}
🎯 <b>Target:</b> ₹{trade_plan['target']:.1f} ({trade_plan['potential']:+.1f}%)
🛑 <b>Stop:</b> ₹{trade_plan['stop_loss']:.1f}
⚖️ <b>R:R:</b> 1:{trade_plan['risk_reward']:.2f}

📊 Conf: {trade_plan['confidence']:.1f}/10 | Size: {trade_plan['position_size']}

<i>Intraday trade - Exit by 3:15 PM</i>
        """
        
        self.send_telegram_alert(message)

    # ----------------- DISPLAY METHODS -----------------
    def show_ultimate_trades(self, bullish_trades: List[Dict], bearish_trades: List[Dict]):
        """Display swing trades in formatted table"""
        current_time = datetime.now().strftime("%H:%M")
        
        print(f"\n{'🎯 ULTIMATE F&O TRADE SIGNALS ':═^90}")
        print(f"⏰ Time: {current_time}")
        print("-" * 90)
        
        if bullish_trades:
            print(f"\n{'🟢 BULLISH CALLS ':═^90}")
            print(f"{'Stock':<12} {'Entry':<8} {'Target':<8} {'Stop':<8} {'R:R':<6} {'Conf':<5} {'Pot%':<5} {'Hold':<8} {'Size':<10}")
            print("─" * 90)
            for trade in bullish_trades:
                print(f"🟢 {trade['symbol']:<10} ₹{trade['entry']:<7.1f} ₹{trade['target']:<7.1f} "
                      f"₹{trade['stop_loss']:<7.1f} {trade['risk_reward']:<5.2f} {trade['confidence']:<4.1f} "
                      f"{trade['potential']:<4.1f}% {trade['holding_period']:<7} {trade['position_size']:<9}")
        
        if bearish_trades:
            print(f"\n{'🔴 BEARISH PUTS ':═^90}")
            print(f"{'Stock':<12} {'Entry':<8} {'Target':<8} {'Stop':<8} {'R:R':<6} {'Conf':<5} {'Pot%':<5} {'Hold':<8} {'Size':<10}")
            print("─" * 90)
            for trade in bearish_trades:
                print(f"🔴 {trade['symbol']:<10} ₹{trade['entry']:<7.1f} ₹{trade['target']:<7.1f} "
                      f"₹{trade['stop_loss']:<7.1f} {trade['risk_reward']:<5.2f} {trade['confidence']:<4.1f} "
                      f"{trade['potential']:<4.1f}% {trade['holding_period']:<7} {trade['position_size']:<9}")

    def show_intraday_trades(self, bullish_trades: List[Dict], bearish_trades: List[Dict]):
        """Display intraday trades"""
        current_time = datetime.now().strftime("%H:%M")
        
        print(f"\n{'🎯 INTRADAY TRADE SIGNALS ':═^90}")
        print(f"⏰ Time: {current_time}")
        print("-" * 90)
        
        # Compact table view for intraday calls
        if bullish_trades:
            print(f"\n{'🟢 INTRADAY CALLS ':═^90}")
            print(f"{'Stock':<12} {'Entry':<8} {'Target':<8} {'Stop':<8} {'R:R':<6} {'Conf':<5} {'Pot%':<5} {'Size':<10}")
            print("─" * 90)
            for trade in bullish_trades:
                print(f"🟢 {trade['symbol']:<10} ₹{trade['entry']:<7.1f} ₹{trade['target']:<7.1f} "
                      f"₹{trade['stop_loss']:<7.1f} {trade['risk_reward']:<5.2f} {trade['confidence']:<4.1f} "
                      f"{trade['potential']:<4.1f}% {trade['position_size']:<9}")
        
        # Compact table view for intraday puts
        if bearish_trades:
            print(f"\n{'🔴 INTRADAY PUTS ':═^90}")
            print(f"{'Stock':<12} {'Entry':<8} {'Target':<8} {'Stop':<8} {'R:R':<6} {'Conf':<5} {'Pot%':<5} {'Size':<10}")
            print("─" * 90)
            for trade in bearish_trades:
                print(f"🔴 {trade['symbol']:<10} ₹{trade['entry']:<7.1f} ₹{trade['target']:<7.1f} "
                      f"₹{trade['stop_loss']:<7.1f} {trade['risk_reward']:<5.2f} {trade['confidence']:<4.1f} "
                      f"{trade['potential']:<4.1f}% {trade['position_size']:<9}")

        # Show intraday risk management
        print(f"\n{'⚡ INTRADAY RISK MANAGEMENT ':═^90}")
        print("""
⚡ INTRADAY TRADING RULES:
--------------------------------------------------------------------------------
   • MAX holding: Same day only (exit by 3:15 PM)
   • Tighter stop losses (1-3%)
   • Smaller position sizes (0.5-3%)
   • Target smaller moves (1-5%)
   • No overnight positions
   • Monitor continuously
   • Use strict discipline
        """)

    # ----------------- UTILITY METHODS -----------------
    def analyze_market_volatility(self) -> str:
        """Analyze current market volatility"""
        try:
            # Use NIFTY 50 as market proxy
            nifty = yf.Ticker("^NSEI")
            hist = nifty.history(period="1mo", interval="1d")
            
            if hist.empty:
                return "MEDIUM_VOLATILITY"
                
            returns = hist['Close'].pct_change().dropna()
            volatility = returns.std() * np.sqrt(252) * 100  # Annualized volatility
            
            if volatility > 25:
                return "HIGH_VOLATILITY"
            elif volatility > 15:
                return "MEDIUM_VOLATILITY"
            else:
                return "LOW_VOLATILITY"
        except:
            return "MEDIUM_VOLATILITY"

    def get_expiry_status(self) -> Dict:
        """Get F&O expiry information"""
        today = datetime.now().date()
        
        # Find next Thursday (weekly expiry)
        days_ahead = 3 - today.weekday()  # Thursday is 3
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        next_thursday = today + timedelta(days=days_ahead)
        
        # Find last Thursday of month (monthly expiry)
        next_month = today.replace(day=28) + timedelta(days=4)
        last_thursday = next_month - timedelta(days=(next_month.weekday() - 3) % 7)
        
        days_to_weekly = (next_thursday - today).days
        days_to_monthly = (last_thursday - today).days
        
        status = "NEAR_EXPIRY" if days_to_weekly <= 2 else "NORMAL"
        
        return {
            'next_weekly_expiry': next_thursday.strftime("%d %b"),
            'next_monthly_expiry': last_thursday.strftime("%d %b"),
            'days_to_weekly': days_to_weekly,
            'days_to_monthly': days_to_monthly,
            'status': status
        }

    def get_intraday_timing_advice(self):
        """Get intraday timing advice based on current time"""
        now = datetime.now().time()
        current_hour = now.hour
        current_minute = now.minute
        
        if current_hour == 9 and current_minute >= 15:
            return "🎯 PERFECT INTRADAY TIMING - Morning momentum"
        elif current_hour == 10:
            return "✅ GOOD INTRADAY TIMING - Early morning consolidation"
        elif current_hour == 11:
            return "👍 DECENT INTRADAY TIMING - Mid-morning moves"
        elif current_hour == 12:
            return "⚠️ CAUTIOUS - Lunch time lull"
        elif current_hour == 13:
            return "✅ GOOD INTRADAY TIMING - Post-lunch momentum"
        elif current_hour == 14:
            return "⏰ FINAL INTRADAY OPPORTUNITIES - Pre-close moves"
        elif current_hour == 15:
            return "🚨 URGENT - Exit all intraday positions by 3:15 PM"
        else:
            return "⏰ MARKET CLOSED - No intraday trading"

    def print_ultimate_stats(self):
        """Print scanning statistics"""
        print(f"\n{'📊 SCAN STATISTICS ':═^90}")
        print(f"Total Stocks Scanned: {self.scan_stats['total_scanned']}")
        print(f"Successful Data Fetch: {self.scan_stats['successful_data']}")
        print(f"Bullish Signals Found: {self.scan_stats['bullish_signals']}")
        print(f"Bearish Signals Found: {self.scan_stats['bearish_signals']}")
        print(f"Market Volatility: {self.scan_stats['market_volatility']}")

    def suggest_ultimate_strategies(self, bullish_trades: List[Dict], bearish_trades: List[Dict]):
        """Suggest trading strategies"""
        print(f"\n{'💡 TRADING STRATEGIES ':═^90}")
        
        if bullish_trades:
            print("🟢 BULLISH STRATEGIES:")
            print("   • Buy CE (Call Options) - OTM or ATM")
            print("   • Bull Call Spread for defined risk")
            print("   • Long Futures with strict stop loss")
            print("   • Buy on dips towards support")
            
        if bearish_trades:
            print("🔴 BEARISH STRATEGIES:")
            print("   • Buy PE (Put Options) - OTM or ATM") 
            print("   • Bear Put Spread for defined risk")
            print("   • Short Futures with strict stop loss")
            print("   • Sell on rallies towards resistance")
            
        print("\n⚡ RISK MANAGEMENT:")
        print("   • Max 3-4% capital per trade")
        print("   • Stop loss: 2-6% based on volatility")
        print("   • Target: 1:1.5+ Risk-Reward ratio")
        print("   • Diversify across sectors")

    # ----------------- PAPER TRADING METHODS -----------------
    def execute_paper_trade(self, trade_plan: Dict) -> bool:
        """Execute paper trade based on trade plan"""
        try:
            symbol = trade_plan['symbol']
            direction = trade_plan['direction']
            quantity = trade_plan.get('paper_quantity', 1)
            price = trade_plan['entry']
            
            trade_value = price * quantity * 100  # Assuming lot size of 100 for F&O
            if trade_value > self.paper_portfolio['cash']:
                print(f"❌ Insufficient cash for {symbol} trade")
                return False
                
            # Execute trade
            self.paper_portfolio['cash'] -= trade_value
            position_key = f"{symbol}_{direction}_{datetime.now().strftime('%H%M%S')}"
            
            self.paper_portfolio['positions'][position_key] = {
                'symbol': symbol,
                'direction': direction,
                'quantity': quantity,
                'entry_price': price,
                'entry_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'stop_loss': trade_plan['stop_loss'],
                'target': trade_plan['target'],
                'trade_mode': trade_plan.get('trade_mode', 'SWING'),
                'trade_plan': trade_plan
            }
            
            # Log trade
            trade_record = {
                'symbol': symbol,
                'direction': direction,
                'quantity': quantity,
                'entry_price': price,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'trade_type': trade_plan.get('trade_mode', 'SWING'),
                'confidence': trade_plan['confidence'],
                'status': 'OPEN'
            }
            self.trade_log.append(trade_record)
            
            print(f"✅ Paper trade executed: {symbol} {direction} {quantity} lots @ ₹{price}")
            
            # Send trade alert
            if self.telegram_enabled:
                direction_icon = "🟢" if direction == 'BULLISH' else "🔴"
                direction_text = "CALL" if direction == 'BULLISH' else "PUT"
                message = f"""
{direction_icon} <b>PAPER TRADE EXECUTED</b>

<b>{direction_text} on {symbol}</b>
💰 Entry: ₹{price:.1f}
🎯 Target: ₹{trade_plan['target']:.1f}
🛑 Stop: ₹{trade_plan['stop_loss']:.1f}
⚖️ R:R: 1:{trade_plan['risk_reward']:.2f}

📊 Mode: {trade_plan.get('trade_mode', 'SWING')}
💼 Size: {trade_plan['position_size']}

<i>Paper trading simulation</i>
                """
                self.send_telegram_alert(message)
            
            # Save trade history
            self.save_trade_history()
            return True
            
        except Exception as e:
            print(f"❌ Error executing paper trade: {e}")
            return False

    def update_paper_positions(self):
        """Update paper portfolio with current prices"""
        try:
            total_value = self.paper_portfolio['cash']
            positions_to_remove = []
            
            for position_key, position in self.paper_portfolio['positions'].items():
                symbol = position['symbol'] + '.NS'
                try:
                    stock = yf.Ticker(symbol)
                    current_data = stock.history(period='1d', interval='1m')
                    
                    if not current_data.empty:
                        current_price = current_data['Close'].iloc[-1]
                        position['current_price'] = current_price
                        position_value = current_price * position['quantity'] * 100  # Lot size 100
                        total_value += position_value
                        
                        # Check for stop loss or target hit
                        if position['direction'] == 'BULLISH':
                            if current_price <= position['stop_loss']:
                                print(f"🛑 STOP LOSS hit for {position['symbol']}")
                                positions_to_remove.append((position_key, current_price, "STOP LOSS"))
                            elif current_price >= position['target']:
                                print(f"🎯 TARGET hit for {position['symbol']}")
                                positions_to_remove.append((position_key, current_price, "TARGET"))
                        else:  # BEARISH
                            if current_price >= position['stop_loss']:
                                print(f"🛑 STOP LOSS hit for {position['symbol']}")
                                positions_to_remove.append((position_key, current_price, "STOP LOSS"))
                            elif current_price <= position['target']:
                                print(f"🎯 TARGET hit for {position['symbol']}")
                                positions_to_remove.append((position_key, current_price, "TARGET"))
                                
                except Exception as e:
                    print(f"❌ Error updating {position['symbol']}: {e}")
                    continue
            
            # Remove closed positions
            for position_key, current_price, reason in positions_to_remove:
                self.close_paper_position(position_key, current_price, reason)
                
            self.paper_portfolio['total_value'] = total_value
            
        except Exception as e:
            print(f"❌ Error updating portfolio: {e}")

    def close_paper_position(self, position_key: str, exit_price: float, reason: str):
        """Close a paper trading position"""
        try:
            position = self.paper_portfolio['positions'][position_key]
            
            # Calculate P&L
            pnl = (exit_price - position['entry_price']) * position['quantity'] * 100
            if position['direction'] == 'BEARISH':
                pnl = -pnl  # Reverse for short positions
            
            # Add cash back to portfolio
            trade_value = exit_price * position['quantity'] * 100
            self.paper_portfolio['cash'] += trade_value
            
            # Record performance
            self.performance_data.append({
                'symbol': position['symbol'],
                'direction': position['direction'],
                'pnl': pnl,
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'exit_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'trade_mode': position.get('trade_mode', 'SWING'),
                'exit_reason': reason
            })
            
            # Update trade log
            for trade in self.trade_log:
                if (trade['symbol'] == position['symbol'] and 
                    trade['direction'] == position['direction'] and 
                    trade.get('status') == 'OPEN'):
                    trade['status'] = 'CLOSED'
                    trade['exit_price'] = exit_price
                    trade['exit_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    trade['pnl'] = pnl
                    break
            
            # Remove position
            del self.paper_portfolio['positions'][position_key]
            
            print(f"📊 Closed {position['symbol']} {position['direction']}: P&L ₹{pnl:.2f} ({reason})")
            
            # Send closure alert
            if self.telegram_enabled:
                pnl_icon = "🟢" if pnl > 0 else "🔴"
                direction_text = "CALL" if position['direction'] == 'BULLISH' else "PUT"
                message = f"""
{pnl_icon} <b>POSITION CLOSED</b>

<b>{direction_text} on {position['symbol']}</b>
💰 Exit: ₹{exit_price:.1f}
📊 P&L: ₹{pnl:.2f}
🎯 Reason: {reason}

<i>Paper trading result</i>
                """
                self.send_telegram_alert(message)
            
            # Save updated portfolio
            self.save_trade_history()
            
        except Exception as e:
            print(f"❌ Error closing position: {e}")

    def generate_performance_report(self):
        """Generate performance report"""
        if not self.performance_data:
            print("No trade performance data available")
            return
            
        total_pnl = sum(trade['pnl'] for trade in self.performance_data)
        winning_trades = [t for t in self.performance_data if t['pnl'] > 0]
        losing_trades = [t for t in self.performance_data if t['pnl'] < 0]
        
        win_rate = len(winning_trades) / len(self.performance_data) * 100 if self.performance_data else 0
        
        print(f"\n{'📊 PERFORMANCE REPORT ':═^80}")
        print(f"Total Trades: {len(self.performance_data)}")
        print(f"Winning Trades: {len(winning_trades)}")
        print(f"Losing Trades: {len(losing_trades)}")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Total P&L: ₹{total_pnl:.2f}")
        print(f"Current Portfolio Value: ₹{self.paper_portfolio['total_value']:.2f}")
        
        # Show recent trades
        if self.performance_data[-5:]:
            print(f"\n{'Recent Trades:':<20}")
            for trade in self.performance_data[-5:]:
                status = "PROFIT" if trade['pnl'] > 0 else "LOSS"
                print(f"  {trade['symbol']} {trade['direction']}: ₹{trade['pnl']:.2f} ({status})")

    # ----------------- ENHANCED MAIN MENU -----------------
    def show_main_menu(self):
        """Enhanced main menu with Single Stock Analysis option"""
        print("\n" + "="*70)
        print("🤖 ULTIMATE F&O TRADER v11.0 - WITH SINGLE STOCK ANALYSIS")
        print("="*70)
        print("1. 🚀 Start Automatic Scheduler (24/7)")
        print("2. 🛑 Stop Automatic Scheduler")
        print("3. 📊 Ultimate F&O Scan (Swing Trades)")
        print("4. ⚡ Intraday Scan (Same Day Trades)")
        print("5. 🔍 Single Stock Analysis (NEW!)")
        print("6. 📝 Execute Paper Trades")
        print("7. 🔍 Monitor Portfolio (5 minutes)")
        print("8. 📈 Generate Performance Report")
        print("9. 💡 Show Trading Strategies")
        print("10. 🎯 Show Market & Timing Analysis")
        print("11. ⚙️ Settings & Configuration")
        print("12. 🔧 Test Telegram Connection")
        print("13. 🚪 Exit")
        print("="*70)
        
        # Show scheduler status
        status = "✅ RUNNING" if self.scheduler_running else "❌ STOPPED"
        telegram_status = "✅ ENABLED" if self.telegram_enabled else "❌ DISABLED"
        print(f"🔄 Scheduler Status: {status}")
        print(f"📱 Telegram Status: {telegram_status}")

    def show_schedule_status(self):
        """Show current schedule status"""
        print("\n" + "="*60)
        print("📅 CURRENT SCHEDULE STATUS")
        print("="*60)
        print(f"🔄 Scheduler Running: {'✅ YES' if self.scheduler_running else '❌ NO'}")
        
        if self.scheduler_running:
            print("\n🕐 Scheduled Jobs:")
            jobs = schedule.get_jobs()
            for job in jobs:
                print(f"   • {job}")
        
        print("\n🎯 Strategy Schedule:")
        print("   🕘 9:15 AM - Intraday Scan 1")
        print("   🕘 9:20 AM - Swing Scan 1")
        print("   🕚 11:00 AM - Intraday Scan 2")
        print("   🕐 1:30 PM - Intraday Scan 3")
        print("   🕑 2:00 PM - Swing Scan 2")
        print("   🕑 2:30 PM - Intraday Exit Alert")
        print("   🕒 3:00 PM - Daily Summary")
        print("   🕒 3:15 PM - Auto Close Positions")

    def show_settings(self):
        """Show and modify settings"""
        print("\n" + "="*60)
        print("⚙️ SETTINGS & CONFIGURATION")
        print("="*60)
        print(f"1. Telegram Alerts: {'✅ ENABLED' if self.telegram_enabled else '❌ DISABLED'}")
        print(f"2. Auto Trading: {'✅ ENABLED' if self.auto_trading_enabled else '❌ DISABLED'}")
        print(f"3. Telegram Channel: {self.telegram_chat_id}")
        print(f"4. Min Confidence: {self.min_confidence}")
        print(f"5. Min Risk-Reward: {self.min_rr_ratio}")
        
        choice = input("\nSelect setting to modify (or Enter to go back): ").strip()
        if choice == "1":
            self.telegram_enabled = not self.telegram_enabled
            print(f"Telegram alerts: {'ENABLED' if self.telegram_enabled else 'DISABLED'}")
        elif choice == "2":
            self.auto_trading_enabled = not self.auto_trading_enabled
            print(f"Auto trading: {'ENABLED' if self.auto_trading_enabled else 'DISABLED'}")

# ----------------- UPDATED MAIN FUNCTION -----------------
def main():
    trader = UltimateFNOTrader()
    
    print("╔══════════════════════════════════════════════════════════╗")
    print("║      ULTIMATE F&O TRADER v11.0 - SINGLE STOCK ANALYSIS  ║")
    print("║      🤖 24/7 SCHEDULING + STOCK ANALYSIS ⚡           ║")
    print("╚══════════════════════════════════════════════════════════╝")
    
    while True:
        trader.show_main_menu()
        
        try:
            choice = input("Select option: ").strip()
            
            if choice == "1":
                trader.start_automatic_scheduler()
                print("\n💡 The bot will now run 24/7 according to the schedule.")
                print("   You can minimize this window - everything runs automatically!")
                print("   Check your Telegram channel for real-time alerts.")
                
            elif choice == "2":
                trader.stop_automatic_scheduler()
                
            elif choice == "3":
                print("\n🧠 Running ULTIMATE SWING analysis...")
                bullish, bearish = trader.ultimate_dual_side_scan()
                trader.show_ultimate_trades(bullish, bearish)
                trader.suggest_ultimate_strategies(bullish, bearish)
                trader.print_ultimate_stats()
                
            elif choice == "4":
                print("\n⚡ Running INTRADAY analysis...")
                bullish, bearish = trader.intraday_scan()
                trader.show_intraday_trades(bullish, bearish)
                print("\n💡 INTRADAY STRATEGIES:")
                print("   • Use smaller position sizes")
                print("   • Set tight stop losses")
                print("   • Exit all positions by 3:15 PM")
                print("   • Focus on high-volume stocks")
                print("   • Monitor continuously")
                
            elif choice == "5":  # NEW SINGLE STOCK ANALYSIS
                print("\n🎯 SINGLE STOCK ANALYSIS")
                print("1. Analyze specific stock")
                print("2. Analyze multiple stocks")
                print("3. Analyze from F&O list")
                analysis_choice = input("Select analysis type (1/2/3): ").strip()
                
                if analysis_choice == "1":
                    symbol = input("Enter stock symbol (e.g., RELIANCE, TCS): ").strip().upper()
                    print("\n📊 Analysis Types:")
                    print("1. Comprehensive Analysis")
                    print("2. Technical Analysis")
                    print("3. Intraday Analysis")
                    print("4. Swing Analysis")
                    analysis_type_choice = input("Select analysis type (1/2/3/4): ").strip()
                    
                    analysis_types = {
                        "1": "COMPREHENSIVE",
                        "2": "TECHNICAL", 
                        "3": "INTRADAY",
                        "4": "SWING"
                    }
                    
                    analysis_type = analysis_types.get(analysis_type_choice, "COMPREHENSIVE")
                    trader.analyze_specific_stock(symbol, analysis_type)
                    
                elif analysis_choice == "2":
                    symbols_input = input("Enter stock symbols (comma-separated, e.g., RELIANCE,TCS,INFY): ").strip()
                    symbols = [s.strip().upper() for s in symbols_input.split(',')]
                    trader.analyze_multiple_stocks(symbols)
                    
                elif analysis_choice == "3":
                    # Analyze random 5 stocks from F&O list
                    import random
                    random_stocks = random.sample(trader.nse_fo_stocks, min(5, len(trader.nse_fo_stocks)))
                    symbols = [s.replace('.NS', '') for s in random_stocks]
                    print(f"🔍 Analyzing random F&O stocks: {', '.join(symbols)}")
                    trader.analyze_multiple_stocks(symbols)
                    
                else:
                    print("❌ Invalid choice")
                    
            elif choice == "6":
                print("\n📊 Choose trade type:")
                print("   1. Swing Trades")
                print("   2. Intraday Trades")
                trade_type = input("Select (1/2): ").strip()
                
                if trade_type == "1":
                    bullish, bearish = trader.ultimate_dual_side_scan()
                elif trade_type == "2":
                    bullish, bearish = trader.intraday_scan()
                else:
                    print("❌ Invalid selection")
                    continue
                
                if bullish or bearish:
                    print("\nSelect trades to execute:")
                    if bullish:
                        print("🟢 BULLISH CALLS:")
                        for i, trade in enumerate(bullish, 1):
                            trade_mode = trade.get('trade_mode', 'SWING')
                            print(f"   {i}. {trade['symbol']} - {trade_mode} - Conf: {trade['confidence']} - Pot: {trade['potential']}%")
                    
                    if bearish:
                        print("🔴 BEARISH PUTS:")
                        for i, trade in enumerate(bearish, 1):
                            base_idx = len(bullish) if bullish else 0
                            trade_mode = trade.get('trade_mode', 'SWING')
                            print(f"   {base_idx + i}. {trade['symbol']} - {trade_mode} - Conf: {trade['confidence']} - Pot: {trade['potential']}%")
                    
                    try:
                        selection = input("Enter trade numbers (comma-separated) or 'all': ").strip()
                        if selection.lower() == 'all':
                            trades_to_execute = bullish + bearish
                        else:
                            indices = [int(x.strip()) - 1 for x in selection.split(',')]
                            all_trades = bullish + bearish
                            trades_to_execute = [all_trades[i] for i in indices if i < len(all_trades)]
                        
                        executed_count = 0
                        for trade in trades_to_execute:
                            if trader.execute_paper_trade(trade):
                                executed_count += 1
                        print(f"✅ Executed {executed_count} paper trades")
                        
                    except Exception as e:
                        print(f"❌ Error executing trades: {e}")
                else:
                    print("❌ No valid trades to execute")
                    
            elif choice == "7":
                print("\n🔍 Monitoring portfolio for 5 minutes...")
                start_time = time.time()
                while time.time() - start_time < 300:
                    trader.update_paper_positions()
                    time.sleep(60)
                    print(f"📈 Portfolio Value: ₹{trader.paper_portfolio['total_value']:.2f}")
                trader.generate_performance_report()
                
            elif choice == "8":
                trader.generate_performance_report()
                
            elif choice == "9":
                print("\n📊 Choose strategy type:")
                print("   1. Swing Trading Strategies")
                print("   2. Intraday Trading Strategies")
                strategy_type = input("Select (1/2): ").strip()
                
                if strategy_type == "1":
                    bullish, bearish = trader.ultimate_dual_side_scan()
                    trader.suggest_ultimate_strategies(bullish, bearish)
                elif strategy_type == "2":
                    print("\n💡 INTRADAY TRADING STRATEGIES:")
                    print("   • Scalping: 0.5-1% moves, quick entries/exits")
                    print("   • Momentum: Ride strong intraday trends")
                    print("   • Breakout: Trade break of key intraday levels")
                    print("   • Mean reversion: Fade extreme moves")
                    print("   • News based: React to intraday news flows")
                else:
                    print("❌ Invalid selection")
                
            elif choice == "10":
                volatility = trader.analyze_market_volatility()
                expiry_status = trader.get_expiry_status()
                timing_advice = trader.get_intraday_timing_advice()
                
                print(f"\n📊 ULTIMATE MARKET ANALYSIS:")
                print(f"   Current Volatility: {volatility}")
                print(f"   Expiry Status: {expiry_status['status']}")
                print(f"   Days to Weekly: {expiry_status['days_to_weekly']}")
                print(f"   Days to Monthly: {expiry_status['days_to_monthly']}")
                print(f"   Trading Advice: {timing_advice}")
                trader.print_ultimate_stats()
                
            elif choice == "11":
                trader.show_settings()
                
            elif choice == "12":
                trader.test_telegram_immediately()
                
            elif choice == "13":
                trader.stop_automatic_scheduler()
                trader.save_trade_history()
                print("👋 Thank you for using Ultimate F&O Trader!")
                if trader.telegram_enabled:
                    trader.send_telegram_alert("🛑 Ultimate F&O Trader stopped by user")
                break
                
            else:
                print("❌ Invalid option. Please try again.")
                
        except KeyboardInterrupt:
            print("\n🛑 Operation cancelled by user")
            continue
        except Exception as e:
            print(f"❌ Error: {e}")
            continue

if __name__ == "__main__":
    # Install required packages
    try:
        import schedule
    except ImportError:
        print("📦 Installing required packages...")
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "schedule"])
        import schedule
    
    main()