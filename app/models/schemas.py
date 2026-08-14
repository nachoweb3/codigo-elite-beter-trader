from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class TransactionType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SWAP = "swap"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class TokenType(str, Enum):
    SPL = "spl"
    NATIVE = "native"
    NFT = "nft"


class Transaction(BaseModel):
    signature: str
    timestamp: datetime
    type: TransactionType
    token_address: str
    token_symbol: Optional[str] = None
    token_name: Optional[str] = None
    token_logo: Optional[str] = None
    token_amount: float
    sol_amount: float
    price_per_token: float
    fee: float = 0.0
    slot: Optional[int] = None


class TokenBalance(BaseModel):
    mint: str
    symbol: str
    name: Optional[str] = None
    logo: Optional[str] = None
    decimals: int
    amount: float
    amount_raw: int
    value_usd: Optional[float] = None


class TokenStats(BaseModel):
    token_address: str
    token_symbol: Optional[str] = None
    token_name: Optional[str] = None
    token_logo: Optional[str] = None
    total_bought: float
    total_sold: float
    current_holdings: float
    total_volume_sol: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    roi_percent: float
    trades_count: int
    first_buy: Optional[datetime] = None
    last_sell: Optional[datetime] = None
    avg_buy_price: float
    avg_sell_price: float
    is_still_holding: bool
    # Precio actual y PnL no realizado en tiempo real (opcional)
    current_price_sol: Optional[float] = None
    current_price_usd: Optional[float] = None
    unrealized_pnl_sol: Optional[float] = None
    unrealized_roi_percent: Optional[float] = None


class TradingMetrics(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_volume: float
    total_fees: float
    avg_trade_size: float
    avg_hold_time_seconds: float
    largest_win: float
    largest_loss: float
    profit_factor: float
    sharpe_ratio: Optional[float] = None


class PatternAnalysis(BaseModel):
    description: str
    confidence: float
    suggestion: str


class Recommendation(BaseModel):
    type: str
    priority: str  # low, medium, high
    title: str
    description: str
    actionable: bool
    data: dict = {}


class WalletProfile(BaseModel):
    """Perfil de estilo de trading detectado automáticamente por wallet."""
    style: str
    label: str
    emoji: str
    description: str
    strengths: List[str] = []
    weaknesses: List[str] = []
    signals: dict = {}


class TimeOfDayInsight(BaseModel):
    """Insights sobre la franja horaria donde la wallet opera mejor."""
    best_hour: Optional[int] = None
    best_hour_pnl: float = 0.0
    best_hour_trades: int = 0
    worst_hour: Optional[int] = None
    worst_hour_pnl: float = 0.0
    by_hour: List[dict] = []
    summary: str = ""


class TradingScore(BaseModel):
    """Puntuación global de trading (0-100) con desglose por dimensión."""
    total: float = 0.0
    rentabilidad: float = 0.0
    consistencia: float = 0.0
    riesgo: float = 0.0
    eficiencia: float = 0.0
    label: str = ""
    summary: str = ""
    grade: str = ""


class WalletAnalysis(BaseModel):
    wallet_address: str
    analyzed_at: datetime
    balance_sol: float
    metrics: TradingMetrics
    tokens: List[TokenStats]
    recommendations: List[Recommendation]
    patterns: List[PatternAnalysis]
    transactions: List[Transaction]
    profile: Optional[WalletProfile] = None
    time_of_day: Optional[TimeOfDayInsight] = None
    score: Optional[TradingScore] = None


class PortfolioResponse(BaseModel):
    wallet_address: str
    sol_balance: float
    total_tokens: int
    tokens: List[TokenBalance]
    total_value_usd: Optional[float] = None


class WalletRequest(BaseModel):
    wallet_address: str = Field(..., description="Solana wallet address (base58)")


class AnalysisRequest(BaseModel):
    wallet_address: str
    limit: Optional[int] = Field(100, description="Number of transactions to analyze")
    include_nfts: bool = Field(False, description="Include NFT transactions")


# ============================================================
# TRADING SCHEMAS
# ============================================================

class TradeRequest(BaseModel):
    """Request para ejecutar un trade"""
    private_key: str = Field(..., description="Private key de la wallet (base58)")
    input_token: str = Field(..., description="Mint address del token a vender")
    output_token: str = Field(..., description="Mint address del token a comprar")
    amount: float = Field(..., gt=0, description="Cantidad a vender")
    slippage_percent: float = Field(1.0, ge=0.1, le=10, description="Slippage máximo en porcentaje")


class QuoteRequest(BaseModel):
    """Request para obtener un quote de trade"""
    input_token: str = Field(..., description="Mint address del token a vender")
    output_token: str = Field(..., description="Mint address del token a comprar")
    amount: float = Field(..., gt=0, description="Cantidad a vender")


class TradeResponse(BaseModel):
    """Respuesta de un trade ejecutado"""
    trade_id: str
    type: str
    input_token: str
    output_token: str
    input_amount: float
    expected_output: float
    status: str
    signature: Optional[str] = None
    error: Optional[str] = None
    timestamp: Optional[str] = None


class StrategyRequest(BaseModel):
    """Request para crear una estrategia de trading"""
    name: str
    strategy_type: str  # dca, signal, grid, scalp
    wallet_address: str
    token_pair: str  # e.g., "SOL/BONK"
    config: dict = {}


class StrategyResponse(BaseModel):
    """Respuesta de estrategia creada"""
    id: str
    name: str
    type: str
    wallet_address: str
    token_pair: str
    is_active: bool
    config: dict
